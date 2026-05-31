import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apis import config, io, utils  # noqa: E402
from apis.controller import PicsController  # noqa: E402
from app.workers import XimeaCamera  # noqa: E402


DEFAULT_OUTPUT_PARENT = Path.cwd() / "data"


def parse_angle_list(value: str) -> list[int]:
    angles = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        angles.append(int(item))
    if not angles:
        raise ValueError("No angles provided.")
    return sorted(set(angles))


def build_angle_window(center: int, radius: int, step: int) -> list[int]:
    start = max(config.POLARIZER_STAGE_MIN_ANGLE, int(center) - int(radius))
    end = min(config.POLARIZER_STAGE_MAX_ANGLE, int(center) + int(radius))
    return list(range(start, end + 1, int(step)))


def prompt_value(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adaptive background-ROI XPL search: coarse scan, fine scan, final confirmation."
    )
    parser.add_argument("--port", help="Arduino COM port, e.g. COM8.")
    parser.add_argument(
        "--output",
        help="Parent output folder. A timestamped adaptive_xpl_search folder will be created inside it.",
    )
    parser.add_argument("--runs", type=int, default=1, help="Repeat the full adaptive search this many times.")
    parser.add_argument(
        "--coarse-angles",
        default="90,100,110,120,130,140,150",
        help="Comma-separated coarse polarizer angles. Default: 90,100,110,120,130,140,150",
    )
    parser.add_argument("--sample-angle", type=int, default=0, help="Sample angle during search. Default: 0")
    parser.add_argument(
        "--exposure-us",
        type=int,
        default=config.XPL_LOCAL_CALIBRATION_EXPOSURE_US,
        help=f"RAW16 exposure for search captures. Default: {config.XPL_LOCAL_CALIBRATION_EXPOSURE_US}",
    )
    parser.add_argument("--roi-x", type=int, default=config.XPL_BACKGROUND_ROI_X)
    parser.add_argument(
        "--roi-y",
        type=int,
        default=None,
        help="Legacy single-ROI y coordinate. If set, top/bottom ROI selection is disabled.",
    )
    parser.add_argument("--roi-top-y", type=int, default=config.XPL_BACKGROUND_ROI_TOP_Y)
    parser.add_argument("--roi-bottom-y", type=int, default=config.XPL_BACKGROUND_ROI_BOTTOM_Y)
    parser.add_argument("--roi-width", type=int, default=config.XPL_BACKGROUND_ROI_WIDTH)
    parser.add_argument("--roi-height", type=int, default=config.XPL_BACKGROUND_ROI_HEIGHT)
    parser.add_argument(
        "--coarse-trigger-mean",
        type=float,
        default=config.XPL_LOCAL_COARSE_TRIGGER_MEAN,
        help=f"Stop coarse and enter fine scan once ROI mean is <= this value. Default: {config.XPL_LOCAL_COARSE_TRIGGER_MEAN:g}",
    )
    parser.add_argument("--fine-radius", type=int, default=10, help="Fallback fine scan radius around best coarse angle.")
    parser.add_argument("--fine-step", type=int, default=1, help="Fine scan step in degrees.")
    parser.add_argument(
        "--fine-max-span",
        type=int,
        default=config.XPL_LOCAL_FINE_MAX_SPAN_DEG,
        help=f"Maximum directional fine scan span from the fine start angle. Default: {config.XPL_LOCAL_FINE_MAX_SPAN_DEG}",
    )
    parser.add_argument(
        "--fine-stop-increase-count",
        type=int,
        default=config.XPL_LOCAL_FINE_STOP_INCREASE_COUNT,
        help=f"Stop fine scan after this many consecutive increases after best. Default: {config.XPL_LOCAL_FINE_STOP_INCREASE_COUNT}",
    )
    parser.add_argument(
        "--fine-stop-ratio",
        type=float,
        default=config.XPL_LOCAL_FINE_STOP_RATIO,
        help=f"Stop fine scan once signal exceeds best by this ratio. Default: {config.XPL_LOCAL_FINE_STOP_RATIO:g}",
    )
    parser.add_argument(
        "--confirm-max-mean",
        type=float,
        default=config.XPL_LOCAL_CONFIRMATION_MAX_MEAN,
        help=f"Confirmation must be <= this ROI mean. Default: {config.XPL_LOCAL_CONFIRMATION_MAX_MEAN:g}",
    )
    parser.add_argument(
        "--confirm-ratio",
        "--confirm-max-ratio",
        dest="confirm_max_ratio",
        type=float,
        default=config.XPL_LOCAL_CONFIRMATION_MAX_RATIO,
        help=f"Confirmation must be <= best scan mean times this ratio. Default: {config.XPL_LOCAL_CONFIRMATION_MAX_RATIO:g}",
    )
    parser.add_argument(
        "--warning-mean",
        type=float,
        default=config.XPL_LOCAL_CONFIRMATION_MAX_MEAN,
        help=f"Print warning when confirmation ROI mean is above this value. Default: {config.XPL_LOCAL_CONFIRMATION_MAX_MEAN:g}",
    )
    parser.add_argument(
        "--final-approach-offset",
        type=int,
        default=config.XPL_LOCAL_CALIBRATION_FINAL_APPROACH_OFFSET_DEG,
        help=f"Approach final angle from this many degrees below. Default: {config.XPL_LOCAL_CALIBRATION_FINAL_APPROACH_OFFSET_DEG}",
    )
    parser.add_argument("--settling-s", type=float, default=config.SETTLING_TIME_S)
    parser.add_argument("--max-refine-rounds", type=int, default=2)
    parser.add_argument(
        "--no-start-home",
        action="store_true",
        help="Skip the initial HOME. By default, the test starts from HOME before adaptive calibration.",
    )
    parser.add_argument(
        "--no-arm-reset",
        action="store_true",
        help="Do not send RESET after connecting to Arduino.",
    )
    return parser.parse_args()


