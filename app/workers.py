import sys
import os
import time
import logging
import cv2
from PyQt6.QtCore import QThread, pyqtSignal, QMutex
import numpy as np
from apis import config

# Add project root if needed
sys.path.append(".")

# --- Dummy Camera Simulation (Replace with XIMEA SDK later) ---
class DummyCamera:
    def __init__(self):
        self.exposure_us = config.XIMEA_DEFAULT_NORMAL_EXPOSURE_US
        self.gain = 0.0
        self.width = 640
        self.height = 480
        self._streaming = False
        self.is_open = False
        self._mode = "live"
        self._imgdataformat = "XI_RGB24"
        self._gamma_y = 1.0
        self._gamma_c = 1.0

    def check_available(self):
        return True

    def open(self):
        if self.is_open:
            return
        self.is_open = True
        self._streaming = True
        self.configure_live_mode(self.exposure_us, self.gain)
        logging.info("Dummy Camera Opened.")

    def close(self):
        self.is_open = False
        self._streaming = False
        logging.info("Dummy Camera Closed.")

    def start_acquisition(self):
        self._streaming = True

    def stop_acquisition(self):
        self._streaming = False
        if self.is_open:
            try:
                self._cam.stop_acquisition()
            except Exception:
                pass

    def set_exposure(self, us):
        self.exposure_us = us
        logging.info(f"CAM: Exposure set to {us} us")

    def set_gain(self, gain):
        self.gain = gain
        logging.info(f"CAM: Gain set to {gain}")

    def configure_live_mode(self, exposure_us, gain_db):
        self._mode = "live"
        self._imgdataformat = "XI_RGB24"
        self._streaming = True
        self.set_exposure(exposure_us)
        self.set_gain(gain_db)

    def configure_sequence_mode(self):
        self._mode = "sequence"
        self._imgdataformat = "XI_RAW16"
        self._streaming = True
        self.set_gain(0.0)

    def get_capture_metadata(self):
        return {
            "camera_model": "DummyCamera",
            "serial": "dummy",
            "camera_backend": "dummy",
            "imgdataformat": self._imgdataformat,
            "sensor_bit_depth": 8,
            "image_data_bit_depth": 16 if self._mode == "sequence" else 8,
            "output_bit_depth": 16 if self._mode == "sequence" else 8,
            "cfa_pattern": "NONE",
            "gammaY": self._gamma_y,
            "gammaC": self._gamma_c,
            "wb_r": config.XIMEA_WB_KR,
            "wb_g": config.XIMEA_WB_KG,
            "wb_b": config.XIMEA_WB_KB,
            "wb_applied": self._mode != "sequence",
            "gain_db": self.gain,
            "resolution": [self.width, self.height],
            "roi": {"x": 0, "y": 0, "w": self.width, "h": self.height},
        }
        
    def get_image(self, raise_on_error=False):
        if not self._streaming:
            return None
        dtype = np.uint16 if self._mode == "sequence" else np.uint8
        max_value = 1023 if self._mode == "sequence" else 255
        img = np.zeros((self.height, self.width), dtype=dtype)
        t = int(time.time() * 20) % self.width
        cv2.line(img, (t, 0), (t, self.height), max_value, 3)
        if self.gain > 0:
            noise = np.random.normal(0, self.gain * 5, img.shape)
            img = np.clip(img.astype(np.float32) + noise, 0, max_value).astype(dtype)
        return img
    
    def capture(self):
        """Simulate single capture for sequence"""
        return self.get_image(raise_on_error=True)
    
    def stop_live(self):
        self._streaming = False
        
    def resume_live(self):
        self._streaming = True

