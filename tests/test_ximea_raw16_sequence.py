import csv
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from apis import config
from apis import io
from apis import utils
from apis.sequence import PicsSequence
from app.workers import XimeaCamera


class FakeController:
    def __init__(self):
        self.is_connected = True
        self.last_state = "ARMED"
        self.reset_calls = 0
        self.emergency_stop_calls = 0
        self.home_calls = 0
        self.sample_moves = []
        self.polarizer_moves = []

    def reset(self):
        self.reset_calls += 1
        self.last_state = "ARMED"
        return True

    def emergency_stop(self):
        self.emergency_stop_calls += 1
        self.last_state = "LATCHED"
        return True

    def home(self):
        self.home_calls += 1
        return True

    def rotate_polarizer(self, angle):
        self.polarizer_moves.append(angle)
        return True

    def rotate_sample(self, angle):
        self.sample_moves.append(angle)
        return True

    def get_state(self):
        return self.last_state


class FakeSequenceCamera:
    def __init__(self, fail_after=None, fail_live_restore=False):
        self.is_open = True
        self.exposure_us = config.XIMEA_DEFAULT_NORMAL_EXPOSURE_US
        self.gain = 0.0
        self.fail_after = fail_after
        self.fail_live_restore = fail_live_restore
        self.capture_count = 0
        self.live_mode_calls = []
        self.sequence_mode_calls = 0
        self.mode = "live"
        self.exposure_calls = []

    def configure_live_mode(self, exposure_us, gain_db):
        if self.fail_live_restore:
            raise RuntimeError("restore failed")
        self.mode = "live"
        self.exposure_us = exposure_us
        self.gain = gain_db
        self.live_mode_calls.append((exposure_us, gain_db))

    def configure_sequence_mode(self):
        self.mode = "sequence"
        self.gain = 0.0
        self.sequence_mode_calls += 1

    def get_capture_metadata(self):
        return {
            "camera_model": "FakeCam",
            "serial": "FAKE123",
            "camera_backend": "fake",
            "imgdataformat": "XI_RAW16" if self.mode == "sequence" else "XI_RGB24",
            "sensor_bit_depth": 10,
            "image_data_bit_depth": 10 if self.mode == "sequence" else 8,
            "output_bit_depth": 10,
            "cfa_pattern": "XI_CFA_BAYER_GBRG",
            "gammaY": 1.0,
            "gammaC": 1.0,
            "wb_r": 1.4,
            "wb_g": 1.0,
            "wb_b": 1.2,
            "wb_applied": self.mode != "sequence",
            "gain_db": self.gain,
            "resolution": [8, 6],
            "roi": {"x": 0, "y": 0, "w": 8, "h": 6},
        }

    def set_exposure(self, exposure_us):
        self.exposure_us = exposure_us
        self.exposure_calls.append(exposure_us)

    def set_gain(self, gain_db):
        self.gain = gain_db

    def stop_acquisition(self):
        return None

    def capture(self):
        if self.fail_after is not None and self.capture_count >= self.fail_after:
            raise RuntimeError("capture failed")
        self.capture_count += 1
        return (np.arange(48, dtype=np.uint16).reshape(6, 8) * 7) % 1024

    def close(self):
        self.is_open = False


class AngleAwareCalibrationCamera(FakeSequenceCamera):
    def __init__(self):
        super().__init__()
        self.current_polarizer_angle = 0

    def capture(self):
        self.capture_count += 1
        intensity = ((self.current_polarizer_angle - 40) ** 2) + self.current_polarizer_angle + 100
        return np.full((6, 8), intensity, dtype=np.uint16)


class SplitRoiCalibrationCamera(FakeSequenceCamera):
    def __init__(self):
        super().__init__()
        self.current_polarizer_angle = 0

    def capture(self):
        self.capture_count += 1
        img = np.full((6, 8), 900, dtype=np.uint16)
        top_intensity = ((self.current_polarizer_angle - 40) ** 2) + 20
        img[0:2, 1:5] = top_intensity
        img[3:5, 1:5] = 500
        return img