def fill_interactive_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.port is not None:
        return args

    print("APIS Adaptive XPL Search Test")
    print()
    print("This keeps sample at 0 deg, scans the background ROI, then confirms the chosen XPL angle.")
    print()
    args.port = prompt_value("Arduino COM port", "COM8")
    args.output = prompt_value("Output parent folder", str(DEFAULT_OUTPUT_PARENT))
    args.runs = int(prompt_value("Runs", str(args.runs)))
    args.coarse_angles = prompt_value("Coarse angles", args.coarse_angles)
    args.sample_angle = int(prompt_value("Sample angle", str(args.sample_angle)))
    args.exposure_us = int(prompt_value("Exposure us", str(args.exposure_us)))
    args.roi_top_y = int(prompt_value("Top ROI y", str(args.roi_top_y)))
    args.roi_bottom_y = int(prompt_value("Bottom ROI y", str(args.roi_bottom_y)))
    args.settling_s = float(prompt_value("Settling seconds", str(args.settling_s)))
    return args


def prepare_run_dir(output_parent: str | None) -> Path:
    parent = Path(output_parent) if output_parent else DEFAULT_OUTPUT_PARENT
    run_dir = parent / f"adaptive_xpl_search_{utils.get_timestamp_file()}"
    utils.ensure_dir(str(run_dir))
    utils.ensure_dir(str(run_dir / "images"))
    return run_dir


def build_roi_specs(args: argparse.Namespace) -> list[dict]:
    if args.roi_y is not None:
        return [
            {
                "name": "single",
                "rect": (args.roi_x, args.roi_y, args.roi_width, args.roi_height),
            }
        ]
    return [
        {
            "name": "bottom",
            "rect": (args.roi_x, args.roi_bottom_y, args.roi_width, args.roi_height),
        },
        {
            "name": "top",
            "rect": (args.roi_x, args.roi_top_y, args.roi_width, args.roi_height),
        },
    ]


def roi_metadata(roi_rect: tuple[int, int, int, int]) -> dict:
    return {
        "x": int(roi_rect[0]),
        "y": int(roi_rect[1]),
        "width": int(roi_rect[2]),
        "height": int(roi_rect[3]),
    }


