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
        polarizer_already_positioned=False,
    ):
        self.log(f"Phase: {display_name}")
        self.cam.set_exposure(exposure_us)

        if polarizer_already_positioned:
            self.log(f"Polarizer already positioned at {polarizer_angle} for {display_name}.")
        else:
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

    def _measure_signal_level(self, img, roi_size_px=None):
        arr = np.asarray(img)
        if arr.ndim == 3:
            arr = arr.mean(axis=2)
        if roi_size_px is not None and roi_size_px > 0:
            h, w = arr.shape[:2]
            roi = min(int(roi_size_px), h, w)
            y0 = (h - roi) // 2
            x0 = (w - roi) // 2
            arr = arr[y0:y0 + roi, x0:x0 + roi]
        return float(arr.astype(np.float32).mean())

    def _measure_roi_signal_level(self, img, roi_rect):
        arr = np.asarray(img)
        if arr.ndim == 3:
            arr = arr.mean(axis=2)

        x, y, width, height = [int(value) for value in roi_rect]
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid ROI size: width={width}, height={height}")

        image_height, image_width = arr.shape[:2]
        if x < 0 or y < 0 or x + width > image_width or y + height > image_height:
            raise ValueError(
                "Background ROI is outside image bounds: "
                f"roi=(x={x}, y={y}, w={width}, h={height}), "
                f"image=(w={image_width}, h={image_height})"
            )

        roi = arr[y:y + height, x:x + width]
        return float(roi.astype(np.float32).mean())

    def _xpl_background_roi_specs(self):
        x = config.XPL_BACKGROUND_ROI_X
        width = config.XPL_BACKGROUND_ROI_WIDTH
        height = config.XPL_BACKGROUND_ROI_HEIGHT
        return [
            {
                "name": "bottom",
                "rect": (x, config.XPL_BACKGROUND_ROI_BOTTOM_Y, width, height),
            },
            {
                "name": "top",
                "rect": (x, config.XPL_BACKGROUND_ROI_TOP_Y, width, height),
            },
        ]

    @staticmethod
    def _roi_rect_metadata(roi_rect):
        return {
            "x": int(roi_rect[0]),
            "y": int(roi_rect[1]),
            "width": int(roi_rect[2]),
            "height": int(roi_rect[3]),
        }

    def _measure_xpl_roi_signals(self, img, roi_specs):
        signals = {}
        for spec in roi_specs:
            signals[spec["name"]] = self._measure_roi_signal_level(img, spec["rect"])
        return signals

    @staticmethod
    def _select_xpl_roi(roi_specs, roi_signals, active_roi_name=None):
        if active_roi_name is not None:
            for spec in roi_specs:
                if spec["name"] == active_roi_name:
                    return spec
            raise ValueError(f"Unknown XPL background ROI: {active_roi_name}")
        return min(
            roi_specs,
            key=lambda spec: (roi_signals[spec["name"]], roi_specs.index(spec)),
        )

    @staticmethod
    def _xpl_result_signal(result, roi_name=None):
        if roi_name is not None and "background_roi_signals" in result:
            return result["background_roi_signals"][roi_name]
        return result["background_roi_signal_mean"]

    def _approach_polarizer_from_below(self, target_angle):
        target_angle = int(target_angle)
        approach_angle = max(
            config.POLARIZER_STAGE_MIN_ANGLE,
            target_angle - int(config.XPL_LOCAL_CALIBRATION_FINAL_APPROACH_OFFSET_DEG),
        )
        if approach_angle != target_angle:
            self.log(
                f"Local XPL Calibration: final approach {approach_angle} -> {target_angle} deg"
            )
            if not self.ctrl.rotate_polarizer(approach_angle):
                raise RuntimeError(f"Failed final XPL approach preload to {approach_angle}.")
            self._wait_settling()
        if not self.ctrl.rotate_polarizer(target_angle):
            raise RuntimeError(f"Failed final XPL approach to {target_angle}.")
        self._wait_settling()

    @staticmethod
    def _best_xpl_result(results, roi_name=None):
        return min(
            results,
            key=lambda item: (
                PicsSequence._xpl_result_signal(item, roi_name),
                item["polarizer_angle_deg"],
            ),
        )

    def _capture_local_xpl_point(
        self,
        *,
        sample_id,
        exposure_us,
        sample_angle,
        angle,
        scan_phase,
        out_dir,
        log_path,
        roi_specs,
        active_roi_name=None,
    ):
        self._check_abort()
        self.log(f"Local XPL Calibration {scan_phase}: Angle {angle} deg")
        if not self.ctrl.rotate_polarizer(angle):
            raise RuntimeError(f"Failed to move Polarizer to {angle}.")
        self._wait_settling()

        img = self.cam.capture()
        if img is None:
            raise RuntimeError("Camera returned no image.")

        fname = f"{sample_id}_xpl_local_cal_{scan_phase}_{angle:03d}.tif"
        fpath = os.path.join(out_dir, fname)
        if not io.save_image(fpath, img):
            raise IOError(f"Failed to save image: {fpath}")

        roi_signals = self._measure_xpl_roi_signals(img, roi_specs)
        selected_roi = self._select_xpl_roi(roi_specs, roi_signals, active_roi_name)
        selected_roi_name = selected_roi["name"]
        signal_level = roi_signals[selected_roi_name]
        selected_roi_metadata = self._roi_rect_metadata(selected_roi["rect"])
        self.log(
            f"Local XPL Calibration {scan_phase}: {selected_roi_name} ROI mean={signal_level:.2f} "
            f"(top={roi_signals.get('top', float('nan')):.2f}, "
            f"bottom={roi_signals.get('bottom', float('nan')):.2f})"
        )
        log_row = {
            "timestamp": utils.get_timestamp_iso(),
            "mode": "xpl_local_calibration",
            "exposure_us": exposure_us,
            "gain": getattr(self.cam, "gain", 0.0),
            "polarizer_angle": angle,
            "sample_angle": sample_angle,
            "signal_mean": signal_level,
            "details": {
                "background_roi_name": selected_roi_name,
                "background_roi": selected_roi_metadata,
                "background_roi_signals": roi_signals,
            },
            "filepath": fpath,
            "arduino_response": scan_phase.upper(),
            "attempt_count": 1,
        }
        if not io.append_to_log(log_path, log_row):
            raise IOError(f"Failed to write CSV log: {log_path}")

        return {
            "filename": fname,
            "scan_phase": scan_phase,
            "polarizer_angle_deg": int(angle),
            "sample_angle_deg": sample_angle,
            "exposure_us": exposure_us,
            "background_roi_signal_mean": signal_level,
            "background_roi_name": selected_roi_name,
            "background_roi": selected_roi_metadata,
            "background_roi_signals": roi_signals,
            "timestamp": log_row["timestamp"],
        }

    def _capture_local_xpl_scan(
        self,
        *,
        angles,
        scan_phase,
        sample_id,
        exposure_us,
        sample_angle,
        out_dir,
        log_path,
        roi_specs,
        active_roi_name=None,
    ):
        results = []
        for angle in angles:
            results.append(
                self._capture_local_xpl_point(
                    sample_id=sample_id,
                    exposure_us=exposure_us,
                    sample_angle=sample_angle,
                    angle=angle,
                    scan_phase=scan_phase,
                    out_dir=out_dir,
                    log_path=log_path,
                    roi_specs=roi_specs,
                    active_roi_name=active_roi_name,
                )
            )
        return results

    def _capture_early_stop_xpl_coarse(
        self,
        *,
        coarse_angles,
        sample_id,
        exposure_us,
        sample_angle,
        out_dir,
        log_path,
        roi_specs,
    ):
        results = []
        trigger_result = None
        previous_angle = None
        for angle in coarse_angles:
            result = self._capture_local_xpl_point(
                sample_id=sample_id,
                exposure_us=exposure_us,
                sample_angle=sample_angle,
                angle=angle,
                scan_phase="coarse",
                out_dir=out_dir,
                log_path=log_path,
                roi_specs=roi_specs,
            )
            results.append(result)
            if result["background_roi_signal_mean"] <= config.XPL_LOCAL_COARSE_TRIGGER_MEAN:
                trigger_result = result
                self.log(
                    "Local XPL coarse trigger at "
                    f"{angle} deg (ROI mean={result['background_roi_signal_mean']:.2f})"
                )
                break
            previous_angle = angle

        if trigger_result is None:
            best = self._best_xpl_result(results)
            active_roi_name = best["background_roi_name"]
            fine_start = max(
                config.POLARIZER_STAGE_MIN_ANGLE,
                int(best["polarizer_angle_deg"]) - int(config.XPL_LOCAL_CALIBRATION_RADIUS_DEG),
            )
            self.log(
                "Local XPL coarse did not reach trigger; refining around best coarse "
                f"{best['polarizer_angle_deg']} deg using {active_roi_name} ROI"
            )
        else:
            active_roi_name = trigger_result["background_roi_name"]
            fine_start = int(previous_angle) if previous_angle is not None else int(trigger_result["polarizer_angle_deg"])

        return results, fine_start, trigger_result, active_roi_name

    def _capture_directional_fine_until_trend_change(
        self,
        *,
        fine_start,
        sample_id,
        exposure_us,
        sample_angle,
        out_dir,
        log_path,
        roi_specs,
        active_roi_name,
        scan_phase,
    ):
        step = int(config.XPL_LOCAL_CALIBRATION_STEP_DEG)
        fine_end = min(
            config.POLARIZER_STAGE_MAX_ANGLE,
            int(fine_start) + int(config.XPL_LOCAL_FINE_MAX_SPAN_DEG),
        )
        results = []
        best = None
        previous_signal = None
        consecutive_increases = 0

        for angle in range(int(fine_start), fine_end + 1, step):
            result = self._capture_local_xpl_point(
                sample_id=sample_id,
                exposure_us=exposure_us,
                sample_angle=sample_angle,
                angle=angle,
                scan_phase=scan_phase,
                out_dir=out_dir,
                log_path=log_path,
                roi_specs=roi_specs,
                active_roi_name=active_roi_name,
            )
            results.append(result)
            signal = self._xpl_result_signal(result, active_roi_name)

            if best is None or signal < self._xpl_result_signal(best, active_roi_name):
                best = result
                consecutive_increases = 0
            elif previous_signal is not None and signal > previous_signal:
                consecutive_increases += 1
            else:
                consecutive_increases = 0

            if best is not None and int(result["polarizer_angle_deg"]) > int(best["polarizer_angle_deg"]):
                if consecutive_increases >= int(config.XPL_LOCAL_FINE_STOP_INCREASE_COUNT):
                    self.log(
                        "Local XPL fine scan stopped after "
                        f"{consecutive_increases} consecutive increases"
                    )
                    break
                if signal > self._xpl_result_signal(best, active_roi_name) * float(config.XPL_LOCAL_FINE_STOP_RATIO):
                    self.log(
                        "Local XPL fine scan stopped after exceeding best by "
                        f"{config.XPL_LOCAL_FINE_STOP_RATIO:.2f}x"
                    )
                    break

            previous_signal = signal

        return results

    def _run_local_xpl_calibration(
        self,
        *,
        sample_id,
        exposure_us,
        sample_angle,
        center_angle,
        out_dir,
        log_path,
        metadata,
        run_info,
        coarse_angles=None,
    ):
        roi_specs = self._xpl_background_roi_specs()
        utils.ensure_dir(out_dir)
        if coarse_angles is None:
            coarse_angles = config.XPL_ADAPTIVE_COARSE_ANGLES_DEG
        coarse_angles = sorted(set(int(angle) for angle in coarse_angles))
        self.log(
            "Local XPL Calibration: "
            f"sample={sample_angle} deg, ROIs={roi_specs}, "
            f"coarse={coarse_angles}"
        )
        self.cam.set_exposure(exposure_us)

        coarse_results, fine_start, coarse_trigger, active_roi_name = self._capture_early_stop_xpl_coarse(
            coarse_angles=coarse_angles,
            sample_id=sample_id,
            exposure_us=exposure_us,
            sample_angle=sample_angle,
            out_dir=out_dir,
            log_path=log_path,
            roi_specs=roi_specs,
        )
        self.log(f"Local XPL Calibration focusing on {active_roi_name} ROI")
        fine_results = self._capture_directional_fine_until_trend_change(
            fine_start=fine_start,
            sample_id=sample_id,
            exposure_us=exposure_us,
            sample_angle=sample_angle,
            out_dir=out_dir,
            log_path=log_path,
            roi_specs=roi_specs,
            active_roi_name=active_roi_name,
            scan_phase="fine",
        )

        results = coarse_results + fine_results

        selected = self._best_xpl_result(results, active_roi_name)
        confirmation = None
        refine_results = []
        for refine_round in range(int(config.XPL_LOCAL_CONFIRMATION_MAX_REFINES) + 1):
            selected_angle = int(selected["polarizer_angle_deg"])
            selected_signal = self._xpl_result_signal(selected, active_roi_name)
            self._approach_polarizer_from_below(selected_angle)
            confirmation = self._capture_local_xpl_point(
                sample_id=sample_id,
                exposure_us=exposure_us,
                sample_angle=sample_angle,
                angle=selected_angle,
                scan_phase=f"confirm_r{refine_round + 1}",
                out_dir=out_dir,
                log_path=log_path,
                roi_specs=roi_specs,
                active_roi_name=active_roi_name,
            )
            confirmation["selected_scan_signal_mean"] = selected_signal
            confirmation["confirmation_ratio_to_selected"] = (
                confirmation["background_roi_signal_mean"] / selected_signal if selected_signal else None
            )
            confirmation_ratio = confirmation["confirmation_ratio_to_selected"]
            confirmation["confirmation_ok"] = (
                confirmation["background_roi_signal_mean"] <= config.XPL_LOCAL_CONFIRMATION_MAX_MEAN
                and confirmation_ratio is not None
                and confirmation_ratio <= config.XPL_LOCAL_CONFIRMATION_MAX_RATIO
            )
            results.append(confirmation)
            if confirmation["confirmation_ok"]:
                break

            if refine_round >= int(config.XPL_LOCAL_CONFIRMATION_MAX_REFINES):
                break

            self.log(
                "Local XPL confirmation failed at "
                f"{selected_angle} deg "
                f"(ROI mean={confirmation['background_roi_signal_mean']:.2f}); "
                "discarding stale candidate and refining nearby"
            )
            refine_angles = utils.build_angle_window(
                selected_angle,
                config.XPL_LOCAL_CONFIRMATION_REFINE_RADIUS_DEG,
                config.POLARIZER_STAGE_MIN_ANGLE,
                config.POLARIZER_STAGE_MAX_ANGLE,
                config.XPL_LOCAL_CALIBRATION_STEP_DEG,
            )
            round_refine_results = self._capture_local_xpl_scan(
                angles=refine_angles,
                scan_phase=f"refine_r{refine_round + 1}",
                sample_id=sample_id,
                exposure_us=exposure_us,
                sample_angle=sample_angle,
                out_dir=out_dir,
                log_path=log_path,
                roi_specs=roi_specs,
                active_roi_name=active_roi_name,
            )
            refine_results.extend(round_refine_results)
            results.extend(round_refine_results)
            selected = self._best_xpl_result(round_refine_results, active_roi_name)

        if confirmation is None:
            raise RuntimeError("Local XPL confirmation was not captured.")
        selected_angle = int(selected["polarizer_angle_deg"])
        calibration_info = {
            "center_angle_deg": int(center_angle),
            "coarse_angles_deg": coarse_angles,
            "coarse_trigger_mean": config.XPL_LOCAL_COARSE_TRIGGER_MEAN,
            "coarse_trigger_angle_deg": coarse_trigger["polarizer_angle_deg"] if coarse_trigger else None,
            "coarse_trigger_roi_name": coarse_trigger["background_roi_name"] if coarse_trigger else None,
            "fine_start_angle_deg": int(fine_start),
            "fine_angles_deg": [item["polarizer_angle_deg"] for item in fine_results],
            "fine_stop_increase_count": config.XPL_LOCAL_FINE_STOP_INCREASE_COUNT,
            "fine_stop_ratio": config.XPL_LOCAL_FINE_STOP_RATIO,
            "refine_angles_deg": [item["polarizer_angle_deg"] for item in refine_results],
            "sample_angle_deg": sample_angle,
            "exposure_us": exposure_us,
            "background_rois": {
                spec["name"]: self._roi_rect_metadata(spec["rect"]) for spec in roi_specs
            },
            "selected_background_roi_name": active_roi_name,
            "background_roi": self._roi_rect_metadata(
                next(spec["rect"] for spec in roi_specs if spec["name"] == active_roi_name)
            ),
            "selected_xpl_angle_deg": selected_angle,
            "selected_background_roi_signal_mean": self._xpl_result_signal(selected, active_roi_name),
            "confirmation_background_roi_signal_mean": self._xpl_result_signal(confirmation, active_roi_name),
            "confirmation_max_mean": config.XPL_LOCAL_CONFIRMATION_MAX_MEAN,
            "confirmation_max_ratio": config.XPL_LOCAL_CONFIRMATION_MAX_RATIO,
            "confirmation_ok": confirmation["confirmation_ok"],
            "confirmation_ratio_to_selected": confirmation["confirmation_ratio_to_selected"],
            "final_approach_offset_deg": config.XPL_LOCAL_CALIBRATION_FINAL_APPROACH_OFFSET_DEG,
            "results": results,
        }
        metadata.setdefault("local_xpl_calibrations", []).append(calibration_info)
        run_info.setdefault("local_xpl_calibrations", []).append(calibration_info)

        if not confirmation["confirmation_ok"]:
            confirmation_ratio = confirmation["confirmation_ratio_to_selected"]
            ratio_text = f"{confirmation_ratio:.3f}" if confirmation_ratio is not None else "n/a"
            raise RuntimeError(
                "Local XPL confirmation failed: "
                f"ROI mean {confirmation['background_roi_signal_mean']:.2f} > "
                f"{config.XPL_LOCAL_CONFIRMATION_MAX_MEAN:.2f} or ratio "
                f"{ratio_text} > "
                f"{config.XPL_LOCAL_CONFIRMATION_MAX_RATIO:.3f}"
            )

        run_info["effective_xpl_angle_deg"] = selected_angle
        self.log(
            "Local XPL Calibration selected "
            f"{selected_angle} deg ({active_roi_name} ROI mean="
            f"{self._xpl_result_signal(selected, active_roi_name):.2f})"
        )
        return selected_angle

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
        local_xpl_calibration_dir = os.path.join(sample_root, "xpl_local_calibration")
        log_path = os.path.join(sample_root, f"{sample_id}_log.csv")
        metadata_path = os.path.join(sample_root, f"{sample_id}_metadata.json")
        metadata["requested_mode_angles_deg"] = {
            "xpl": xpl_polarizer_angle if do_xpl else None,
            "ppl": ppl_polarizer_angle if do_ppl else None,
        }
        metadata["mode_angles_deg"] = dict(metadata["requested_mode_angles_deg"])
        metadata["local_xpl_calibrations"] = []

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

            self.log("Starting sequence from HOME...")
            if not self.ctrl.home():
                raise RuntimeError("Failed to HOME system before adaptive XPL calibration.")
            self._wait_settling()

            effective_xpl_angle = int(xpl_polarizer_angle)
            effective_ppl_angle = int(ppl_polarizer_angle)

            if do_xpl:
                self.log("Moving Sample to 0 deg for local XPL calibration...")
                if not self.ctrl.rotate_sample(config.SAMPLE_STAGE_MIN_ANGLE):
                    raise RuntimeError(
                        f"Failed to move Sample to {config.SAMPLE_STAGE_MIN_ANGLE} before local XPL calibration."
                    )
                self._wait_settling()

                effective_xpl_angle = self._run_local_xpl_calibration(
                    sample_id=sample_id,
                    exposure_us=config.XPL_LOCAL_CALIBRATION_EXPOSURE_US,
                    sample_angle=config.SAMPLE_STAGE_MIN_ANGLE,
                    center_angle=xpl_polarizer_angle,
                    out_dir=local_xpl_calibration_dir,
                    log_path=log_path,
                    metadata=metadata,
                    run_info=run_info,
                )
                metadata["mode_angles_deg"]["xpl"] = effective_xpl_angle
                if do_ppl:
                    effective_ppl_angle, ppl_offset = utils.choose_orthogonal_polarizer_angle(
                        effective_xpl_angle,
                        config.POLARIZER_STAGE_MIN_ANGLE,
                        config.POLARIZER_STAGE_MAX_ANGLE,
                        prefer_positive=False,
                    )
                    metadata["mode_angles_deg"]["ppl"] = effective_ppl_angle
                    metadata["ppl_offset_from_local_xpl_deg"] = ppl_offset
                    run_info["effective_ppl_angle_deg"] = effective_ppl_angle
                    run_info["ppl_offset_from_local_xpl_deg"] = ppl_offset

                self._capture_phase(
                    mode_name="xpl",
                    display_name="XPL",
                    sample_angles=sample_angles,
                    exposure_us=xpl_exposure_us,
                    polarizer_angle=effective_xpl_angle,
                    out_dir=xpl_dir,
                    sample_id=sample_id,
                    log_path=log_path,
                    metadata=metadata,
                    polarizer_already_positioned=True,
                )
                run_info["final_polarizer_angle_deg"] = effective_xpl_angle

            if do_ppl:
                self._capture_phase(
                    mode_name="ppl",
                    display_name="PPL",
                    sample_angles=sample_angles,
                    exposure_us=ppl_exposure_us,
                    polarizer_angle=effective_ppl_angle,
                    out_dir=ppl_dir,
                    sample_id=sample_id,
                    log_path=log_path,
                    metadata=metadata,
                )
                run_info["final_polarizer_angle_deg"] = effective_ppl_angle

            self.log("Sequence Complete. Returning sample to 0 deg...")
            if not self.ctrl.rotate_sample(config.SAMPLE_STAGE_MIN_ANGLE):
                raise RuntimeError(f"Failed to return Sample to {config.SAMPLE_STAGE_MIN_ANGLE}.")
            self._wait_settling()
            run_info["final_sample_angle_deg"] = config.SAMPLE_STAGE_MIN_ANGLE
            metadata["final_positions_deg"] = {
                "polarizer": run_info.get("final_polarizer_angle_deg"),
                "sample": run_info["final_sample_angle_deg"],
            }
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
        calibration_exposure_us,
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
        if calibration_exposure_us is None:
            calibration_exposure_us = config.XIMEA_DEFAULT_POLARIZER_CALIBRATION_EXPOSURE_US
        metadata["adaptive_exposure_us"] = calibration_exposure_us
        metadata["coarse_scan_angles_deg"] = list(polarizer_angles)
        metadata["local_xpl_calibrations"] = []
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

            self.log(f"Setting adaptive calibration exposure to {calibration_exposure_us} us")
            calibration_xpl = self._run_local_xpl_calibration(
                sample_id=sample_id,
                exposure_us=calibration_exposure_us,
                sample_angle=sample_angle,
                center_angle=config.POLARIZER_XPL_ANGLE_DEG,
                out_dir=calibration_dir,
                log_path=log_path,
                metadata=metadata,
                run_info=run_info,
                coarse_angles=polarizer_angles,
            )
            calibration_info = metadata["local_xpl_calibrations"][-1]
            calibration_results = calibration_info["results"]
            metadata["scan_results"] = calibration_results
            run_info["scan_results"] = calibration_results
            metadata["images"] = [
                {
                    "filename": item["filename"],
                    "mode": "polarizer_calibration",
                    "scan_phase": item["scan_phase"],
                    "polarizer_angle_deg": item["polarizer_angle_deg"],
                    "sample_angle_deg": item["sample_angle_deg"],
                    "exposure_us": item["exposure_us"],
                    "timestamp": item["timestamp"],
                }
                for item in calibration_results
            ]
            run_info["recommended_xpl_angle_deg"] = calibration_xpl
            recommended_ppl_angle, recommended_ppl_offset = utils.choose_orthogonal_polarizer_angle(
                calibration_xpl,
                config.POLARIZER_STAGE_MIN_ANGLE,
                config.POLARIZER_STAGE_MAX_ANGLE,
            )
            run_info["recommended_ppl_angle_deg"] = recommended_ppl_angle
            run_info["recommended_ppl_offset_deg"] = recommended_ppl_offset
            metadata["recommended_xpl_angle_deg"] = run_info["recommended_xpl_angle_deg"]
            metadata["recommended_ppl_angle_deg"] = run_info["recommended_ppl_angle_deg"]
            metadata["recommended_ppl_offset_deg"] = run_info["recommended_ppl_offset_deg"]
            metadata["xpl_signal_mean"] = calibration_info["selected_background_roi_signal_mean"]
            metadata["selected_background_roi_name"] = calibration_info["selected_background_roi_name"]
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
