import logging
import os
import time

import numpy as np

from . import config, io, utils


class PicsSequence:
    def __init__(self, controller, camera, log_callback=None):
        """
        controller: PicsController instance
        camera: object with configure_live_mode/configure_sequence_mode/capture support
        log_callback: function(msg) for GUI updates
        """
        self.ctrl = controller
        self.cam = camera
        self.log_cb = log_callback
        self._abort_flag = False
        self.last_run_info = {}
        self.last_calibration_info = {}
        self.last_capture_info = {}

    def log(self, msg):
        logging.info(msg)
        if self.log_cb:
            self.log_cb(msg)

    def abort(self):
        self._abort_flag = True
        self.log("Abort requested!")

    def _check_abort(self):
        if self._abort_flag:
            raise InterruptedError("Sequence aborted by user.")

    def _wait_settling(self, duration=config.SETTLING_TIME_S):
        """Sliced wait with abort check."""
        start = time.time()
        while (time.time() - start) < duration:
            self._check_abort()
            time.sleep(0.05)

    def _build_metadata(self, save_dir, sample_id, live_thread_was_running, camera_connected_at_start):
        return {
            "sample_id": sample_id,
            "sequence_started_at": utils.get_timestamp_iso(),
            "sequence_completed": False,
            "error_message": "",
            "cleanup_error": "",
            "live_thread_was_running": live_thread_was_running,
            "camera_connected_at_start": camera_connected_at_start,
            "sequence_mode_configured": False,
            "save_root": save_dir,
            "images": [],
        }

    def _apply_camera_metadata(self, metadata):
        if not hasattr(self.cam, "get_capture_metadata"):
            return
        camera_metadata = self.cam.get_capture_metadata() or {}
        metadata.update(camera_metadata)

    def _capture_phase(
        self,
        *,
        mode_name,
        display_name,
        sample_angles,
        exposure_us,
        polarizer_angle,
        out_dir,
        sample_id,
        log_path,
        metadata,
    ):
        self.log(f"Phase: {display_name}")
        self.cam.set_exposure(exposure_us)

        self.log(f"Moving Polarizer to {polarizer_angle}...")
        if not self.ctrl.rotate_polarizer(polarizer_angle):
            raise RuntimeError(f"Failed to move Polarizer to {polarizer_angle}.")
        self._wait_settling()

        for angle in sample_angles:
            self._check_abort()
            self.log(f"{display_name}: Sample {angle} deg")

            if not self.ctrl.rotate_sample(angle):
                if mode_name == "xpl":
                    self.log(f"Error moving sample to {angle}. Retrying once...")
                    if not self.ctrl.rotate_sample(angle):
                        raise RuntimeError(f"Failed moving sample to {angle}")
                else:
                    raise RuntimeError(f"Failed moving sample to {angle}")

            self._wait_settling()

            img = self.cam.capture()
            if img is None:
                raise RuntimeError("Camera returned no image.")

            fname = f"{sample_id}_{mode_name}_{angle:03d}.tif"
            fpath = os.path.join(out_dir, fname)
            if not io.save_image(fpath, img):
                raise IOError(f"Failed to save image: {fpath}")

            log_row = {
                "timestamp": utils.get_timestamp_iso(),
                "mode": mode_name,
                "exposure_us": exposure_us,
                "gain": getattr(self.cam, "gain", 0.0),
                "polarizer_angle": polarizer_angle,
                "sample_angle": angle,
                "filepath": fpath,
                "arduino_response": "OK",
                "attempt_count": 1,
            }
            if not io.append_to_log(log_path, log_row):
                raise IOError(f"Failed to write CSV log: {log_path}")

            metadata["images"].append(
                {
                    "filename": fname,
                    "mode": mode_name,
                    "polarizer_angle_deg": polarizer_angle,
                    "sample_angle_deg": angle,
                    "exposure_us": exposure_us,
                    "timestamp": log_row["timestamp"],
                }
            )

    def _measure_signal_level(self, img):
        arr = np.asarray(img)
        if arr.ndim == 3:
            arr = arr.mean(axis=2)
        return float(arr.astype(np.float32).mean())

    def _capture_polarizer_scan(
        self,
        *,
        sample_id,
        exposure_us,
        sample_angle,
        polarizer_angles,
        scan_phase,
        out_dir,
        log_path,
        metadata,
        run_info,
    ):
        results = []
        for angle in polarizer_angles:
            self._check_abort()
            self.log(f"Polarizer Calibration {scan_phase}: Angle {angle} deg")
            if not self.ctrl.rotate_polarizer(angle):
                raise RuntimeError(f"Failed to move Polarizer to {angle}.")
            self._wait_settling()

            img = self.cam.capture()
            if img is None:
                raise RuntimeError("Camera returned no image.")

            fname = f"{sample_id}_polarizer_cal_{scan_phase}_{angle:03d}.tif"
            fpath = os.path.join(out_dir, fname)
            if not io.save_image(fpath, img):
                raise IOError(f"Failed to save image: {fpath}")

            signal_level = self._measure_signal_level(img)
            log_row = {
                "timestamp": utils.get_timestamp_iso(),
                "mode": "polarizer_calibration",
                "exposure_us": exposure_us,
                "gain": getattr(self.cam, "gain", 0.0),
                "polarizer_angle": angle,
                "sample_angle": sample_angle,
                "signal_mean": signal_level,
                "filepath": fpath,
                "arduino_response": scan_phase.upper(),
                "attempt_count": 1,
            }
            if not io.append_to_log(log_path, log_row):
                raise IOError(f"Failed to write CSV log: {log_path}")

            result = {
                "filename": fname,
                "scan_phase": scan_phase,
                "polarizer_angle_deg": angle,
                "sample_angle_deg": sample_angle,
                "exposure_us": exposure_us,
                "mean_signal": signal_level,
                "timestamp": log_row["timestamp"],
            }
            metadata["images"].append(
                {
                    "filename": fname,
                    "mode": "polarizer_calibration",
                    "scan_phase": scan_phase,
                    "polarizer_angle_deg": angle,
                    "sample_angle_deg": sample_angle,
                    "exposure_us": exposure_us,
                    "timestamp": log_row["timestamp"],
                }
            )
            metadata["scan_results"].append(result)
            run_info["scan_results"].append(result)
            results.append(result)

        return results

    def run_sequence(
        self,
        save_dir,
        sample_id,
        xpl_exposure_us,
        ppl_exposure_us,
        sample_angles=None,
        do_xpl=True,
        do_ppl=True,
        xpl_polarizer_angle=config.POLARIZER_XPL_ANGLE_DEG,
        ppl_polarizer_angle=config.POLARIZER_PPL_ANGLE_DEG,
        live_exposure_us=None,
        live_gain_db=None,
        live_thread_was_running=False,
    ):
        self._abort_flag = False
        self.last_run_info = {}
        if sample_angles is None:
            sample_angles = [90, 60, 45, 30, 0]
        if not (do_xpl or do_ppl):
            raise RuntimeError("No modes enabled for sequence.")

        camera_connected_at_start = bool(getattr(self.cam, "is_open", False))
        metadata = self._build_metadata(
            save_dir,
            sample_id,
            live_thread_was_running=live_thread_was_running,
            camera_connected_at_start=camera_connected_at_start,
        )
        run_info = {
            "camera_connected_at_start": camera_connected_at_start,
            "live_thread_was_running": live_thread_was_running,
            "raw_mode_configured": False,
            "saved_frame_count": 0,
            "cleanup_restore_ok": False,
            "cleanup_error": "",
            "body_error": "",
            "should_restart_live": False,
            "sequence_completed": False,
        }

        sample_root = os.path.join(save_dir, sample_id)
        xpl_dir = os.path.join(sample_root, "xpl")
        ppl_dir = os.path.join(sample_root, "ppl")
        log_path = os.path.join(sample_root, f"{sample_id}_log.csv")
        metadata_path = os.path.join(sample_root, f"{sample_id}_metadata.json")
        metadata["mode_angles_deg"] = {
            "xpl": xpl_polarizer_angle if do_xpl else None,
            "ppl": ppl_polarizer_angle if do_ppl else None,
        }

        body_error = None
        cleanup_error = None

        try:
            self.log("Starting Sequence...")

            if not self.ctrl.is_connected:
                raise RuntimeError("Controller not connected.")
            if not camera_connected_at_start:
                raise RuntimeError("Camera not connected.")

            if do_xpl:
                utils.ensure_dir(xpl_dir)
            if do_ppl:
                utils.ensure_dir(ppl_dir)

            self.log("Configuring camera for RAW16 sequence...")
            if hasattr(self.cam, "configure_sequence_mode"):
                self.cam.configure_sequence_mode()
            run_info["raw_mode_configured"] = True
            metadata["sequence_mode_configured"] = True
            self._apply_camera_metadata(metadata)

            self.log("Arming system (RESET)...")
            if not self.ctrl.reset():
                raise RuntimeError("Failed to ARM system (RESET command failed).")

            if do_xpl:
                self._capture_phase(
                    mode_name="xpl",
                    display_name="XPL",
                    sample_angles=sample_angles,
                    exposure_us=xpl_exposure_us,
                    polarizer_angle=xpl_polarizer_angle,
                    out_dir=xpl_dir,
                    sample_id=sample_id,
                    log_path=log_path,
                    metadata=metadata,
                )

            if do_ppl:
                self._capture_phase(
                    mode_name="ppl",
                    display_name="PPL",
                    sample_angles=sample_angles,
                    exposure_us=ppl_exposure_us,
                    polarizer_angle=ppl_polarizer_angle,
                    out_dir=ppl_dir,
                    sample_id=sample_id,
                    log_path=log_path,
                    metadata=metadata,
                )

            self.log("Sequence Complete. Homing...")
            self.ctrl.home()
            metadata["sequence_completed"] = True
            run_info["sequence_completed"] = True

        except InterruptedError as e:
            body_error = e
            metadata["error_message"] = str(e)
            self.log("Sequence Aborted!")

        except Exception as e:
            body_error = e
            metadata["error_message"] = str(e)
            self.log(f"Sequence Error: {e}")
            self.log("Emergency Stop Triggered due to Error.")
            try:
                self.ctrl.emergency_stop()
            except Exception as estop_error:
                logging.error(f"Emergency stop failed: {estop_error}")

        finally:
            run_info["saved_frame_count"] = len(metadata["images"])

            try:
                if hasattr(self.cam, "stop_acquisition"):
                    self.cam.stop_acquisition()
            except Exception as e:
                cleanup_error = cleanup_error or RuntimeError(f"Failed to stop acquisition: {e}")

            try:
                if camera_connected_at_start and getattr(self.cam, "is_open", False):
                    restore_exposure = live_exposure_us
                    if restore_exposure is None:
                        restore_exposure = getattr(
                            self.cam,
                            "exposure_us",
                            config.XIMEA_DEFAULT_NORMAL_EXPOSURE_US,
                        )
                    restore_gain = live_gain_db
                    if restore_gain is None:
                        restore_gain = getattr(self.cam, "gain", 0.0)
                    if hasattr(self.cam, "configure_live_mode"):
                        self.cam.configure_live_mode(restore_exposure, restore_gain)
                    run_info["cleanup_restore_ok"] = True
            except Exception as e:
                cleanup_error = cleanup_error or RuntimeError(f"Failed to restore live mode: {e}")

            if cleanup_error and not metadata["error_message"]:
                metadata["error_message"] = str(cleanup_error)
            metadata["cleanup_error"] = str(cleanup_error) if cleanup_error else ""

            metadata["sequence_completed"] = run_info["sequence_completed"]
            metadata["saved_frame_count"] = len(metadata["images"])

            if "camera_model" not in metadata:
                try:
                    self._apply_camera_metadata(metadata)
                except Exception:
                    pass

            try:
                if not io.save_json(metadata_path, metadata):
                    raise IOError(f"Failed to save metadata JSON: {metadata_path}")
            except Exception as e:
                cleanup_error = cleanup_error or RuntimeError(str(e))

            run_info["cleanup_error"] = str(cleanup_error) if cleanup_error else ""
            run_info["body_error"] = str(body_error) if body_error else ""
            run_info["should_restart_live"] = bool(
                live_thread_was_running
                and run_info["cleanup_restore_ok"]
                and not cleanup_error
                and camera_connected_at_start
            )
            self.last_run_info = run_info
            self.last_capture_info = run_info

        if cleanup_error:
            raise RuntimeError(f"Sequence cleanup failed: {cleanup_error}") from cleanup_error
        if body_error:
            raise body_error

    def run_polarizer_calibration(
        self,
        save_dir,
        sample_id,
        exposure_us,
        polarizer_angles,
        sample_angle=0,
        live_exposure_us=None,
        live_gain_db=None,
        live_thread_was_running=False,
    ):
        self._abort_flag = False
        self.last_calibration_info = {}
        self.last_capture_info = {}
        if not polarizer_angles:
            raise RuntimeError("No polarizer angles provided for calibration.")

        camera_connected_at_start = bool(getattr(self.cam, "is_open", False))
        metadata = self._build_metadata(
            save_dir,
            sample_id,
            live_thread_was_running=live_thread_was_running,
            camera_connected_at_start=camera_connected_at_start,
        )
        metadata["capture_type"] = "polarizer_calibration"
        metadata["calibration_sample_angle_deg"] = sample_angle
        metadata["coarse_scan_angles_deg"] = list(polarizer_angles)
        metadata["scan_results"] = []
        run_info = {
            "camera_connected_at_start": camera_connected_at_start,
            "live_thread_was_running": live_thread_was_running,
            "raw_mode_configured": False,
            "saved_frame_count": 0,
            "cleanup_restore_ok": False,
            "cleanup_error": "",
            "body_error": "",
            "should_restart_live": False,
            "sequence_completed": False,
            "recommended_xpl_angle_deg": None,
            "recommended_ppl_angle_deg": None,
            "recommended_ppl_offset_deg": None,
            "scan_results": [],
        }

        sample_root = os.path.join(save_dir, sample_id)
        calibration_dir = os.path.join(sample_root, "polarizer_calibration")
        log_path = os.path.join(sample_root, f"{sample_id}_log.csv")
        metadata_path = os.path.join(sample_root, f"{sample_id}_polarizer_calibration.json")

        body_error = None
        cleanup_error = None

        try:
            self.log("Starting Polarizer Calibration...")

            if not self.ctrl.is_connected:
                raise RuntimeError("Controller not connected.")
            if not camera_connected_at_start:
                raise RuntimeError("Camera not connected.")

            utils.ensure_dir(calibration_dir)

            self.log("Configuring camera for RAW16 calibration scan...")
            if hasattr(self.cam, "configure_sequence_mode"):
                self.cam.configure_sequence_mode()
            run_info["raw_mode_configured"] = True
            metadata["sequence_mode_configured"] = True
            self._apply_camera_metadata(metadata)

            self.log("Arming system (RESET)...")
            if not self.ctrl.reset():
                raise RuntimeError("Failed to ARM system (RESET command failed).")

            self.log(f"Moving Sample to {sample_angle}...")
            if not self.ctrl.rotate_sample(sample_angle):
                raise RuntimeError(f"Failed to move Sample to {sample_angle}.")
            self._wait_settling()

            self.cam.set_exposure(exposure_us)

            coarse_results = self._capture_polarizer_scan(
                sample_id=sample_id,
                exposure_us=exposure_us,
                sample_angle=sample_angle,
                polarizer_angles=polarizer_angles,
                scan_phase="coarse",
                out_dir=calibration_dir,
                log_path=log_path,
                metadata=metadata,
                run_info=run_info,
            )

            if coarse_results:
                coarse_xpl = min(
                    coarse_results,
                    key=lambda item: (item["mean_signal"], item["polarizer_angle_deg"]),
                )
                metadata["coarse_darkest_angle_deg"] = coarse_xpl["polarizer_angle_deg"]
                metadata["coarse_darkest_signal_mean"] = coarse_xpl["mean_signal"]
                fine_scan_angles = utils.build_angle_window(
                    coarse_xpl["polarizer_angle_deg"],
                    config.POLARIZER_CALIBRATION_FINE_RADIUS_DEG,
                    config.POLARIZER_STAGE_MIN_ANGLE,
                    config.POLARIZER_STAGE_MAX_ANGLE,
                    config.POLARIZER_CALIBRATION_FINE_STEP_DEG,
                )
                metadata["fine_scan_angles_deg"] = fine_scan_angles
                self.log(
                    "Fine XPL search around darkest coarse angle "
                    f"{coarse_xpl['polarizer_angle_deg']} deg: "
                    f"{fine_scan_angles[0]} to {fine_scan_angles[-1]} by "
                    f"{config.POLARIZER_CALIBRATION_FINE_STEP_DEG} deg"
                )
                fine_results = self._capture_polarizer_scan(
                    sample_id=sample_id,
                    exposure_us=exposure_us,
                    sample_angle=sample_angle,
                    polarizer_angles=fine_scan_angles,
                    scan_phase="fine",
                    out_dir=calibration_dir,
                    log_path=log_path,
                    metadata=metadata,
                    run_info=run_info,
                )
                xpl_candidate = min(
                    fine_results,
                    key=lambda item: (item["mean_signal"], item["polarizer_angle_deg"]),
                )
                run_info["recommended_xpl_angle_deg"] = xpl_candidate["polarizer_angle_deg"]
                recommended_ppl_angle, recommended_ppl_offset = utils.choose_orthogonal_polarizer_angle(
                    run_info["recommended_xpl_angle_deg"],
                    config.POLARIZER_STAGE_MIN_ANGLE,
                    config.POLARIZER_STAGE_MAX_ANGLE,
                )
                run_info["recommended_ppl_angle_deg"] = recommended_ppl_angle
                run_info["recommended_ppl_offset_deg"] = recommended_ppl_offset
                metadata["recommended_xpl_angle_deg"] = run_info["recommended_xpl_angle_deg"]
                metadata["recommended_ppl_angle_deg"] = run_info["recommended_ppl_angle_deg"]
                metadata["recommended_ppl_offset_deg"] = run_info["recommended_ppl_offset_deg"]
                metadata["xpl_signal_mean"] = xpl_candidate["mean_signal"]
                self.log(
                    "Polarizer calibration recommendation: "
                    f"XPL darkest={run_info['recommended_xpl_angle_deg']} deg, "
                    f"PPL=XPL{run_info['recommended_ppl_offset_deg']:+d} -> "
                    f"{run_info['recommended_ppl_angle_deg']} deg"
                )

            self.log("Polarizer Calibration Complete. Homing...")
            self.ctrl.home()
            metadata["sequence_completed"] = True
            run_info["sequence_completed"] = True

        except InterruptedError as e:
            body_error = e
            metadata["error_message"] = str(e)
            self.log("Polarizer Calibration Aborted!")

        except Exception as e:
            body_error = e
            metadata["error_message"] = str(e)
            self.log(f"Polarizer Calibration Error: {e}")
            self.log("Emergency Stop Triggered due to Error.")
            try:
                self.ctrl.emergency_stop()
            except Exception as estop_error:
                logging.error(f"Emergency stop failed: {estop_error}")

        finally:
            run_info["saved_frame_count"] = len(metadata["images"])

            try:
                if hasattr(self.cam, "stop_acquisition"):
                    self.cam.stop_acquisition()
            except Exception as e:
                cleanup_error = cleanup_error or RuntimeError(f"Failed to stop acquisition: {e}")

            try:
                if camera_connected_at_start and getattr(self.cam, "is_open", False):
                    restore_exposure = live_exposure_us
                    if restore_exposure is None:
                        restore_exposure = getattr(
                            self.cam,
                            "exposure_us",
                            config.XIMEA_DEFAULT_PPL_EXPOSURE_US,
                        )
                    restore_gain = live_gain_db
                    if restore_gain is None:
                        restore_gain = getattr(self.cam, "gain", 0.0)
                    if hasattr(self.cam, "configure_live_mode"):
                        self.cam.configure_live_mode(restore_exposure, restore_gain)
                    run_info["cleanup_restore_ok"] = True
            except Exception as e:
                cleanup_error = cleanup_error or RuntimeError(f"Failed to restore live mode: {e}")

            if cleanup_error and not metadata["error_message"]:
                metadata["error_message"] = str(cleanup_error)
            metadata["cleanup_error"] = str(cleanup_error) if cleanup_error else ""

            metadata["sequence_completed"] = run_info["sequence_completed"]
            metadata["saved_frame_count"] = len(metadata["images"])

            if "camera_model" not in metadata:
                try:
                    self._apply_camera_metadata(metadata)
                except Exception:
                    pass

            try:
                if not io.save_json(metadata_path, metadata):
                    raise IOError(f"Failed to save metadata JSON: {metadata_path}")
            except Exception as e:
                cleanup_error = cleanup_error or RuntimeError(str(e))

            run_info["cleanup_error"] = str(cleanup_error) if cleanup_error else ""
            run_info["body_error"] = str(body_error) if body_error else ""
            run_info["should_restart_live"] = bool(
                live_thread_was_running
                and run_info["cleanup_restore_ok"]
                and not cleanup_error
                and camera_connected_at_start
            )
            self.last_calibration_info = run_info
            self.last_capture_info = run_info

        if cleanup_error:
            raise RuntimeError(f"Calibration cleanup failed: {cleanup_error}") from cleanup_error
        if body_error:
            raise body_error