def compute_stats(img: np.ndarray, roi_specs: list[dict], active_roi_name: str | None = None) -> dict:
    arr = np.asarray(img)
    if arr.ndim == 3:
        arr = arr.mean(axis=2)
    arr = arr.astype(np.float32)

    roi_stats = {}
    roi_signals = {}
    image_height, image_width = arr.shape[:2]
    for spec in roi_specs:
        x, y, width, height = spec["rect"]
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > image_width or y + height > image_height:
            raise ValueError(
                "Background ROI is outside image bounds: "
                f"roi=(x={x}, y={y}, w={width}, h={height}), "
                f"image=(w={image_width}, h={image_height})"
            )
        roi = arr[y:y + height, x:x + width]
        prefix = spec["name"]
        roi_mean = float(roi.mean())
        roi_signals[prefix] = roi_mean
        roi_stats[f"{prefix}_roi_mean"] = roi_mean
        roi_stats[f"{prefix}_roi_std"] = float(roi.std())
        roi_stats[f"{prefix}_roi_min"] = float(roi.min())
        roi_stats[f"{prefix}_roi_max"] = float(roi.max())

    if active_roi_name is None:
        selected_roi = min(roi_specs, key=lambda spec: (roi_signals[spec["name"]], roi_specs.index(spec)))
    else:
        selected_roi = next((spec for spec in roi_specs if spec["name"] == active_roi_name), None)
        if selected_roi is None:
            raise ValueError(f"Unknown ROI name: {active_roi_name}")

    selected_name = selected_roi["name"]
    return {
        "full_mean": float(arr.mean()),
        "full_std": float(arr.std()),
        "full_min": float(arr.min()),
        "full_max": float(arr.max()),
        "selected_roi_name": selected_name,
        "roi_mean": roi_stats[f"{selected_name}_roi_mean"],
        "roi_std": roi_stats[f"{selected_name}_roi_std"],
        "roi_min": roi_stats[f"{selected_name}_roi_min"],
        "roi_max": roi_stats[f"{selected_name}_roi_max"],
        **roi_stats,
    }


