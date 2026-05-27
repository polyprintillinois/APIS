import sys
import os
import time
import logging
import ctypes
import json
import cv2
import re
import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QComboBox, QGroupBox, QSpinBox, QCheckBox,
    QDoubleSpinBox, QSlider, QLineEdit, QProgressBar, QTextEdit, 
    QFileDialog, QMessageBox, QFrame, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer
from PyQt6.QtGui import QImage, QPixmap, QColor, QPalette, QIcon

# Add project root to path
sys.path.append(".") 

from apis.controller import PicsController
from apis.sequence import PicsSequence
from apis import config, utils, io
from app.workers import (
    CameraThread,
    SequenceThread,
    PolarizerCalibrationThread,
    DummyCamera,
    XimeaCamera,
)

# --- UI STATES ---
STATE_DISCONNECTED = "DISCONNECTED"
STATE_LATCHED = config.STATE_LATCHED # "LATCHED"
STATE_ARMED = config.STATE_ARMED     # "ARMED"
STATE_RUNNING = "RUNNING"
STATE_ERROR = "ERROR"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("APIS (Automated Polarization Imaging System)")
        icon_candidates = [
            os.path.join(os.path.dirname(__file__), "assets", "apis_logo.png"),
            os.path.join(os.path.dirname(__file__), "..", "assets", "icon.ico"),
            os.path.join(os.path.dirname(__file__), "assets", "icon.ico"),
        ]
        for icon_path in icon_candidates:
            if os.path.isfile(icon_path):
                self.setWindowIcon(QIcon(icon_path))
                break
        self.resize(1400, 850)
        
        # 1. Init Base State & UI (Required for Logging)
        self.current_state = STATE_DISCONNECTED
        self._syncing_polarizer_angles = False
        self.init_ui()
        
        # --- Logic Objects ---
        self.ctrl = PicsController()
        
        # Camera Initialization strategy
        # 1. Try XimeaCamera
        # 2. Fallback to DummyCamera if SDK missing
        self.cam = XimeaCamera()
        if not self.cam.check_available():
            logging.warning("Ximea SDK missing. Using Dummy Camera.")
            self.cam = DummyCamera()
            self.log("Initialized: DummyCamera (Simulation)", "WARN")
        else:
            self.log("Initialized: XimeaCamera (Hardware)")
            
        self.sequence_logic = PicsSequence(self.ctrl, self.cam)
        self._load_polarizer_baseline()
        
        self.cam_thread = None
        self.seq_thread = None
        self.calib_thread = None
        self._logged_frame_info = False
        self._logged_channel_stats = False
        self._force_bgr_swap = False
        self._last_frame_rgb = None
        self._exp_xpl_last = config.XIMEA_DEFAULT_XPL_EXPOSURE_US
        self._exp_ppl_last = config.XIMEA_DEFAULT_PPL_EXPOSURE_US
        
        # Initial UI Update
        self.update_state_ui(STATE_DISCONNECTED)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # 0. LOGGING (Must be first)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        
        # Redirect Logger to UI
        logging.getLogger().setLevel(logging.INFO)

        # 1. TOP: SAFETY BAR
        self.safety_bar = self.create_safety_bar()
        main_layout.addWidget(self.safety_bar)

        # 2. MIDDLE: Split 3 Columns (Left: Devices, Center: Live, Right: Sequence)
        content_layout = QHBoxLayout()
        
        # Left Panel (Devices & Manual)
        left_panel = QVBoxLayout()
        left_panel.addWidget(self.create_connection_group())
        left_panel.addWidget(self.create_camera_settings_group())
        left_panel.addWidget(self.create_manual_control_group())
        left_panel.addStretch()
        content_layout.addLayout(left_panel, 1)

        # Center Panel (Live View)
        center_panel = QVBoxLayout()
        center_panel.addWidget(self.create_live_view_group())
        content_layout.addLayout(center_panel, 2)

        # Right Panel (Sequence)
        right_panel = QVBoxLayout()
        right_panel.addWidget(self.create_sequence_group())
        right_panel.addWidget(self.create_image_conversion_group())
        right_panel.addStretch()
        content_layout.addLayout(right_panel, 1)

        main_layout.addLayout(content_layout)

        # 3. BOTTOM: LOG
        log_layout = QHBoxLayout()
        self.btn_log_copy = QPushButton("Copy Log")
        self.btn_log_copy.clicked.connect(self.on_log_copy)
        self.btn_log_save = QPushButton("Save Log")
        self.btn_log_save.clicked.connect(self.on_log_save)
        log_layout.addStretch()
        log_layout.addWidget(self.btn_log_copy)
        log_layout.addWidget(self.btn_log_save)

        main_layout.addWidget(self.log_text)
        main_layout.addLayout(log_layout)
        
        # Redirect Logger to UI
        logging.getLogger().setLevel(logging.INFO)
        # (A fuller impl would attach a custom Handler, 
        #  here we just wrap log calls or rely on console for now + my log helper)
        
    def log(self, msg, level="INFO"):
        """Append log message to text area"""
        ts = time.strftime("%H:%M:%S")
        state = self.current_state
        formatted = f"[{ts}][{state}][{level}] {msg}"
        self.log_text.append(formatted)
        # Auto scroll
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())
        print(formatted) # console backup

    # --- UI CREATION HELPERS ---
    
    def create_safety_bar(self):
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet("background-color: #eee; border: 1px solid #ccc;")
        layout = QHBoxLayout(frame)
        
        self.lbl_status = QLabel("Controller: DISCONNECTED")
        self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: gray;")
        
        self.lbl_cam_status = QLabel(" | Camera: DISCONNECTED")
        self.lbl_cam_status.setStyleSheet("font-size: 16px; font-weight: bold; color: gray;")

        self.lbl_info = QLabel(" | Pol: -- | Samp: -- | Exp: -- us | Gain: -- dB")
        self.lbl_info.setStyleSheet("font-size: 14px; color: #333;")
        
        self.btn_estop = QPushButton("E-STOP")
        self.btn_estop.setStyleSheet("background-color: red; color: white; font-weight: bold; font-size: 16px; padding: 10px;")
        self.btn_estop.clicked.connect(self.on_estop)
        
        self.btn_reset = QPushButton("RESET / ARM")
        self.btn_reset.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; font-size: 16px; padding: 10px;")
        self.btn_reset.clicked.connect(self.on_reset)
        
        self.btn_home = QPushButton("HOME")
        self.btn_home.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; font-size: 16px; padding: 10px;")
        self.btn_home.clicked.connect(self.on_home)
        
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.lbl_cam_status)
        layout.addWidget(self.lbl_info)
        layout.addStretch()
        layout.addWidget(self.btn_home)
        layout.addWidget(self.btn_reset)
        layout.addWidget(self.btn_estop)
        return frame

    def create_connection_group(self):
        grp = QGroupBox("Device Connections")
        layout = QGridLayout()
        
        # --- Arduino Section ---
        layout.addWidget(QLabel("<b>Arduino Controller</b>"), 0, 0, 1, 3)
        
        layout.addWidget(QLabel("Port:"), 1, 0)
        self.cmb_port = QComboBox()
        self.on_refresh_ports() # Populate initially
        layout.addWidget(self.cmb_port, 1, 1)
        
        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setFixedWidth(30)
        self.btn_refresh.setToolTip("Refresh COM Ports")
        self.btn_refresh.clicked.connect(self.on_refresh_ports)
        layout.addWidget(self.btn_refresh, 1, 2)
        
        self.btn_connect = QPushButton("Connect Controller")
        self.btn_connect.clicked.connect(self.on_toggle_connect)
        layout.addWidget(self.btn_connect, 2, 0, 1, 3)
        
        # --- Camera Section ---
        layout.addWidget(QLabel("<b>Camera</b>"), 3, 0, 1, 3)
        
        self.btn_cam_connect = QPushButton("Connect Camera")
        self.btn_cam_connect.clicked.connect(self.on_toggle_camera)
        layout.addWidget(self.btn_cam_connect, 4, 0, 1, 3)
        
        grp.setLayout(layout)
        return grp

    def create_camera_settings_group(self):
        grp = QGroupBox("Camera Settings")
        layout = QGridLayout()
        
        layout.addWidget(QLabel("Exposure (us):"), 0, 0)
        self.spin_exp = QSpinBox()
        self.spin_exp.setRange(100, 1000000)
        self.spin_exp.setValue(config.XIMEA_DEFAULT_NORMAL_EXPOSURE_US)
        self.spin_exp.setSingleStep(1000)
        layout.addWidget(self.spin_exp, 0, 1)
        
        layout.addWidget(QLabel("Gain (dB):"), 1, 0)
        self.spin_gain = QDoubleSpinBox()
        self.spin_gain.setRange(0.0, 24.0)
        self.spin_gain.setValue(0.0)
        layout.addWidget(self.spin_gain, 1, 1)
        
        self.btn_cam_apply = QPushButton("Apply Settings")
        self.btn_cam_apply.clicked.connect(self.on_cam_apply)
        layout.addWidget(self.btn_cam_apply, 2, 0, 1, 2)

        self.spin_exp.valueChanged.connect(self.update_status_info)
        self.spin_gain.valueChanged.connect(self.update_status_info)
        
        lbl_rec = QLabel("Rec: XPL=500,000us (500ms), PPL=18,000us (18ms)")
        lbl_rec.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(lbl_rec, 3, 0, 1, 2)
        
        grp.setLayout(layout)
        self.grp_camera = grp
        return grp

    def create_manual_control_group(self):
        grp = QGroupBox("Manual Motor Control (Absolute Movement)")
        layout = QGridLayout()
        
        # Polarizer
        layout.addWidget(QLabel("Polarizer (Pin 10):"), 0, 0, 1, 2)
        self.spin_pol = QSpinBox()
        self.spin_pol.setRange(config.POLARIZER_STAGE_MIN_ANGLE, config.POLARIZER_STAGE_MAX_ANGLE)
        self.btn_move_pol = QPushButton("Move")
        self.btn_move_pol.clicked.connect(lambda: self.on_manual_move(10))
        self.btn_pol_p45 = QPushButton("+45")
        self.btn_pol_p45.clicked.connect(lambda: self.on_add_angle(self.spin_pol, 45))
        
        layout.addWidget(self.spin_pol, 1, 0)
        layout.addWidget(self.btn_move_pol, 1, 1)
        layout.addWidget(self.btn_pol_p45, 1, 2)
        
        # Sample
        layout.addWidget(QLabel("Sample (Pin 11):"), 2, 0, 1, 2)
        self.spin_samp = QSpinBox()
        self.spin_samp.setRange(config.SAMPLE_STAGE_MIN_ANGLE, config.SAMPLE_STAGE_MAX_ANGLE)
        self.btn_move_samp = QPushButton("Move")
        self.btn_move_samp.clicked.connect(lambda: self.on_manual_move(11))
        self.btn_samp_p45 = QPushButton("+45")
        self.btn_samp_p45.clicked.connect(lambda: self.on_add_angle(self.spin_samp, 45))
        
        layout.addWidget(self.spin_samp, 3, 0)
        layout.addWidget(self.btn_move_samp, 3, 1)
        layout.addWidget(self.btn_samp_p45, 3, 2)
        
        grp.setLayout(layout)
        return grp

    def create_live_view_group(self):
        grp = QGroupBox("Live View")
        layout = QVBoxLayout()
        
        self.lbl_live = QLabel("No Signal")
        self.lbl_live.setMinimumSize(640, 480)
        self.lbl_live.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_live.setStyleSheet("background-color: black; color: white;")
        layout.addWidget(self.lbl_live)
        
        self.lbl_live_overlay = QLabel("LIVE PAUSED (Capture Running)", self.lbl_live)
        self.lbl_live_overlay.setStyleSheet("color: yellow; font-size: 24px; font-weight: bold; background: rgba(0,0,0,150);")
        self.lbl_live_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_live_overlay.setGeometry(0, 200, 640, 80)
        self.lbl_live_overlay.hide()
        
        controls = QHBoxLayout()

        controls.addWidget(QLabel("Filename:"))
        self.edt_snapshot_name = QLineEdit("snapshot")
        self.edt_snapshot_name.setFixedWidth(160)
        controls.addWidget(self.edt_snapshot_name)

        controls.addWidget(QLabel("Save Dir:"))
        self.edt_snapshot_dir = QLineEdit(os.path.join(os.getcwd(), "data"))
        controls.addWidget(self.edt_snapshot_dir)
        self.btn_snapshot_browse = QPushButton("...")
        self.btn_snapshot_browse.setFixedWidth(28)
        self.btn_snapshot_browse.clicked.connect(self.on_snapshot_browse)
        controls.addWidget(self.btn_snapshot_browse)

        controls.addStretch()

        self.btn_snap = QPushButton("Snapshot")
        self.btn_snap.setFixedWidth(110)
        self.btn_snap.clicked.connect(self.on_snapshot)
        controls.addWidget(self.btn_snap)

        layout.addLayout(controls)  # Attach controls to main layout
        
        grp.setLayout(layout)  # CRITICAL: Set layout on group!
        grp.setEnabled(False) # Disabled until camera connects
        self.grp_live = grp
        return grp

    def create_sequence_group(self):
        grp = QGroupBox("Sequence Control")
        layout = QGridLayout()
        
        layout.addWidget(QLabel("Save Directory:"), 0, 0)
        self.edt_save_dir = QLineEdit(os.path.join(os.getcwd(), "data"))
        layout.addWidget(self.edt_save_dir, 0, 1)
        self.btn_browse = QPushButton("...")
        self.btn_browse.clicked.connect(self.on_browse)
        layout.addWidget(self.btn_browse, 0, 2)
        
        layout.addWidget(QLabel("Sample ID:"), 1, 0)
        self.edt_sample_id = QLineEdit("TestSample")
        layout.addWidget(self.edt_sample_id, 1, 1, 1, 2)
        
        layout.addWidget(QLabel("Modes:"), 2, 0)
        modes_box = QHBoxLayout()
        self.chk_xpl = QCheckBox("XPL")
        self.chk_xpl.setChecked(True)
        self.chk_ppl = QCheckBox("PPL")
        self.chk_ppl.setChecked(True)
        self.chk_xpl.toggled.connect(self.on_seq_mode_toggle)
        self.chk_ppl.toggled.connect(self.on_seq_mode_toggle)
        modes_box.addWidget(self.chk_xpl)
        modes_box.addWidget(self.chk_ppl)
        modes_widget = QWidget()
        modes_widget.setLayout(modes_box)
        layout.addWidget(modes_widget, 2, 1, 1, 2)
        
        layout.addWidget(QLabel("XPL Exposure (us):"), 3, 0)
        self.spin_exp_xpl = QSpinBox()
        self.spin_exp_xpl.setRange(100, 1000000)
        self.spin_exp_xpl.setValue(config.XIMEA_DEFAULT_XPL_EXPOSURE_US)
        self.spin_exp_xpl.setSingleStep(1000)
        layout.addWidget(self.spin_exp_xpl, 3, 1, 1, 2)
        
        layout.addWidget(QLabel("PPL Exposure (us):"), 4, 0)
        self.spin_exp_ppl = QSpinBox()
        self.spin_exp_ppl.setRange(100, 1000000)
        self.spin_exp_ppl.setValue(config.XIMEA_DEFAULT_PPL_EXPOSURE_US)
        self.spin_exp_ppl.setSingleStep(1000)
        layout.addWidget(self.spin_exp_ppl, 4, 1, 1, 2)

        layout.addWidget(QLabel("XPL Polarizer Angle (deg):"), 5, 0)
        self.spin_xpl_angle = QSpinBox()
        self.spin_xpl_angle.setRange(config.POLARIZER_STAGE_MIN_ANGLE, config.POLARIZER_STAGE_MAX_ANGLE)
        self.spin_xpl_angle.setValue(config.POLARIZER_XPL_ANGLE_DEG)
        self.spin_xpl_angle.valueChanged.connect(self.on_xpl_angle_changed)
        layout.addWidget(self.spin_xpl_angle, 5, 1, 1, 2)

        layout.addWidget(QLabel("PPL Polarizer Angle (deg):"), 6, 0)
        self.spin_ppl_angle = QSpinBox()
        self.spin_ppl_angle.setRange(config.POLARIZER_STAGE_MIN_ANGLE, config.POLARIZER_STAGE_MAX_ANGLE)
        self.spin_ppl_angle.setValue(config.POLARIZER_PPL_ANGLE_DEG)
        self.spin_ppl_angle.setReadOnly(True)
        self.spin_ppl_angle.setToolTip("Derived automatically from XPL as a reachable orthogonal angle (XPL +/- 90 deg).")
        layout.addWidget(self.spin_ppl_angle, 6, 1, 1, 2)
        
        layout.addWidget(QLabel("Sample Angles (stage deg):"), 7, 0)
        self.edt_angles = QLineEdit("90,60,45,30,0")
        self.edt_angles.setToolTip(
            f"List or range. Examples: 0,30,60 or 0:{config.SAMPLE_STAGE_MAX_ANGLE}:15"
        )
        layout.addWidget(self.edt_angles, 7, 1, 1, 2)
        
        layout.addWidget(QLabel("Settling Time (s, motor settle):"), 8, 0)
        self.spin_settling = QDoubleSpinBox()
        self.spin_settling.setRange(0.1, 10.0)
        self.spin_settling.setValue(config.SETTLING_TIME_S)
        layout.addWidget(self.spin_settling, 8, 1)

        layout.addWidget(QLabel("Calibration Scan (pol deg):"), 9, 0)
        self.edt_cal_angles = QLineEdit(f"0:{config.POLARIZER_STAGE_MAX_ANGLE}:5")
        self.edt_cal_angles.setToolTip(
            f"Coarse polarizer scan range. Examples: 0:{config.POLARIZER_STAGE_MAX_ANGLE}:5 or 10,15,20"
        )
        layout.addWidget(self.edt_cal_angles, 9, 1, 1, 2)

        layout.addWidget(QLabel("Calibration Exposure (us):"), 10, 0)
        self.spin_cal_exp = QSpinBox()
        self.spin_cal_exp.setRange(100, 1000000)
        self.spin_cal_exp.setValue(config.XIMEA_DEFAULT_POLARIZER_CALIBRATION_EXPOSURE_US)
        self.spin_cal_exp.setSingleStep(1000)
        layout.addWidget(self.spin_cal_exp, 10, 1, 1, 2)

        layout.addWidget(QLabel("Calibration Sample Angle:"), 11, 0)
        self.spin_cal_sample_angle = QSpinBox()
        self.spin_cal_sample_angle.setRange(config.SAMPLE_STAGE_MIN_ANGLE, config.SAMPLE_STAGE_MAX_ANGLE)
        self.spin_cal_sample_angle.setValue(0)
        layout.addWidget(self.spin_cal_sample_angle, 11, 1, 1, 2)

        self.btn_calibrate_polarizer = QPushButton("RUN POLARIZER CALIBRATION")
        self.btn_calibrate_polarizer.setStyleSheet("background-color: #607D8B; color: white; font-weight: bold; padding: 8px;")
        self.btn_calibrate_polarizer.clicked.connect(self.on_start_polarizer_calibration)
        layout.addWidget(self.btn_calibrate_polarizer, 12, 0, 1, 3)

        self.lbl_calibration_result = QLabel("Baseline: loading...")
        self.lbl_calibration_result.setWordWrap(True)
        layout.addWidget(self.lbl_calibration_result, 13, 0, 1, 3)

        self.btn_start = QPushButton("START SEQUENCE")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
        self.btn_start.clicked.connect(self.on_start_sequence)
        layout.addWidget(self.btn_start, 14, 0, 1, 3)
        
        self.progress = QProgressBar()
        layout.addWidget(self.progress, 15, 0, 1, 3)
        
        self.lbl_seq_status = QLabel("Idle")
        layout.addWidget(self.lbl_seq_status, 16, 0, 1, 3)
        
        grp.setLayout(layout)
        return grp

    def create_image_conversion_group(self):
        grp = QGroupBox("Image Conversion")
        layout = QGridLayout()

        layout.addWidget(QLabel("Source Directory:"), 0, 0)
        self.edt_convert_dir = QLineEdit(os.path.join(os.getcwd(), "data"))
        layout.addWidget(self.edt_convert_dir, 0, 1)
        self.btn_convert_browse = QPushButton("...")
        self.btn_convert_browse.clicked.connect(self.on_conversion_browse)
        layout.addWidget(self.btn_convert_browse, 0, 2)

        lbl_hint = QLabel("Converts RAW16 TIFFs in this folder and its direct child folders to RGB8 previews.")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(lbl_hint, 1, 0, 1, 3)

        self.btn_convert_rgb = QPushButton("Convert RAW16 to RGB")
        self.btn_convert_rgb.clicked.connect(self.on_convert_raw16_to_rgb)
        layout.addWidget(self.btn_convert_rgb, 2, 0, 1, 3)

        grp.setLayout(layout)
        self.grp_conversion = grp
        return grp

    # --- LOGIC SLOTS ---

    def on_refresh_ports(self):
        self.cmb_port.clear()
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
        def port_key(name):
            m = re.search(r"COM(\d+)", name, re.IGNORECASE)
            return (int(m.group(1)) if m else 10**9, name)
        for dev in sorted(ports, key=port_key):
            self.cmb_port.addItem(dev)
        self.log("COM ports refreshed.")
    
    def on_toggle_connect(self):
        """Arduino Connection Toggle"""
        if self.ctrl.is_connected:
            # Disconnect
            self.ctrl.disconnect()
            self.update_state_ui(STATE_DISCONNECTED)
            self.btn_connect.setText("Connect Controller")
            self.log("Disconnected from Arduino.")
        else:
            # Connect
            port = self.cmb_port.currentText()
            if not port:
                QMessageBox.warning(self, "No Port", "Please select a COM port.")
                return
            try:
                self.ctrl.connect(port)
                # Successful connect logic
                init_state = self.ctrl.get_state() # Should be LATCHED or ARMED (if we reset)
                self.update_state_ui(init_state)
                self.btn_connect.setText("Disconnect Controller")
                self.log(f"Connected to {port}. State: {init_state}")
            except Exception as e:
                QMessageBox.critical(self, "Connection Failed", str(e))
                self.log(f"Connection Error: {e}", "ERROR")

    def _is_camera_connected(self):
        return bool(self.cam_thread is not None or getattr(self.cam, "is_open", False))

    def _set_camera_status(self, status, color):
        self.lbl_cam_status.setText(f" | Camera: {status}")
        self.lbl_cam_status.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {color};")

    def _get_polarizer_baseline_path(self):
        return os.path.join(os.getcwd(), "data", "polarizer_baseline.json")

    def _derive_ppl_angle(self, xpl_angle):
        return utils.derive_ppl_angle_from_xpl(
            xpl_angle,
            config.POLARIZER_STAGE_MIN_ANGLE,
            config.POLARIZER_STAGE_MAX_ANGLE,
        )

    def _apply_polarizer_baseline(self, xpl_angle, *, source, persist):
        ppl_angle, ppl_offset = utils.choose_orthogonal_polarizer_angle(
            xpl_angle,
            config.POLARIZER_STAGE_MIN_ANGLE,
            config.POLARIZER_STAGE_MAX_ANGLE,
        )
        config.POLARIZER_XPL_ANGLE_DEG = int(xpl_angle)
        config.POLARIZER_PPL_ANGLE_DEG = int(ppl_angle)

        self._syncing_polarizer_angles = True
        try:
            if hasattr(self, "spin_xpl_angle"):
                self.spin_xpl_angle.blockSignals(True)
                self.spin_xpl_angle.setValue(int(xpl_angle))
                self.spin_xpl_angle.blockSignals(False)
            if hasattr(self, "spin_ppl_angle"):
                self.spin_ppl_angle.blockSignals(True)
                self.spin_ppl_angle.setValue(int(ppl_angle))
                self.spin_ppl_angle.blockSignals(False)
            if hasattr(self, "lbl_calibration_result"):
                self.lbl_calibration_result.setText(
                    f"Baseline: XPL={int(xpl_angle)} deg, PPL={int(ppl_angle)} deg "
                    f"(XPL{int(ppl_offset):+d}, {source})"
                )
        finally:
            self._syncing_polarizer_angles = False

        if persist:
            payload = {
                "saved_at": utils.get_timestamp_iso(),
                "source": source,
                "xpl_angle_deg": int(xpl_angle),
                "ppl_angle_deg": int(ppl_angle),
                "ppl_offset_deg": int(ppl_offset),
            }
            io.save_json(self._get_polarizer_baseline_path(), payload)

    def _load_polarizer_baseline(self):
        baseline_path = self._get_polarizer_baseline_path()
        baseline = None
        if os.path.isfile(baseline_path):
            try:
                with open(baseline_path, "r", encoding="utf-8") as f:
                    baseline = json.load(f)
            except Exception as e:
                self.log(f"Failed to load polarizer baseline: {e}", "WARN")

        if baseline and "xpl_angle_deg" in baseline:
            try:
                self._apply_polarizer_baseline(
                    int(baseline["xpl_angle_deg"]),
                    source="saved baseline",
                    persist=False,
                )
                self.log(
                    f"Loaded polarizer baseline: XPL={config.POLARIZER_XPL_ANGLE_DEG} deg, "
                    f"PPL={config.POLARIZER_PPL_ANGLE_DEG} deg"
                )
                return
            except Exception as e:
                self.log(f"Saved polarizer baseline is invalid: {e}", "WARN")

        self._apply_polarizer_baseline(
            config.POLARIZER_XPL_ANGLE_DEG,
            source="default baseline",
            persist=False,
        )

    def _has_active_capture(self):
        return (
            (self.seq_thread is not None and self.seq_thread.isRunning())
            or (self.calib_thread is not None and self.calib_thread.isRunning())
        )

    def _refresh_camera_ui(self):
        camera_connected = self._is_camera_connected()
        capture_busy = self._has_active_capture()
        camera_controls_enabled = camera_connected and self.current_state != STATE_RUNNING and not capture_busy
        if getattr(self, "grp_camera", None):
            self.grp_camera.setEnabled(camera_controls_enabled)
        if getattr(self, "grp_live", None):
            self.grp_live.setEnabled(camera_controls_enabled)
        if getattr(self, "grp_conversion", None):
            self.grp_conversion.setEnabled(not capture_busy and self.current_state != STATE_RUNNING)
        self.btn_cam_connect.setEnabled(self.current_state != STATE_RUNNING and not capture_busy)
        self.btn_connect.setEnabled(self.current_state != STATE_RUNNING and not capture_busy)

    def _start_camera_thread(self):
        if self.cam_thread:
            return
        if hasattr(self.cam, "configure_live_mode"):
            self.cam.configure_live_mode(self.spin_exp.value(), self.spin_gain.value())
        self.cam_thread = CameraThread(self.cam)
        self.cam_thread.new_frame.connect(self.on_new_frame)
        self.cam_thread.error_occurred.connect(self.on_cam_error)
        self.cam_thread.start()

    def _stop_camera_thread(self):
        had_thread = self.cam_thread is not None
        if self.cam_thread:
            self.cam_thread.stop()
            self.cam_thread = None
        return had_thread

    def _disconnect_camera(self, log_message=True):
        self._stop_camera_thread()
        try:
            if hasattr(self.cam, "close"):
                self.cam.close()
        except Exception as e:
            logging.error(f"Camera close failed: {e}")
        self.btn_cam_connect.setText("Connect Camera")
        self._set_camera_status("DISCONNECTED", "gray")
        self.lbl_live.setText("Camera Disconnected")
        self._last_frame_rgb = None
        self._refresh_camera_ui()
        if log_message:
            self.log("Camera Disconnected.")

    def _set_camera_error_ui(self, message="Camera Error"):
        self._stop_camera_thread()
        try:
            if hasattr(self.cam, "close"):
                self.cam.close()
        except Exception as e:
            logging.error(f"Camera close failed during error cleanup: {e}")
        self.btn_cam_connect.setText("Connect Camera")
        self._set_camera_status("ERROR", "red")
        self.lbl_live.setText(message)
        self._last_frame_rgb = None
        self._refresh_camera_ui()


    def on_toggle_camera(self):
        """Camera Connection Toggle"""
        if self.current_state == STATE_RUNNING:
            return
        if self._is_camera_connected():
            self._disconnect_camera()
            self.update_state_ui(self.current_state)
        else:
            try:
                self._start_camera_thread()
                self.btn_cam_connect.setText("Disconnect Camera")
                self._set_camera_status("CONNECTED", "green")
                self._refresh_camera_ui()
                self.update_state_ui(self.current_state)
                self.log("Camera Connected.")
            except Exception as e:
                QMessageBox.critical(self, "Camera Error", str(e))

    def on_estop(self):
        self.log(">>> EMERGENCY STOP PRESSED <<<", "ERROR")
        
        # 1. Hardware ESTOP
        if self.ctrl.is_connected:
            self.ctrl.emergency_stop()
        
        # 2. Logic ESTOP
        if self.seq_thread and self.seq_thread.isRunning():
            self.seq_thread.abort()
        if self.calib_thread and self.calib_thread.isRunning():
            self.calib_thread.abort()
            
        # 3. UI Latch
        self.update_state_ui(STATE_LATCHED)

    def on_reset(self):
        if not self.ctrl.is_connected:
            return

        # If Running Sequence, Ask User
        if self.current_state == STATE_RUNNING:
            ans = QMessageBox.warning(self, "Abort?", "A capture is running. Abort and Reset?", 
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans == QMessageBox.StandardButton.No:
                return
            # If Yes, Abort logic first
            if self.seq_thread:
                self.seq_thread.abort()
            if self.calib_thread:
                self.calib_thread.abort()
                
        # Send Reset
        if self.ctrl.reset():
            self.log("System RESET / ARMED.")
            self.update_state_ui(STATE_ARMED)
        else:
            self.log("RESET Failed.", "ERROR")

    def on_home(self):
        if self.ctrl.home():
            self.log("CMD: HOME sent.")
            self.spin_pol.setValue(0)
            self.spin_samp.setValue(0)
            self.update_status_info()
        else:
            self.log("HOME Failed (Check if ARMED).", "WARN")

    def on_manual_move(self, pin):
        if self.current_state not in [STATE_ARMED]:
            self.log("Cannot move: System not ARMED.", "WARN")
            return
            
        if pin == 10:
            val = self.spin_pol.value()
            if self.ctrl.rotate_polarizer(val):
                self.log(f"Polarizer -> {val}")
                self.update_status_info()
        elif pin == 11:
            val = self.spin_samp.value()
            if self.ctrl.rotate_sample(val):
                self.log(f"Sample -> {val}")
                self.update_status_info()

    def on_add_angle(self, spinbox, deg):
        val = spinbox.value() + deg
        if val > spinbox.maximum():
            val = spinbox.maximum()
        spinbox.setValue(val)

    def on_start_sequence(self):
        # Validation
        if self.current_state != STATE_ARMED:
             QMessageBox.warning(self, "Not Armed", "System must be ARMED to start sequence.")
             return
        if not self._is_camera_connected():
            QMessageBox.warning(self, "Camera Not Connected", "Connect the camera before starting a sequence.")
            return
        
        do_xpl = self.chk_xpl.isChecked()
        do_ppl = self.chk_ppl.isChecked()
        if not (do_xpl or do_ppl):
            QMessageBox.warning(self, "No Modes", "Enable at least one mode (XPL or PPL).")
            return
             
        save_dir = self.edt_save_dir.text()
        sid = self.edt_sample_id.text()
        settling = self.spin_settling.value()
        exp_xpl = self.spin_exp_xpl.value()
        exp_ppl = self.spin_exp_ppl.value()
        xpl_angle = self.spin_xpl_angle.value()
        try:
            ppl_angle = self._derive_ppl_angle(xpl_angle)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid PPL Angle", str(e))
            return
        angles, angle_err = self.parse_angles(
            self.edt_angles.text(),
            config.SAMPLE_STAGE_MIN_ANGLE,
            config.SAMPLE_STAGE_MAX_ANGLE,
        )
        if not angles:
            msg = (
                "Enter stage angles as comma/space list or range.\n"
                f"Examples: 0,30,60 or 0:{config.SAMPLE_STAGE_MAX_ANGLE}:15"
            )
            if angle_err:
                msg = f"{angle_err}\n\n{msg}"
            QMessageBox.warning(self, "Invalid Angles", msg)
            return
        
        # Update config
        config.SETTLING_TIME_S = settling

        live_thread_was_running = self._stop_camera_thread()
        
        # UI -> RUNNING
        self.update_state_ui(STATE_RUNNING)
        self.lbl_seq_status.setText("Running...")
        self.log(f"Starting Sequence for {sid}...")
        
        self.lbl_live_overlay.show()

        self.update_status_info()
        
        # Start Thread
        self.seq_thread = SequenceThread(
            self.sequence_logic, 
            save_dir, 
            sid, 
            exp_xpl,
            exp_ppl,
            angles,
            do_xpl,
            do_ppl,
            xpl_angle,
            ppl_angle,
            self.spin_exp.value(),
            self.spin_gain.value(),
            live_thread_was_running,
        )
        self.seq_thread.progress_update.connect(self.on_seq_progress_msg)
        self.seq_thread.progress_val.connect(self.progress.setValue)
        self.seq_thread.finished_ok.connect(self.on_seq_finished)
        self.seq_thread.error_occurred.connect(self.on_seq_error)
        self.seq_thread.start()

    def on_start_polarizer_calibration(self):
        if self.current_state != STATE_ARMED:
            QMessageBox.warning(self, "Not Armed", "System must be ARMED to run calibration.")
            return
        if not self._is_camera_connected():
            QMessageBox.warning(self, "Camera Not Connected", "Connect the camera before running calibration.")
            return

        save_dir = self.edt_save_dir.text()
        sid = self.edt_sample_id.text()
        settling = self.spin_settling.value()
        exposure_us = self.spin_cal_exp.value()
        sample_angle = self.spin_cal_sample_angle.value()
        polarizer_angles, angle_err = self.parse_angles(
            self.edt_cal_angles.text(),
            config.POLARIZER_STAGE_MIN_ANGLE,
            config.POLARIZER_STAGE_MAX_ANGLE,
        )
        if not polarizer_angles:
            msg = (
                "Enter polarizer scan angles as comma/space list or range.\n"
                f"Examples: 0,5,10 or 0:{config.POLARIZER_STAGE_MAX_ANGLE}:5"
            )
            if angle_err:
                msg = f"{angle_err}\n\n{msg}"
            QMessageBox.warning(self, "Invalid Polarizer Angles", msg)
            return

        config.SETTLING_TIME_S = settling
        live_thread_was_running = self._stop_camera_thread()

        self.update_state_ui(STATE_RUNNING)
        self.lbl_seq_status.setText("Running polarizer calibration...")
        self.log(f"Starting polarizer calibration for {sid}...")
        self.lbl_live_overlay.show()
        self.update_status_info()

        self.calib_thread = PolarizerCalibrationThread(
            self.sequence_logic,
            save_dir,
            sid,
            exposure_us,
            polarizer_angles,
            sample_angle,
            self.spin_exp.value(),
            self.spin_gain.value(),
            live_thread_was_running,
        )
        self.calib_thread.progress_update.connect(self.on_seq_progress_msg)
        self.calib_thread.progress_val.connect(self.progress.setValue)
        self.calib_thread.finished_ok.connect(self.on_polarizer_calibration_finished)
        self.calib_thread.error_occurred.connect(self.on_polarizer_calibration_error)
        self.calib_thread.start()

    def on_seq_progress_msg(self, msg):
        self.lbl_seq_status.setText(msg)
        self.log(msg)

    def on_seq_finished(self):
        self.log("Sequence Finished Successfully.")
        self.restore_ui_after_capture()
        
    def on_seq_error(self, err_msg):
        self.log(err_msg, "ERROR")
        QMessageBox.critical(self, "Sequence Error", err_msg)
        self.restore_ui_after_capture()

    def on_polarizer_calibration_finished(self):
        info = getattr(self.sequence_logic, "last_calibration_info", {}) or {}
        xpl_angle = info.get("recommended_xpl_angle_deg")
        if xpl_angle is not None:
            try:
                self._apply_polarizer_baseline(
                    int(xpl_angle),
                    source="calibration baseline",
                    persist=True,
                )
            except ValueError as e:
                self.lbl_calibration_result.setText(f"Baseline: calibration failed to apply ({e})")
                self.log(f"Calibration baseline rejected: {e}", "ERROR")
        else:
            self.lbl_calibration_result.setText("Baseline: calibration completed, no recommendation")
        self.log("Polarizer calibration finished successfully.")
        self.restore_ui_after_capture()

    def on_polarizer_calibration_error(self, err_msg):
        self.log(err_msg, "ERROR")
        QMessageBox.critical(self, "Polarizer Calibration Error", err_msg)
        self.restore_ui_after_capture()
        
    def restore_ui_after_capture(self):
        self.lbl_live_overlay.hide()
        run_info = getattr(self.sequence_logic, "last_capture_info", {}) or getattr(self.sequence_logic, "last_run_info", {}) or {}
        cleanup_error = run_info.get("cleanup_error")
        should_restart_live = run_info.get("should_restart_live", False)

        if cleanup_error:
            self.log(f"Capture cleanup failed: {cleanup_error}", "ERROR")
            self.update_state_ui(STATE_ERROR)
            self._set_camera_error_ui("Camera Error")
            self.lbl_seq_status.setText("Error")
            self.seq_thread = None
            self.calib_thread = None
            return

        if should_restart_live:
            try:
                self._start_camera_thread()
                self.btn_cam_connect.setText("Disconnect Camera")
                self._set_camera_status("CONNECTED", "green")
            except Exception as e:
                self.log(f"Failed to restart live view: {e}", "ERROR")
                self.update_state_ui(STATE_ERROR)
                self._set_camera_error_ui("Camera Error")
                self.lbl_seq_status.setText("Error")
                self.seq_thread = None
                self.calib_thread = None
                return
        elif not self._is_camera_connected():
            self.btn_cam_connect.setText("Connect Camera")
            self._set_camera_status("DISCONNECTED", "gray")

        if self.ctrl.is_connected:
            self.update_state_ui(self.ctrl.get_state())
        else:
            self.update_state_ui(STATE_DISCONNECTED)
        self._refresh_camera_ui()
        self.lbl_seq_status.setText("Idle")
        self.seq_thread = None
        self.calib_thread = None

    def restore_ui_after_sequence(self):
        self.restore_ui_after_capture()

    def on_xpl_angle_changed(self, value):
        if self._syncing_polarizer_angles:
            return
        try:
            self._apply_polarizer_baseline(int(value), source="current session", persist=False)
        except ValueError as e:
            self.log(f"XPL angle update rejected: {e}", "WARN")

    def on_cam_apply(self):
        if self._is_camera_connected() and self.current_state != STATE_RUNNING:
            exp = self.spin_exp.value()
            gain = self.spin_gain.value()
            if hasattr(self.cam, "configure_live_mode"):
                self.cam.configure_live_mode(exp, gain)
            elif self.cam_thread:
                self.cam_thread.set_exposure(exp)
                self.cam_thread.set_gain(gain)
            self.log(f"Camera Params Applied: {exp}us, {gain}dB")
            self.update_status_info()

    def on_snapshot(self):
        if self._last_frame_rgb is not None:
            base = self.edt_snapshot_name.text().strip() or "snapshot"
            fname = f"{base}_{int(time.time())}.tif"
            save_dir = self.edt_snapshot_dir.text()
            path = os.path.join(save_dir, fname)
            io.save_image(path, self._last_frame_rgb, color_mode="rgb")
            io.append_to_log(os.path.join(save_dir, "snapshot_log.csv"), {
                "timestamp": utils.get_timestamp_iso(),
                "mode": "snapshot",
                "exposure_us": self.spin_exp.value(),
                "gain": self.spin_gain.value(),
                "filepath": path,
            })
            self.log(f"Snapshot saved: {path}")
            return

        if self.cam_thread:
            img = self.cam_thread.capture_frame()
            if img is not None:
                if img.ndim == 2:
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
                elif img.ndim == 3 and img.shape[2] == 4:
                    img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
                elif img.ndim == 3 and img.shape[2] == 3 and self._force_bgr_swap:
                    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                base = self.edt_snapshot_name.text().strip() or "snapshot"
                fname = f"{base}_{int(time.time())}.tif"
                save_dir = self.edt_snapshot_dir.text()
                path = os.path.join(save_dir, fname)
                io.save_image(path, img, color_mode="rgb")
                io.append_to_log(os.path.join(save_dir, "snapshot_log.csv"), {
                    "timestamp": utils.get_timestamp_iso(),
                    "mode": "snapshot",
                    "exposure_us": self.spin_exp.value(),
                    "gain": self.spin_gain.value(),
                    "filepath": path,
                })
                self.log(f"Snapshot saved: {path}")

    def on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Save Directory")
        if d:
            self.edt_save_dir.setText(d)

    def on_snapshot_browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Snapshot Directory")
        if d:
            self.edt_snapshot_dir.setText(d)

    def on_conversion_browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select RAW16 Source Directory")
        if d:
            self.edt_convert_dir.setText(d)

    def on_convert_raw16_to_rgb(self):
        source_dir = self.edt_convert_dir.text().strip()
        if not source_dir:
            QMessageBox.warning(self, "No Directory", "Select a source directory first.")
            return

        try:
            self.log(f"Starting RAW16 -> RGB conversion: {source_dir}")
            summary = io.convert_raw16_tree_to_rgb_preview(source_dir)
        except Exception as e:
            self.log(f"RAW16 -> RGB conversion failed: {e}", "ERROR")
            QMessageBox.critical(self, "Conversion Error", str(e))
            return

        converted = len(summary["converted"])
        skipped = len(summary["skipped"])
        errors = len(summary["errors"])
        output_dir = summary["output_dir"]

        self.log(
            f"RAW16 -> RGB conversion complete. Converted={converted}, Skipped={skipped}, Errors={errors}, Output={output_dir}"
        )

        if converted == 0:
            QMessageBox.warning(
                self,
                "No RAW16 Files Converted",
                "No eligible RAW16 TIFF files were found in the selected folder or its direct child folders.",
            )
            return

        QMessageBox.information(
            self,
            "Conversion Complete",
            f"Converted {converted} file(s).\nSkipped: {skipped}\nErrors: {errors}\nOutput: {output_dir}",
        )

    def on_new_frame(self, frame):
        try:
            if not self._logged_frame_info:
                self.log(f"Frame info: shape={getattr(frame, 'shape', None)}, dtype={getattr(frame, 'dtype', None)}")
                self._logged_frame_info = True

            # Normalize to RGB for display
            if frame is None:
                return
            if frame.ndim == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
            elif frame.ndim == 3 and frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
            elif frame.ndim == 3 and frame.shape[2] == 3 and self._force_bgr_swap:
                # Ximea may deliver BGR; swap to RGB if colors look off
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Ensure array is C-contiguous (REQUIRED for QImage)
            frame = np.ascontiguousarray(frame)
            
            if not self._logged_channel_stats and frame.ndim == 3 and frame.shape[2] == 3:
                # Simple diagnostics to see if color channels are actually distinct
                means = frame.reshape(-1, 3).mean(axis=0)
                self.log(f"Frame channel means (RGB): {means[0]:.1f}, {means[1]:.1f}, {means[2]:.1f}")
                self._logged_channel_stats = True
            
            # Cache the display frame (RGB) for snapshot consistency
            self._last_frame_rgb = frame.copy()

            # Use frame for display
            h, w, ch = frame.shape
            bytes_per_line = ch * w
            qt_img = QImage(frame.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
            
            pixmap = QPixmap.fromImage(qt_img).scaled(
                self.lbl_live.size(), Qt.AspectRatioMode.KeepAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
            self.lbl_live.setPixmap(pixmap)
            
        except Exception as e:
            print(f"DEBUG: Display Error: {e}")

    def update_status_info(self):
        pol = self.spin_pol.value() if hasattr(self, "spin_pol") else "--"
        samp = self.spin_samp.value() if hasattr(self, "spin_samp") else "--"
        exp = self.spin_exp.value() if hasattr(self, "spin_exp") else "--"
        gain = self.spin_gain.value() if hasattr(self, "spin_gain") else "--"
        self.lbl_info.setText(f" | Pol: {pol} | Samp: {samp} | Exp: {exp} us | Gain: {gain} dB")

    def on_seq_mode_toggle(self):
        if self.chk_xpl.isChecked():
            self.spin_exp_xpl.setEnabled(True)
            self.spin_exp_xpl.setValue(self._exp_xpl_last)
            self.spin_xpl_angle.setEnabled(True)
        else:
            self._exp_xpl_last = self.spin_exp_xpl.value()
            self.spin_exp_xpl.setEnabled(False)
            self.spin_xpl_angle.setEnabled(False)

        if self.chk_ppl.isChecked():
            self.spin_exp_ppl.setEnabled(True)
            self.spin_exp_ppl.setValue(self._exp_ppl_last)
            self.spin_ppl_angle.setEnabled(True)
        else:
            self._exp_ppl_last = self.spin_exp_ppl.value()
            self.spin_exp_ppl.setEnabled(False)
            self.spin_ppl_angle.setEnabled(False)

    def parse_angles(self, text, min_angle, max_angle):
        raw = text.strip()
        if not raw:
            return [], "Angles are empty."
        parts = [p for p in re.split(r"[,\s]+", raw) if p]
        angles = []
        for p in parts:
            if ":" in p:
                nums = p.split(":")
                if len(nums) not in (2, 3):
                    return [], f"Invalid range token: '{p}'"
                try:
                    start = int(nums[0])
                    end = int(nums[1])
                    step = int(nums[2]) if len(nums) == 3 else 1
                except ValueError:
                    return [], f"Invalid range token: '{p}'"
                if step == 0:
                    return [], f"Step cannot be 0 in '{p}'"
                if (
                    start < min_angle
                    or start > max_angle
                    or end < min_angle
                    or end > max_angle
                ):
                    return [], (
                        f"Range out of bounds ({min_angle}-{max_angle}): '{p}'"
                    )
                if step > 0:
                    rng = range(start, end + 1, step)
                else:
                    rng = range(start, end - 1, step)
                angles.extend(list(rng))
            else:
                try:
                    val = int(p)
                except ValueError:
                    return [], f"Invalid angle token: '{p}'"
                if val < min_angle or val > max_angle:
                    return [], (
                        f"Angle out of bounds ({min_angle}-{max_angle}): '{p}'"
                    )
                angles.append(val)
        if not angles:
            return [], "No valid angles found."
        return angles, ""

    # --- STATE MACHINE UI UPDATE ---
    def update_state_ui(self, state):
        self.current_state = state
        self.lbl_status.setText(f"Controller: {state}")
        
        # Colors
        if state == STATE_DISCONNECTED:
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: gray;")
            enable_manual = False
            enable_start = False
        elif state == STATE_LATCHED:
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: red;")
            enable_manual = False
            enable_start = False
        elif state == STATE_ARMED:
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: green;")
            enable_manual = True
            enable_start = True
        elif state == STATE_RUNNING:
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: blue;")
            enable_manual = False
            enable_start = False
        elif state == STATE_ERROR:
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: darkred;")
            enable_manual = False
            enable_start = False
        else:
            enable_manual = False
            enable_start = False
            
        # Widgets Enable/Disable
        self.btn_estop.setEnabled(True) # Always
        self.btn_reset.setEnabled(state != STATE_DISCONNECTED)
        self.btn_home.setEnabled(state == STATE_ARMED)
        
        self.btn_move_pol.setEnabled(enable_manual)
        self.btn_move_samp.setEnabled(enable_manual)
        self.btn_pol_p45.setEnabled(enable_manual)
        self.btn_samp_p45.setEnabled(enable_manual)
        
        capture_busy = self._has_active_capture()
        self.btn_start.setEnabled(enable_start and self._is_camera_connected() and not capture_busy)
        self.btn_calibrate_polarizer.setEnabled(enable_start and self._is_camera_connected() and not capture_busy)
        self._refresh_camera_ui()

    def on_cam_error(self, err_msg):
        self.log(err_msg, "ERROR")
        QMessageBox.critical(self, "Camera Error", err_msg)
        self.update_state_ui(STATE_ERROR)
        self._set_camera_error_ui("Camera Error")

    def on_log_copy(self):
        QApplication.clipboard().setText(self.log_text.toPlainText())
        self.log("Log copied to clipboard.")

    def on_log_save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Log", "apis_log.txt", "Text Files (*.txt);;All Files (*)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_text.toPlainText())
            self.log(f"Log saved: {path}")

    def closeEvent(self, event):
        """Cleanup on Close"""
        self.log("Closing application...", "WARN")
        
        # Stop Sequence Thread
        if self.seq_thread:
            self.seq_thread.abort()
            self.seq_thread.wait()
        if self.calib_thread:
            self.calib_thread.abort()
            self.calib_thread.wait()
        
        # Stop Camera Thread
        self._stop_camera_thread()
        
        # Close Camera Logic
        if hasattr(self.cam, 'close'):
            self.cam.close()
            
        # Close Serial
        if self.ctrl:
            self.ctrl.disconnect()
            
        event.accept()

if __name__ == "__main__":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("APIS.AutomatedPolarizationImagingSystem")
    except Exception:
        pass
    app = QApplication(sys.argv)
    icon_candidates = [
        os.path.join(os.path.dirname(__file__), "assets", "apis_logo.png"),
        os.path.join(os.path.dirname(__file__), "..", "assets", "icon.ico"),
        os.path.join(os.path.dirname(__file__), "assets", "icon.ico"),
    ]
    for icon_path in icon_candidates:
        if os.path.isfile(icon_path):
            app.setWindowIcon(QIcon(icon_path))
            break
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