# --- Real XIMEA Camera Wrapper ---
class XimeaCamera:
    def __init__(self):
        self._cam = None
        self._streaming = False
        self.exposure_us = config.XIMEA_DEFAULT_NORMAL_EXPOSURE_US
        self.gain = 0.0
        self.is_open = False
        self._mode = "live"
        self._imgdataformat = "XI_RGB24"
        self._gamma_y = 1.0
        self._gamma_c = 1.0
        self._device_info = {}
        
        try:
            from ximea import xiapi
            self.xiapi = xiapi
            logging.info("XIMEA SDK found.")
        except ImportError:
            self._try_add_ximea_path()
            try:
                from ximea import xiapi
                self.xiapi = xiapi
                logging.info("XIMEA SDK found via system path.")
            except ImportError:
                self.xiapi = None
                logging.warning("XIMEA SDK not found. Install 'ximea' package.")

    def _try_add_ximea_path(self):
        # Add default XIMEA Python API path if present and not already on sys.path
        candidates = [
            r"C:\XIMEA\API\Python\v3",
        ]
        for p in candidates:
            if os.path.isdir(p) and p not in sys.path:
                sys.path.append(p)

    def check_available(self):
        return self.xiapi is not None

    def open(self):
        if not self.xiapi:
            raise RuntimeError("XIMEA SDK not installed.")
        if self.is_open and self._cam:
            return
        
        self._cam = self.xiapi.Camera()
        self._cam.open_device() # Open first available
        
        # Log device info
        model_name = self._decode_if_bytes(self._safe_get("get_device_name"))
        serial_num = self._decode_if_bytes(self._safe_get("get_device_sn"))
        self._device_info = {
            "camera_model": model_name,
            "serial": serial_num,
            "camera_backend": "ximea",
        }
        logging.info(f"XIMEA: Model={model_name}, Serial={serial_num}")
        
        # Configure default from Reference Code
        try:
            # Disable Auto Exposure / Gain (Critical)
            self._cam.disable_aeag()

            # Ensure Free Run (Disable Trigger)
            try:
                self._cam.set_trigger_source('XI_TRG_OFF')
            except Exception:
                pass

            self.is_open = True
            self._streaming = True
            self._set_gamma_defaults()
            self.configure_live_mode(self.exposure_us, self.gain)
            logging.info("XIMEA Camera Opened.")
            
        except Exception as e:
            # If config fails, close
            try:
                self._cam.close_device()
            except Exception:
                pass
            self._cam = None
            self.is_open = False
            raise e

    def close(self):
        if self._cam and self.is_open:
            try:
                self._cam.stop_acquisition()
            except Exception:
                pass
            self._cam.close_device()
            self.is_open = False
            self._streaming = False
            self._cam = None
            logging.info("XIMEA Camera Closed.")

    def start_acquisition(self):
        self._streaming = True

    def stop_acquisition(self):
        self._streaming = False

    def set_exposure(self, us):
        self.exposure_us = us
        if self.is_open:
            try:
                self._cam.set_exposure(us)
            except Exception as e:
                logging.error(f"CAM Set Exposure Error: {e}")

    def set_gain(self, gain):
        self.gain = gain
        if self.is_open:
            try:
                self._cam.set_gain(gain)
            except Exception as e:
                logging.error(f"CAM Set Gain Error: {e}")

    def configure_live_mode(self, exposure_us, gain_db):
        self.open()
        self._mode = "live"
        self._imgdataformat = "XI_RGB24"
        self._set_gamma_defaults()
        self._apply_white_balance()
        self._cam.set_imgdataformat(self._imgdataformat)
        self.set_exposure(exposure_us)
        self.set_gain(gain_db)
        logging.info(
            "XIMEA: Live mode configured (fmt=%s, exposure=%sus, gain=%.2fdB, gammaY=%.2f, gammaC=%.2f)",
            self._safe_get("get_imgdataformat", self._imgdataformat),
            self.exposure_us,
            self.gain,
            self._safe_get("get_gammaY", self._gamma_y),
            self._safe_get("get_gammaC", self._gamma_c),
        )

    def configure_sequence_mode(self):
        self.open()
        self._mode = "sequence"
        self._imgdataformat = "XI_RAW16"
        self._set_gamma_defaults()
        self._cam.set_imgdataformat(self._imgdataformat)
        self.set_gain(0.0)
        logging.info(
            "XIMEA: Sequence mode configured (fmt=%s, gain=%.2fdB, gammaY=%.2f, gammaC=%.2f)",
            self._safe_get("get_imgdataformat", self._imgdataformat),
            self.gain,
            self._safe_get("get_gammaY", self._gamma_y),
            self._safe_get("get_gammaC", self._gamma_c),
        )

    def get_capture_metadata(self):
        metadata = dict(self._device_info)
        metadata.update(
            {
                "imgdataformat": self._safe_get("get_imgdataformat", self._imgdataformat),
                "sensor_bit_depth": self._safe_get("get_sensor_bit_depth"),
                "image_data_bit_depth": self._safe_get("get_image_data_bit_depth"),
                "output_bit_depth": self._safe_get("get_output_bit_depth"),
                "cfa_pattern": self._safe_get("get_cfa"),
                "gammaY": self._safe_get("get_gammaY", self._gamma_y),
                "gammaC": self._safe_get("get_gammaC", self._gamma_c),
                "wb_r": self._safe_get("get_wb_kr", config.XIMEA_WB_KR),
                "wb_g": self._safe_get("get_wb_kg", config.XIMEA_WB_KG),
                "wb_b": self._safe_get("get_wb_kb", config.XIMEA_WB_KB),
                "wb_applied": self._mode != "sequence",
                "gain_db": self.gain,
                "resolution": [
                    self._safe_get("get_width"),
                    self._safe_get("get_height"),
                ],
                "roi": {
                    "x": self._safe_get("get_offsetX", 0),
                    "y": self._safe_get("get_offsetY", 0),
                    "w": self._safe_get("get_width"),
                    "h": self._safe_get("get_height"),
                },
            }
        )
        return metadata
        
    def get_image(self, raise_on_error=False):
        # Only check if camera is open - _streaming is irrelevant for per-frame acquisition
        if not self.is_open:
            if raise_on_error:
                raise RuntimeError("XIMEA camera is not open.")
            return None
        
        try:
            # Start-Get-Stop Pattern (from reference code)
            self._cam.start_acquisition()
            
            img = self.xiapi.Image()
            self._cam.get_image(img)  # NO TIMEOUT - reference code style
            invert_rgb = self._imgdataformat.startswith("XI_RGB")
            data = img.get_image_data_numpy(invert_rgb_order=invert_rgb)
            
            self._cam.stop_acquisition()
            return data
        except Exception as e:
            # Try to stop if stuck
            try:
                self._cam.stop_acquisition()
            except Exception:
                pass

            logging.error(f"XIMEA get_image failed: {e}")
            if raise_on_error:
                raise RuntimeError(f"XIMEA get_image failed: {e}") from e
            return None
    
    def capture(self):
        return self.get_image(raise_on_error=True)
    
    def stop_live(self):
        self._streaming = False
        
    def resume_live(self):
        self._streaming = True

    def _apply_white_balance(self):
        try:
            if config.XIMEA_USE_FIXED_WB:
                try:
                    if self._cam.is_auto_wb():
                        self._cam.disable_auto_wb()
                except Exception:
                    self._cam.disable_auto_wb()

                self._cam.set_wb_kr(config.XIMEA_WB_KR)
                self._cam.set_wb_kg(config.XIMEA_WB_KG)
                self._cam.set_wb_kb(config.XIMEA_WB_KB)
                logging.info(
                    "XIMEA: Fixed WB enabled (R=%.2f, G=%.2f, B=%.2f)",
                    config.XIMEA_WB_KR,
                    config.XIMEA_WB_KG,
                    config.XIMEA_WB_KB,
                )
            else:
                if not self._cam.is_auto_wb():
                    self._cam.enable_auto_wb()
                logging.info("XIMEA: Auto WB enabled.")
        except Exception as e:
            logging.warning(f"XIMEA: White-balance configuration failed: {e}")

    def _set_gamma_defaults(self):
        self._gamma_y = 1.0
        self._gamma_c = 1.0
        try:
            self._cam.set_gammaY(self._gamma_y)
            self._cam.set_gammaC(self._gamma_c)
            logging.info(
                "XIMEA: Gamma configured (Y=%.2f, C=%.2f)",
                self._safe_get("get_gammaY", self._gamma_y),
                self._safe_get("get_gammaC", self._gamma_c),
            )
        except Exception as e:
            logging.warning(f"XIMEA: Gamma configuration failed: {e}")

    def _safe_get(self, getter_name, default=None):
        if not self._cam:
            return default
        getter = getattr(self._cam, getter_name, None)
        if getter is None:
            return default
        try:
            value = getter()
        except Exception:
            return default
        return self._decode_if_bytes(value)

    @staticmethod
    def _decode_if_bytes(value):
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

