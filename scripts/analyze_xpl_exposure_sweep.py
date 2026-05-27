import argparse
from pathlib import Path

import cv2
import numpy as np


def load_raw16(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError(f"Failed to load image: {path}")
    if img.ndim != 2 or img.dtype != np.uint16:
        raise RuntimeError(f"Expected RAW16 2D TIFF, got shape={img.shape}, dtype={img.dtype} for {path}")
    return img


def compute_stats(img: np.ndarray, raw_max_value: float) -> dict:
    arr = img.astype(np.float32)
    sat_threshold = raw_max_value * 0.98
    dark_threshold = raw_max_value * 0.02
    p01, p05, p50, p95, p99 = np.percentile(arr, [1, 5, 50, 95, 99])
    return {
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "p01": float(p01),
        "p05": float(p05),
        "p50": float(p50),
        "p95": float(p95),
        "p99": float(p99),
        "dynamic_range": float(p99 - p01),
        "sat_pct": float((arr >= sat_threshold).mean() * 100.0),
        "dark_pct": float((arr <= dark_threshold).mean() * 100.0),
    }


def collect_images(folder: Path, recursive: bool) -> list[Path]:
    if recursive:
        paths = sorted(p for p in folder.rglob("*.tif") if p.is_file())
        paths.extend(sorted(p for p in folder.rglob("*.tiff") if p.is_file()))
    else:
        paths = sorted(p for p in folder.glob("*.tif") if p.is_file())
        paths.extend(sorted(p for p in folder.glob("*.tiff") if p.is_file()))
    seen = set()
    unique_paths = []
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique_paths.append(path)
    return unique_paths


def print_table(rows: list[dict], root: Path) -> None:
    headers = [
        "file",
        "mean",
        "std",
        "p95",
        "p99",
        "max",
        "range99",
        "sat%",
        "dark%",
    ]
    formatted = []
    for row in rows:
        formatted.append(
            {
                "file": str(row["path"].relative_to(root)),
                "mean": f"{row['mean']:.1f}",
                "std": f"{row['std']:.1f}",
                "p95": f"{row['p95']:.1f}",
                "p99": f"{row['p99']:.1f}",
                "max": f"{row['max']:.1f}",
                "range99": f"{row['dynamic_range']:.1f}",
                "sat%": f"{row['sat_pct']:.3f}",
                "dark%": f"{row['dark_pct']:.3f}",
            }
        )

    widths = {header: len(header) for header in headers}
    for row in formatted:
        for header in headers:
            widths[header] = max(widths[header], len(row[header]))

    print(" ".join(header.ljust(widths[header]) for header in headers))
    print(" ".join("-" * widths[header] for header in headers))
    for row in formatted:
        print(" ".join(row[header].ljust(widths[header]) for header in headers))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize RAW16 TIFF exposure sweeps to compare saturation, darkness, and usable contrast."
    )
    parser.add_argument("folder", help="Folder containing RAW16 TIFFs.")
    parser.add_argument(
        "--raw-max",
        type=float,
        default=1023.0,
        help="Sensor white level used to estimate saturation/dark percentages. Default: 1023",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Scan subfolders recursively.",
    )
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        raise SystemExit(f"Folder does not exist: {root}")

    image_paths = collect_images(root, args.recursive)
    if not image_paths:
        raise SystemExit("No TIFF images found.")

    rows = []
    for path in image_paths:
        img = load_raw16(path)
        stats = compute_stats(img, args.raw_max)
        stats["path"] = path
        rows.append(stats)

    print_table(rows, root)


if __name__ == "__main__":
    main()