class FakeXiCamera:
    def __init__(self):
        self.open_device_calls = 0
        self.close_device_calls = 0
        self.stop_calls = 0
        self.imgdataformat = "XI_MONO8"
        self.exposure = 0
        self.gain = 0.0
        self.gammaY = 0.47
        self.gammaC = 0.8
        self.wb_kr = 1.4
        self.wb_kg = 1.0
        self.wb_kb = 1.2
        self.auto_wb = False

    def open_device(self):
        self.open_device_calls += 1

    def close_device(self):
        self.close_device_calls += 1

    def stop_acquisition(self):
        self.stop_calls += 1

    def disable_aeag(self):
        return None

    def set_trigger_source(self, value):
        self.trigger_source = value

    def get_device_name(self):
        return b"MQ022CG-CM"

    def get_device_sn(self):
        return b"10870451"

    def set_gammaY(self, value):
        self.gammaY = value

    def set_gammaC(self, value):
        self.gammaC = value

    def get_gammaY(self):
        return self.gammaY

    def get_gammaC(self):
        return self.gammaC

    def is_auto_wb(self):
        return self.auto_wb

    def disable_auto_wb(self):
        self.auto_wb = False

    def enable_auto_wb(self):
        self.auto_wb = True

    def set_wb_kr(self, value):
        self.wb_kr = value

    def set_wb_kg(self, value):
        self.wb_kg = value

    def set_wb_kb(self, value):
        self.wb_kb = value

    def get_wb_kr(self):
        return self.wb_kr

    def get_wb_kg(self):
        return self.wb_kg

    def get_wb_kb(self):
        return self.wb_kb

    def set_imgdataformat(self, value):
        self.imgdataformat = value

    def get_imgdataformat(self):
        return self.imgdataformat

    def set_exposure(self, value):
        self.exposure = value

    def set_gain(self, value):
        self.gain = value

    def get_sensor_bit_depth(self):
        return "XI_BPP_10"

    def get_image_data_bit_depth(self):
        return "XI_BPP_10" if self.imgdataformat == "XI_RAW16" else "XI_BPP_8"

    def get_output_bit_depth(self):
        return "XI_BPP_10"

    def get_cfa(self):
        return "XI_CFA_BAYER_GBRG"

    def get_width(self):
        return 2048

    def get_height(self):
        return 1088

    def get_offsetX(self):
        return 0

    def get_offsetY(self):
        return 0


class SignalStub:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class FakeThread:
    def __init__(self, camera):
        self.camera = camera
        self.new_frame = SignalStub()
        self.error_occurred = SignalStub()
        self.started = False
        self.stopped = False
        self._running = False

    def start(self):
        self.started = True
        self._running = True

    def stop(self):
        self.stopped = True
        self._running = False

    def isRunning(self):
        return self._running


class FakeMainCamera:
    def __init__(self):
        self.is_open = True
        self.configure_calls = []

    def check_available(self):
        return True

    def configure_live_mode(self, exposure_us, gain_db):
        self.is_open = True
        self.configure_calls.append((exposure_us, gain_db))

    def close(self):
        self.is_open = False

    def stop_acquisition(self):
        return None


class CalibrationController(FakeController):
    def __init__(self, camera):
        super().__init__()
        self.camera = camera

    def rotate_polarizer(self, angle):
        self.polarizer_moves.append(angle)
        self.camera.current_polarizer_angle = angle
        return True