class CameraThread(QThread):
    new_frame = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str) # New signal for errors
    
    def __init__(self, camera_obj=None):
        super().__init__()
        self.cam = camera_obj if camera_obj else DummyCamera()
        self._running = False
        self._paused = False
        self.fps = 30
        self._cam_mutex = QMutex()
        
    def run(self):
        self._running = True
        print("DEBUG: CameraThread Started")
        
        # Ensure camera is open
        try:
             if hasattr(self.cam, 'open'):
                 self.cam.open()
             # NOTE: DO NOT call start_acquisition here!
             # XimeaCamera.get_image() handles start/stop internally.
        except Exception as e:
             logging.error(f"Camera Start Error: {e}")
             self.error_occurred.emit(f"Failed to open camera: {str(e)}")
             self._running = False
             return

        while self._running:
            if not self._paused:
                self._cam_mutex.lock()
                try:
                    frame = self.cam.get_image()
                finally:
                    self._cam_mutex.unlock()
                if frame is not None:
                    #print(f"DEBUG: Emitting Frame {frame.shape}")
                    self.new_frame.emit(frame)
                else:
                    # print("DEBUG: Frame is None")
                    pass
            time.sleep(1.0 / self.fps)
            
    def stop(self):
        self._running = False
        self.wait()
        self.cam.stop_acquisition()

    def set_pause(self, paused):
        self._paused = paused
        if paused:
            self.cam.stop_live()
        else:
            self.cam.resume_live()
            
    def set_exposure(self, us):
        self.cam.set_exposure(us)
        
    def set_gain(self, g):
        self.cam.set_gain(g)

    def capture_frame(self):
        """Safely grab a single frame while the thread may be running."""
        self._cam_mutex.lock()
        try:
            return self.cam.get_image()
        finally:
            self._cam_mutex.unlock()