def save_rows(csv_path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def capture_at_angle(
    *,
    ctrl: PicsController,
    cam: XimeaCamera,
    angle: int,
    phase: str,
    run_dir: Path,
    roi_specs: list[dict],
    rows: list[dict],
    settling_s: float,
    active_roi_name: str | None = None,
    note: str = "",
) -> dict:
    print(f"  {phase}: polarizer {angle} deg")
    if not ctrl.rotate_polarizer(angle):
        raise RuntimeError(f"Polarizer move failed: {angle}")
    time.sleep(settling_s)

    img = cam.capture()
    if img is None:
        raise RuntimeError("Camera returned no image.")

    filename = f"{phase}_{angle:03d}_{len(rows) + 1:03d}.tif"
    image_path = run_dir / "images" / filename
    if not io.save_image(str(image_path), img):
        raise IOError(f"Failed to save image: {image_path}")

    row = {
        "index": len(rows) + 1,
        "timestamp": utils.get_timestamp_iso(),
        "phase": phase,
        "angle_deg": angle,
        "note": note,
        "filepath": str(image_path),
        **compute_stats(img, roi_specs, active_roi_name),
    }
    rows.append(row)
    roi_parts = []
    for spec in roi_specs:
        roi_name = spec["name"]
        roi_parts.append(f"{roi_name}={row[f'{roi_name}_roi_mean']:.2f}")
    print(
        f"    {row['selected_roi_name']} ROI mean={row['roi_mean']:.2f} "
        f"({', '.join(roi_parts)}), full mean={row['full_mean']:.2f}"
    )
    return row


def scan_angles(
    *,
    ctrl: PicsController,
    cam: XimeaCamera,
    angles: list[int],
    phase: str,
    run_dir: Path,
    roi_specs: list[dict],
    rows: list[dict],
    settling_s: float,
    active_roi_name: str | None = None,
) -> list[dict]:
    results = []
    for angle in angles:
        results.append(
            capture_at_angle(
                ctrl=ctrl,
                cam=cam,
                angle=angle,
                phase=phase,
                run_dir=run_dir,
                roi_specs=roi_specs,
                rows=rows,
                settling_s=settling_s,
                active_roi_name=active_roi_name,
            )
        )
    return results


def choose_best(results: list[dict]) -> dict:
    return min(results, key=lambda row: (row["roi_mean"], row["angle_deg"]))


def coarse_scan_until_trigger(
    *,
    ctrl: PicsController,
    cam: XimeaCamera,
    angles: list[int],
    args: argparse.Namespace,
    run_dir: Path,
    roi_specs: list[dict],
    rows: list[dict],
) -> tuple[list[dict], int, dict | None, str]:
    results = []
    trigger = None
    previous_angle = None
    for angle in angles:
        result = capture_at_angle(
            ctrl=ctrl,
            cam=cam,
            angle=angle,
            phase="coarse",
            run_dir=run_dir,
            roi_specs=roi_specs,
            rows=rows,
            settling_s=args.settling_s,
        )
        results.append(result)
        if result["roi_mean"] <= args.coarse_trigger_mean:
            trigger = result
            print(
                f"Coarse trigger at {angle} deg, "
                f"{result['selected_roi_name']} ROI mean={result['roi_mean']:.2f}"
            )
            break
        previous_angle = angle

    if trigger is None:
        best = choose_best(results)
        active_roi_name = best["selected_roi_name"]
        fine_start = max(config.POLARIZER_STAGE_MIN_ANGLE, best["angle_deg"] - args.fine_radius)
        print(
            f"Coarse trigger not reached; fallback fine start={fine_start} "
            f"from best coarse {best['angle_deg']} using {active_roi_name} ROI"
        )
    else:
        active_roi_name = trigger["selected_roi_name"]
        fine_start = previous_angle if previous_angle is not None else trigger["angle_deg"]
    return results, fine_start, trigger, active_roi_name


def directional_fine_scan_until_trend_change(
    *,
    ctrl: PicsController,
    cam: XimeaCamera,
    fine_start: int,
    args: argparse.Namespace,
    run_dir: Path,
    roi_specs: list[dict],
    rows: list[dict],
    active_roi_name: str,
    phase: str,
) -> list[dict]:
    fine_end = min(config.POLARIZER_STAGE_MAX_ANGLE, int(fine_start) + int(args.fine_max_span))
    results = []
    best = None
    previous_signal = None
    consecutive_increases = 0

    for angle in range(int(fine_start), fine_end + 1, int(args.fine_step)):
        result = capture_at_angle(
            ctrl=ctrl,
            cam=cam,
            angle=angle,
            phase=phase,
            run_dir=run_dir,
            roi_specs=roi_specs,
            rows=rows,
            settling_s=args.settling_s,
            active_roi_name=active_roi_name,
        )
        results.append(result)
        signal = result["roi_mean"]

        if best is None or signal < best["roi_mean"]:
            best = result
            consecutive_increases = 0
        elif previous_signal is not None and signal > previous_signal:
            consecutive_increases += 1
        else:
            consecutive_increases = 0

        if best is not None and result["angle_deg"] > best["angle_deg"]:
            if consecutive_increases >= args.fine_stop_increase_count:
                print(f"Fine scan stopped after {consecutive_increases} consecutive increases")
                break
            if signal > best["roi_mean"] * args.fine_stop_ratio:
                print(f"Fine scan stopped after exceeding best by {args.fine_stop_ratio:.2f}x")
                break

        previous_signal = signal

    return results


def final_approach_and_confirm(
    *,
    ctrl: PicsController,
    cam: XimeaCamera,
    target_angle: int,
    best_scan_mean: float,
    args: argparse.Namespace,
    run_dir: Path,
    roi_specs: list[dict],
    rows: list[dict],
    active_roi_name: str,
    phase: str,
) -> dict:
    approach_angle = max(config.POLARIZER_STAGE_MIN_ANGLE, target_angle - args.final_approach_offset)
    if approach_angle != target_angle:
        print(f"  final approach: {approach_angle} -> {target_angle} deg")
        if not ctrl.rotate_polarizer(approach_angle):
            raise RuntimeError(f"Polarizer final approach preload failed: {approach_angle}")
        time.sleep(args.settling_s)

    confirm = capture_at_angle(
        ctrl=ctrl,
        cam=cam,
        angle=target_angle,
        phase=phase,
        run_dir=run_dir,
        roi_specs=roi_specs,
        rows=rows,
        settling_s=args.settling_s,
        active_roi_name=active_roi_name,
        note=f"best_scan_mean={best_scan_mean:.6f}",
    )
    confirm["confirm_ratio_to_best"] = confirm["roi_mean"] / best_scan_mean if best_scan_mean else None
    confirm["confirmation_ok"] = (
        confirm["roi_mean"] <= args.confirm_max_mean
        and confirm["confirm_ratio_to_best"] is not None
        and confirm["confirm_ratio_to_best"] <= args.confirm_max_ratio
    )
    return confirm


def is_edge_best(best: dict, results: list[dict]) -> int:
    angles = [row["angle_deg"] for row in results]
    if best["angle_deg"] == min(angles):
        return -1
    if best["angle_deg"] == max(angles):
        return 1
    return 0


def run_search(args: argparse.Namespace) -> dict:
    coarse_angles = parse_angle_list(args.coarse_angles)
    roi_specs = build_roi_specs(args)
    run_dir = prepare_run_dir(args.output)
    csv_path = run_dir / "adaptive_xpl_search_log.csv"
    metadata_path = run_dir / "adaptive_xpl_search_metadata.json"

    ctrl = PicsController()
    cam = XimeaCamera()
    rows = []
    metadata = {
        "run_dir": str(run_dir),
        "csv_path": str(csv_path),
        "rois": {spec["name"]: roi_metadata(spec["rect"]) for spec in roi_specs},
        "coarse_angles_deg": coarse_angles,
        "coarse_trigger_mean": args.coarse_trigger_mean,
        "sample_angle_deg": args.sample_angle,
        "exposure_us": args.exposure_us,
        "settling_s": args.settling_s,
        "fine_max_span_deg": args.fine_max_span,
        "fine_stop_increase_count": args.fine_stop_increase_count,
        "fine_stop_ratio": args.fine_stop_ratio,
        "final_approach_offset_deg": args.final_approach_offset,
        "confirm_max_mean": args.confirm_max_mean,
        "confirm_max_ratio": args.confirm_max_ratio,
        "warning_mean": args.warning_mean,
    }

    try:
        print(f"Connecting Arduino on {args.port}...")
        ctrl.connect(args.port)
        if not args.no_arm_reset:
            print("Sending Arduino RESET / ARM...")
            if not ctrl.reset():
                raise RuntimeError("Arduino RESET failed.")

        if not args.no_start_home:
            print("Starting from HOME...")
            if not ctrl.home():
                raise RuntimeError("HOME failed.")
            time.sleep(args.settling_s)

        print("Opening XIMEA camera in RAW16 sequence mode...")
        cam.open()
        cam.configure_sequence_mode()
        cam.set_exposure(args.exposure_us)
        cam.set_gain(0.0)

        print(f"Moving sample to {args.sample_angle} deg...")
        if not ctrl.rotate_sample(args.sample_angle):
            raise RuntimeError(f"Sample move failed: {args.sample_angle}")
        time.sleep(args.settling_s)

        print("\nCoarse scan")
        coarse_results, fine_start, coarse_trigger, active_roi_name = coarse_scan_until_trigger(
            ctrl=ctrl,
            cam=cam,
            angles=coarse_angles,
            args=args,
            run_dir=run_dir,
            roi_specs=roi_specs,
            rows=rows,
        )
        best_coarse = choose_best(coarse_results)
        metadata["best_coarse_angle_deg"] = best_coarse["angle_deg"]
        metadata["best_coarse_roi_mean"] = best_coarse["roi_mean"]
        metadata["best_coarse_roi_name"] = best_coarse["selected_roi_name"]
        metadata["coarse_trigger_angle_deg"] = coarse_trigger["angle_deg"] if coarse_trigger else None
        metadata["coarse_trigger_roi_name"] = coarse_trigger["selected_roi_name"] if coarse_trigger else None
        metadata["selected_background_roi_name"] = active_roi_name
        metadata["fine_start_angle_deg"] = fine_start
        print(
            f"Best coarse: {best_coarse['angle_deg']} deg, "
            f"{best_coarse['selected_roi_name']} ROI mean={best_coarse['roi_mean']:.2f}"
        )
        print(f"Focusing fine/confirmation on {active_roi_name} ROI")

        print(f"\nFine scan from {fine_start} deg")
        fine_results = directional_fine_scan_until_trend_change(
            ctrl=ctrl,
            cam=cam,
            fine_start=fine_start,
            args=args,
            run_dir=run_dir,
            roi_specs=roi_specs,
            rows=rows,
            active_roi_name=active_roi_name,
            phase="fine",
        )
        all_search_results = coarse_results + fine_results
        best = choose_best(all_search_results)

        confirmations = []
        for refine_round in range(args.max_refine_rounds + 1):
            print(f"\nConfirmation round {refine_round + 1}")
            confirm = final_approach_and_confirm(
                ctrl=ctrl,
                cam=cam,
                target_angle=best["angle_deg"],
                best_scan_mean=best["roi_mean"],
                args=args,
                run_dir=run_dir,
                roi_specs=roi_specs,
                rows=rows,
                active_roi_name=active_roi_name,
                phase=f"confirm_r{refine_round + 1}",
            )
            confirmations.append(confirm)
            print(
                f"Confirmation ROI mean={confirm['roi_mean']:.2f}, "
                f"ratio={confirm['confirm_ratio_to_best']:.3f}, "
                f"ok={confirm['confirmation_ok']}"
            )
            if confirm["confirmation_ok"]:
                break

            if refine_round >= args.max_refine_rounds:
                break

            print("\nConfirmation failed; rescanning +/-5 deg around current best")
            refine_angles = build_angle_window(best["angle_deg"], 5, args.fine_step)
            refine_results = scan_angles(
                ctrl=ctrl,
                cam=cam,
                angles=refine_angles,
                phase=f"refine_r{refine_round + 1}",
                run_dir=run_dir,
                roi_specs=roi_specs,
                rows=rows,
                settling_s=args.settling_s,
                active_roi_name=active_roi_name,
            )
            all_search_results.extend(refine_results)
            best = choose_best(refine_results)

        final_confirm = confirmations[-1]
        metadata.update(
            {
                "selected_xpl_angle_deg": best["angle_deg"],
                "selected_background_roi_name": active_roi_name,
                "selected_scan_roi_mean": best["roi_mean"],
                "confirmation_roi_mean": final_confirm["roi_mean"],
                "confirmation_ok": final_confirm["confirmation_ok"],
                "confirmation_ratio_to_best": final_confirm["confirm_ratio_to_best"],
                "warning_roi_mean_exceeded": final_confirm["roi_mean"] > args.warning_mean,
                "rows": rows,
            }
        )

        save_rows(csv_path, rows)
        io.save_json(str(metadata_path), metadata)
        return metadata

    finally:
        try:
            cam.close()
        except Exception:
            pass
        try:
            ctrl.disconnect()
        except Exception:
            pass


def run_repeated_searches(args: argparse.Namespace) -> list[dict]:
    if args.runs <= 0:
        raise ValueError("runs must be greater than 0.")

    results = []
    for run_idx in range(1, args.runs + 1):
        print()
        print(f"=== Adaptive XPL Search Run {run_idx}/{args.runs} ===")
        result = run_search(args)
        result["run_index"] = run_idx
        results.append(result)

    if len(results) > 1:
        output_parent = Path(args.output) if args.output else DEFAULT_OUTPUT_PARENT
        summary_path = output_parent / f"adaptive_xpl_search_repeat_summary_{utils.get_timestamp_file()}.csv"
        rows = []
        for result in results:
            rows.append(
                {
                    "run_index": result["run_index"],
                    "run_dir": result["run_dir"],
                    "selected_xpl_angle_deg": result["selected_xpl_angle_deg"],
                    "selected_background_roi_name": result["selected_background_roi_name"],
                    "selected_scan_roi_mean": result["selected_scan_roi_mean"],
                    "confirmation_roi_mean": result["confirmation_roi_mean"],
                    "confirmation_ok": result["confirmation_ok"],
                    "confirmation_ratio_to_best": result["confirmation_ratio_to_best"],
                    "warning_roi_mean_exceeded": result["warning_roi_mean_exceeded"],
                }
            )
        save_rows(summary_path, rows)

        selected_angles = [result["selected_xpl_angle_deg"] for result in results]
        confirmation_means = [result["confirmation_roi_mean"] for result in results]
        print()
        print("Repeat summary")
        print(f"Summary CSV: {summary_path}")
        print(
            "Selected XPL range: "
            f"{min(selected_angles)} -> {max(selected_angles)} deg "
            f"(span={max(selected_angles) - min(selected_angles)} deg)"
        )
        print(
            "Confirmation ROI mean range: "
            f"{min(confirmation_means):.2f} -> {max(confirmation_means):.2f} "
            f"(span={max(confirmation_means) - min(confirmation_means):.2f})"
        )

    return results


def main() -> None:
    interactive = len(sys.argv) == 1
    try:
        args = fill_interactive_args(parse_args())
        results = run_repeated_searches(args)
        metadata = results[-1]
        print()
        print("Done.")
        print(f"Output: {metadata['run_dir']}")
        print(f"CSV: {metadata['csv_path']}")
        print(
            "Selected XPL: "
            f"{metadata['selected_xpl_angle_deg']} deg, "
            f"ROI={metadata['selected_background_roi_name']}, "
            f"scan ROI mean={metadata['selected_scan_roi_mean']:.2f}, "
            f"confirmation ROI mean={metadata['confirmation_roi_mean']:.2f}, "
            f"ok={metadata['confirmation_ok']}"
        )
        if metadata["warning_roi_mean_exceeded"]:
            print(f"WARNING: confirmation ROI mean exceeded {metadata['warning_mean']:.2f}")
    except Exception as exc:
        print(f"Error: {exc}")
        raise SystemExit(1)
    finally:
        if interactive:
            try:
                input("\nPress Enter to close...")
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