class TestXimeaRaw16Sequence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def local_xpl_test_config(
        self,
        coarse_angles=None,
        coarse_trigger_mean=1000.0,
        fine_max_span_deg=2,
    ):
        if coarse_angles is None:
            coarse_angles = [94, 95, 96]
        return patch.multiple(
            config,
            XPL_BACKGROUND_ROI_X=1,
            XPL_BACKGROUND_ROI_TOP_Y=0,
            XPL_BACKGROUND_ROI_BOTTOM_Y=1,
            XPL_BACKGROUND_ROI_WIDTH=4,
            XPL_BACKGROUND_ROI_HEIGHT=3,
            XPL_ADAPTIVE_COARSE_ANGLES_DEG=coarse_angles,
            XPL_LOCAL_COARSE_TRIGGER_MEAN=coarse_trigger_mean,
            XPL_LOCAL_CALIBRATION_RADIUS_DEG=1,
            XPL_LOCAL_CALIBRATION_STEP_DEG=1,
            XPL_LOCAL_FINE_MAX_SPAN_DEG=fine_max_span_deg,
            XPL_LOCAL_FINE_STOP_INCREASE_COUNT=2,
            XPL_LOCAL_FINE_STOP_RATIO=1.2,
            XPL_LOCAL_CALIBRATION_FINAL_APPROACH_OFFSET_DEG=1,
            XPL_LOCAL_CONFIRMATION_MAX_MEAN=1000.0,
            XPL_LOCAL_CONFIRMATION_MAX_RATIO=10.0,
            XPL_LOCAL_CONFIRMATION_REFINE_RADIUS_DEG=1,
            XPL_LOCAL_CONFIRMATION_MAX_REFINES=1,
        )

    def test_sequence_writes_uint16_tiff_and_metadata(self):
        ctrl = FakeController()
        cam = FakeSequenceCamera()
        seq = PicsSequence(ctrl, cam)

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.local_xpl_test_config():
                seq.run_sequence(
                    tmpdir,
                    "sample1",
                    50000,
                    12000,
                    sample_angles=[0, 10],
                    do_xpl=True,
                    do_ppl=False,
                    live_exposure_us=12000,
                    live_gain_db=0.5,
                    live_thread_was_running=True,
                )

            image_path = os.path.join(tmpdir, "sample1", "xpl", "sample1_xpl_000.tif")
            metadata_path = os.path.join(tmpdir, "sample1", "sample1_metadata.json")
            csv_path = os.path.join(tmpdir, "sample1", "sample1_log.csv")

            restored = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.dtype, np.uint16)
            self.assertEqual(restored.shape, (6, 8))

            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            self.assertTrue(metadata["sequence_completed"])
            self.assertEqual(metadata["imgdataformat"], "XI_RAW16")
            self.assertEqual(metadata["gain_db"], 0.0)
            self.assertEqual(len(metadata["images"]), 2)
            self.assertEqual(len(metadata["local_xpl_calibrations"]), 1)
            self.assertEqual(metadata["cleanup_error"], "")
            self.assertEqual(ctrl.home_calls, 1)

            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 7)
            self.assertEqual(rows[-2]["gain"], "0.0")
            self.assertEqual(cam.live_mode_calls[-1], (12000, 0.5))

    def test_sequence_adaptive_xpl_calibration_uses_background_roi_and_home(self):
        cam = AngleAwareCalibrationCamera()
        ctrl = CalibrationController(cam)
        seq = PicsSequence(ctrl, cam)

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.local_xpl_test_config(coarse_angles=[39, 40, 41]):
                seq.run_sequence(
                    tmpdir,
                    "sample_local",
                    50000,
                    12000,
                    sample_angles=[0],
                    do_xpl=True,
                    do_ppl=False,
                    xpl_polarizer_angle=40,
                )

            metadata_path = os.path.join(tmpdir, "sample_local", "sample_local_metadata.json")
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            calibration = metadata["local_xpl_calibrations"][0]
            self.assertEqual(calibration["background_roi"], {"x": 1, "y": 1, "width": 4, "height": 3})
            self.assertEqual(
                calibration["background_rois"],
                {
                    "bottom": {"x": 1, "y": 1, "width": 4, "height": 3},
                    "top": {"x": 1, "y": 0, "width": 4, "height": 3},
                },
            )
            self.assertEqual(calibration["selected_background_roi_name"], "bottom")
            self.assertEqual(calibration["coarse_angles_deg"], [39, 40, 41])
            self.assertEqual(calibration["coarse_trigger_angle_deg"], 39)
            self.assertEqual(calibration["fine_start_angle_deg"], 39)
            self.assertEqual(calibration["fine_angles_deg"], [39, 40, 41])
            self.assertEqual(calibration["selected_xpl_angle_deg"], 39)
            self.assertTrue(calibration["confirmation_ok"])
            self.assertEqual(metadata["mode_angles_deg"]["xpl"], 39)
            self.assertEqual(ctrl.home_calls, 1)
            self.assertEqual(ctrl.sample_moves[0], 0)
            self.assertEqual(ctrl.sample_moves[-1], 0)

    def test_sequence_adaptive_xpl_calibration_can_focus_top_roi(self):
        cam = SplitRoiCalibrationCamera()
        ctrl = CalibrationController(cam)
        seq = PicsSequence(ctrl, cam)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.multiple(
                config,
                XPL_BACKGROUND_ROI_X=1,
                XPL_BACKGROUND_ROI_TOP_Y=0,
                XPL_BACKGROUND_ROI_BOTTOM_Y=3,
                XPL_BACKGROUND_ROI_WIDTH=4,
                XPL_BACKGROUND_ROI_HEIGHT=2,
                XPL_ADAPTIVE_COARSE_ANGLES_DEG=[39, 40, 41],
                XPL_LOCAL_COARSE_TRIGGER_MEAN=80.0,
                XPL_LOCAL_CALIBRATION_RADIUS_DEG=1,
                XPL_LOCAL_CALIBRATION_STEP_DEG=1,
                XPL_LOCAL_FINE_MAX_SPAN_DEG=2,
                XPL_LOCAL_FINE_STOP_INCREASE_COUNT=2,
                XPL_LOCAL_FINE_STOP_RATIO=1.2,
                XPL_LOCAL_CALIBRATION_FINAL_APPROACH_OFFSET_DEG=1,
                XPL_LOCAL_CONFIRMATION_MAX_MEAN=1000.0,
                XPL_LOCAL_CONFIRMATION_MAX_RATIO=10.0,
                XPL_LOCAL_CONFIRMATION_REFINE_RADIUS_DEG=1,
                XPL_LOCAL_CONFIRMATION_MAX_REFINES=0,
            ):
                seq.run_sequence(
                    tmpdir,
                    "sample_top_roi",
                    50000,
                    12000,
                    sample_angles=[0],
                    do_xpl=True,
                    do_ppl=False,
                    xpl_polarizer_angle=40,
                )

            metadata_path = os.path.join(tmpdir, "sample_top_roi", "sample_top_roi_metadata.json")
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            calibration = metadata["local_xpl_calibrations"][0]
            self.assertEqual(calibration["selected_background_roi_name"], "top")
            self.assertEqual(calibration["background_roi"], {"x": 1, "y": 0, "width": 4, "height": 2})
            self.assertEqual(calibration["coarse_trigger_roi_name"], "top")
            self.assertEqual(calibration["selected_xpl_angle_deg"], 40)

    def test_roi_signal_uses_requested_rectangle_and_rejects_out_of_bounds(self):
        seq = PicsSequence(FakeController(), FakeSequenceCamera())
        img = np.full((10, 12), 1000, dtype=np.uint16)
        img[3:5, 2:6] = 12

        self.assertEqual(seq._measure_roi_signal_level(img, (2, 3, 4, 2)), 12.0)
        with self.assertRaises(ValueError):
            seq._measure_roi_signal_level(img, (9, 8, 4, 3))

    def test_polarizer_calibration_recommends_darkest_xpl_and_derived_ppl(self):
        cam = AngleAwareCalibrationCamera()
        ctrl = CalibrationController(cam)
        seq = PicsSequence(ctrl, cam)

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.local_xpl_test_config(
                coarse_angles=[0, 20, 40, 60, 80],
                coarse_trigger_mean=80.0,
                fine_max_span_deg=30,
            ):
                seq.run_polarizer_calibration(
                    tmpdir,
                    "sample_cal",
                    70000,
                    polarizer_angles=[0, 20, 40, 60, 80],
                    sample_angle=0,
                    live_exposure_us=12000,
                    live_gain_db=0.5,
                    live_thread_was_running=True,
                )

            self.assertEqual(seq.last_calibration_info["recommended_xpl_angle_deg"], 39)
            self.assertEqual(seq.last_calibration_info["recommended_ppl_angle_deg"], 129)
            self.assertEqual(seq.last_calibration_info["recommended_ppl_offset_deg"], 90)

            metadata_path = os.path.join(tmpdir, "sample_cal", "sample_cal_polarizer_calibration.json")
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            self.assertTrue(metadata["sequence_completed"])
            self.assertEqual(metadata["recommended_xpl_angle_deg"], 39)
            self.assertEqual(metadata["recommended_ppl_angle_deg"], 129)
            self.assertEqual(metadata["recommended_ppl_offset_deg"], 90)
            self.assertEqual(metadata["adaptive_exposure_us"], 70000)
            self.assertEqual(metadata["coarse_scan_angles_deg"], [0, 20, 40, 60, 80])
            calibration = metadata["local_xpl_calibrations"][0]
            self.assertEqual(calibration["coarse_angles_deg"], [0, 20, 40, 60, 80])
            self.assertEqual(calibration["selected_background_roi_name"], "bottom")
            self.assertEqual(calibration["fine_start_angle_deg"], 39)
            self.assertEqual(calibration["fine_angles_deg"], [39, 40, 41, 42])
            self.assertEqual(len(metadata["scan_results"]), 10)
            self.assertEqual(metadata["scan_results"][0]["exposure_us"], 70000)
            self.assertEqual(metadata["scan_results"][-1]["exposure_us"], 70000)
            self.assertEqual(cam.exposure_calls, [70000])
            self.assertEqual(ctrl.polarizer_moves[:5], [0, 20, 40, 60, 80])
            self.assertEqual(ctrl.polarizer_moves[5:9], [39, 40, 41, 42])
            self.assertEqual(ctrl.polarizer_moves[9:11], [38, 39])

    def test_signal_level_uses_center_roi_when_requested(self):
        seq = PicsSequence(FakeController(), FakeSequenceCamera())
        img = np.full((10, 10), 1000, dtype=np.uint16)
        img[3:7, 3:7] = 10

        self.assertEqual(seq._measure_signal_level(img, roi_size_px=4), 10.0)

    def test_choose_orthogonal_polarizer_angle_uses_negative_90_when_positive_is_out_of_range(self):
        ppl_angle, offset = utils.choose_orthogonal_polarizer_angle(120, 0, 169)

        self.assertEqual(ppl_angle, 30)
        self.assertEqual(offset, -90)

    def test_convert_raw16_tree_to_rgb_preview_processes_root_and_one_child_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root_file = os.path.join(tmpdir, "root_raw.tif")
            child_dir = os.path.join(tmpdir, "xpl")
            deep_dir = os.path.join(child_dir, "deep")
            skip_dir = os.path.join(tmpdir, "existing_rgb")
            os.makedirs(child_dir, exist_ok=True)
            os.makedirs(deep_dir, exist_ok=True)
            os.makedirs(skip_dir, exist_ok=True)

            raw = (np.arange(64, dtype=np.uint16).reshape(8, 8) * 13) % 1024
            cv2.imwrite(root_file, raw)
            cv2.imwrite(os.path.join(child_dir, "child_raw.tif"), raw)
            cv2.imwrite(os.path.join(deep_dir, "too_deep.tif"), raw)
            cv2.imwrite(os.path.join(skip_dir, "skip_me.tif"), raw)
            cv2.imwrite(os.path.join(tmpdir, "already_rgb_rgb.tif"), np.zeros((8, 8, 3), dtype=np.uint8))

            summary = io.convert_raw16_tree_to_rgb_preview(tmpdir)

            self.assertEqual(len(summary["converted"]), 2)
            self.assertGreaterEqual(len(summary["skipped"]), 1)
            output_root = summary["output_dir"]
            root_rgb = os.path.join(output_root, "root_raw_rgb.tif")
            child_rgb = os.path.join(output_root, "xpl", "child_raw_rgb.tif")
            deep_rgb = os.path.join(output_root, "xpl", "deep", "too_deep_rgb.tif")

            self.assertTrue(os.path.exists(root_rgb))
            self.assertTrue(os.path.exists(child_rgb))
            self.assertFalse(os.path.exists(deep_rgb))

            root_img = cv2.imread(root_rgb, cv2.IMREAD_UNCHANGED)
            child_img = cv2.imread(child_rgb, cv2.IMREAD_UNCHANGED)
            self.assertEqual(root_img.dtype, np.uint8)
            self.assertEqual(child_img.dtype, np.uint8)
            self.assertEqual(root_img.shape[2], 3)
            self.assertEqual(child_img.shape[2], 3)

    def test_sequence_partial_failure_saves_partial_metadata_and_restores_live(self):
        ctrl = FakeController()
        cam = FakeSequenceCamera(fail_after=6)
        seq = PicsSequence(ctrl, cam)

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(RuntimeError):
                with self.local_xpl_test_config():
                    seq.run_sequence(
                        tmpdir,
                        "sample2",
                        50000,
                        12000,
                        sample_angles=[0, 10],
                        do_xpl=True,
                        do_ppl=False,
                        live_exposure_us=10000,
                        live_gain_db=0.25,
                        live_thread_was_running=True,
                    )

            metadata_path = os.path.join(tmpdir, "sample2", "sample2_metadata.json")
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            self.assertFalse(metadata["sequence_completed"])
            self.assertIn("capture failed", metadata["error_message"])
            self.assertEqual(len(metadata["images"]), 1)
            self.assertEqual(ctrl.emergency_stop_calls, 1)
            self.assertEqual(cam.live_mode_calls[-1], (10000, 0.25))
            self.assertTrue(seq.last_run_info["cleanup_restore_ok"])

    def test_ximea_camera_open_is_idempotent_and_switches_modes(self):
        fake_pkg = types.ModuleType("ximea")
        fake_pkg.xiapi = types.SimpleNamespace(Camera=FakeXiCamera, Image=object)

        with patch.dict(sys.modules, {"ximea": fake_pkg}):
            cam = XimeaCamera()
            cam.open()
            first_camera = cam._cam
            cam.open()

            self.assertIs(cam._cam, first_camera)
            self.assertEqual(first_camera.open_device_calls, 1)
            self.assertEqual(first_camera.gammaY, 1.0)
            self.assertEqual(first_camera.gammaC, 1.0)

            cam.configure_sequence_mode()
            self.assertEqual(first_camera.imgdataformat, "XI_RAW16")
            self.assertEqual(cam.gain, 0.0)

            metadata = cam.get_capture_metadata()
            self.assertEqual(metadata["camera_model"], "MQ022CG-CM")
            self.assertEqual(metadata["imgdataformat"], "XI_RAW16")
            self.assertEqual(metadata["cfa_pattern"], "XI_CFA_BAYER_GBRG")

            cam.configure_live_mode(12345, 1.5)
            self.assertEqual(first_camera.imgdataformat, "XI_RGB24")
            self.assertEqual(first_camera.exposure, 12345)
            self.assertEqual(first_camera.gain, 1.5)

    def test_main_window_restore_restarts_live_after_success(self):
        from app import main as main_module

        with patch.object(main_module, "XimeaCamera", FakeMainCamera), patch.object(
            main_module, "CameraThread", FakeThread
        ):
            window = main_module.MainWindow()
            window.sequence_logic.last_run_info = {"cleanup_error": "", "should_restart_live": True}
            window.ctrl.is_connected = True
            window.ctrl.get_state = lambda: main_module.STATE_ARMED
            window.restore_ui_after_capture()

            self.assertIsNotNone(window.cam_thread)
            self.assertEqual(window.btn_cam_connect.text(), "Disconnect Camera")
            self.assertIn("CONNECTED", window.lbl_cam_status.text())
            window.close()

    def test_main_window_restore_sets_error_ui_on_cleanup_failure(self):
        from app import main as main_module

        with patch.object(main_module, "XimeaCamera", FakeMainCamera), patch.object(
            main_module, "CameraThread", FakeThread
        ):
            window = main_module.MainWindow()
            window.sequence_logic.last_run_info = {"cleanup_error": "restore failed", "should_restart_live": False}
            window.restore_ui_after_capture()

            self.assertEqual(window.current_state, main_module.STATE_ERROR)
            self.assertEqual(window.btn_cam_connect.text(), "Connect Camera")
            self.assertIn("ERROR", window.lbl_cam_status.text())
            self.assertIsNone(window.cam_thread)
            window.close()


if __name__ == "__main__":
    unittest.main()