class SequenceThread(QThread):
    progress_update = pyqtSignal(str) # Status message
    progress_val = pyqtSignal(int)    # 0-100
    finished_ok = pyqtSignal()
    error_occurred = pyqtSignal(str)
    
    def __init__(
        self,
        sequence_logic,
        save_dir,
        sample_id,
        exp_xpl,
        exp_ppl,
        angles,
        do_xpl,
        do_ppl,
        xpl_angle,
        ppl_angle,
        live_exposure_us,
        live_gain_db,
        live_thread_was_running,
    ):
        super().__init__()
        self.seq = sequence_logic
        self.save_dir = save_dir
        self.sample_id = sample_id
        self.exp_xpl = exp_xpl
        self.exp_ppl = exp_ppl
        self.angles = angles
        self.do_xpl = do_xpl
        self.do_ppl = do_ppl
        self.xpl_angle = xpl_angle
        self.ppl_angle = ppl_angle
        self.live_exposure_us = live_exposure_us
        self.live_gain_db = live_gain_db
        self.live_thread_was_running = live_thread_was_running
        
        # Override log callback to emit signal
        self.seq.log_cb = self._on_log
        
        # Calculate total steps for progress bar
        phase_count = int(self.do_xpl) + int(self.do_ppl)
        self.total_steps = max(1, len(self.angles) * phase_count)
        self.current_step = 0

    def _on_log(self, msg):
        self.progress_update.emit(msg)
        # Crude progress estimation based on keywords
        if "Sample" in msg:
            self.current_step += 1
            pct = int((self.current_step / self.total_steps) * 100)
            self.progress_val.emit(pct)

    def run(self):
        try:
            self.current_step = 0
            self.progress_val.emit(0)
            self.seq.run_sequence(
                self.save_dir, 
                self.sample_id, 
                self.exp_xpl, 
                self.exp_ppl,
                self.angles,
                self.do_xpl,
                self.do_ppl,
                self.xpl_angle,
                self.ppl_angle,
                live_exposure_us=self.live_exposure_us,
                live_gain_db=self.live_gain_db,
                live_thread_was_running=self.live_thread_was_running,
            )
            self.progress_val.emit(100)
            self.finished_ok.emit()
            
        except InterruptedError:
            self.error_occurred.emit("Sequence Aborted by User")
            
        except Exception as e:
            self.error_occurred.emit(f"Sequence Error: {str(e)}")

    def abort(self):
        self.seq.abort()


class PolarizerCalibrationThread(QThread):
    progress_update = pyqtSignal(str)
    progress_val = pyqtSignal(int)
    finished_ok = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        sequence_logic,
        save_dir,
        sample_id,
        exposure_us,
        polarizer_angles,
        sample_angle,
        live_exposure_us,
        live_gain_db,
        live_thread_was_running,
    ):
        super().__init__()
        self.seq = sequence_logic
        self.save_dir = save_dir
        self.sample_id = sample_id
        self.exposure_us = exposure_us
        self.polarizer_angles = polarizer_angles
        self.sample_angle = sample_angle
        self.live_exposure_us = live_exposure_us
        self.live_gain_db = live_gain_db
        self.live_thread_was_running = live_thread_was_running

        self.seq.log_cb = self._on_log

        self.total_steps = max(
            1,
            len(self.polarizer_angles)
            + (config.POLARIZER_CALIBRATION_FINE_RADIUS_DEG * 2)
            + 1,
        )
        self.current_step = 0

    def _on_log(self, msg):
        self.progress_update.emit(msg)
        if "Polarizer Calibration " in msg and "Angle" in msg:
            self.current_step += 1
            pct = int((self.current_step / self.total_steps) * 100)
            self.progress_val.emit(pct)

    def run(self):
        try:
            self.current_step = 0
            self.progress_val.emit(0)
            self.seq.run_polarizer_calibration(
                self.save_dir,
                self.sample_id,
                self.exposure_us,
                self.polarizer_angles,
                sample_angle=self.sample_angle,
                live_exposure_us=self.live_exposure_us,
                live_gain_db=self.live_gain_db,
                live_thread_was_running=self.live_thread_was_running,
            )
            self.progress_val.emit(100)
            self.finished_ok.emit()

        except InterruptedError:
            self.error_occurred.emit("Polarizer calibration aborted by user")

        except Exception as e:
            self.error_occurred.emit(f"Polarizer Calibration Error: {str(e)}")

    def abort(self):
        self.seq.abort()
