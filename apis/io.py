import os
import csv
import json
import logging
from . import utils
from . import config

# Try importing OpenCV for image saving
try:
    import cv2
    import numpy as np
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    logging.warning("OpenCV not found. Image saving may fail if not handled by caller.")

def save_image(filepath, image_data, color_mode=None):
    """
    Save image_data (numpy array) to filepath (TIFF).
    color_mode: None (no conversion), "rgb" (convert RGB->BGR), "bgr" (no-op).
    """
    if not HAS_OPENCV:
        raise ImportError("OpenCV (cv2) is required to save images.")
    
    # Ensure directory
    directory = os.path.dirname(filepath)
    if directory:
        utils.ensure_dir(directory)

    # Validate image data
    if image_data is None or image_data.size == 0:
        logging.error(f"Cannot save image: Data is None or empty. File: {filepath}")
        return False
        
    # check extension
    if not filepath.lower().endswith(('.tif', '.tiff')):
        filepath += ".tif"
        
    # Optional color conversion for OpenCV (expects BGR)
    if color_mode == "rgb" and image_data.ndim == 3 and image_data.shape[2] == 3:
        image_data = cv2.cvtColor(image_data, cv2.COLOR_RGB2BGR)

    # Save (OpenCV handles TIFF if extension is .tif)
    try:
        success = cv2.imwrite(filepath, image_data)
        if not success:
            raise IOError(f"cv2.imwrite failed for {filepath}")
        logging.info(f"Saved image: {filepath}")
        return True
    except Exception as e:
        logging.error(f"Failed to save image: {e}")
        return False

def append_to_log(log_path, data_dict):
    """
    Append a dictionary row to a CSV log file.
    Automatically writes header if file doesn't exist.
    """
    file_exists = os.path.isfile(log_path)
    
    utils.ensure_dir(os.path.dirname(log_path))
    
    fieldnames = [
        "timestamp", "mode", "exposure_us", "gain", 
        "polarizer_angle", "sample_angle", "filepath", 
        "arduino_response", "attempt_count", "signal_mean"
    ]
    
    try:
        with open(log_path, mode='a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            if not file_exists:
                writer.writeheader()
                
            # Filter data_dict to only known fields to avoid errors
            row = {k: data_dict.get(k, "") for k in fieldnames}
            writer.writerow(row)
            return True
            
    except Exception as e:
        logging.error(f"Failed to write log {log_path}: {e}")
        return False

def save_json(filepath, data):
    """Save structured metadata as JSON."""
    directory = os.path.dirname(filepath)
    if directory:
        utils.ensure_dir(directory)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logging.info(f"Saved JSON: {filepath}")
        return True
    except Exception as e:
        logging.error(f"Failed to save JSON {filepath}: {e}")
        return False

def convert_raw16_tree_to_rgb_preview(
    input_dir,
    *,
    raw_max_value=1023.0,
    gamma=1.0 / 2.2,
    wb_gains=None,
):
    """
    Convert RAW16 Bayer TIFFs in input_dir and its immediate child directories to RGB8 previews.

    Processing rules:
    - input_dir/*.tif(f)
    - input_dir/<child>/*.tif(f)
    - deeper subdirectories are ignored
    - directories ending with '_rgb' are skipped
    - only uint16 2D images are converted
    """
    if not HAS_OPENCV:
        raise ImportError("OpenCV (cv2) is required for RAW16 conversion.")

    source_dir = os.path.abspath(input_dir)
    if not os.path.isdir(source_dir):
        raise FileNotFoundError(f"Input directory does not exist: {source_dir}")
    if source_dir.lower().endswith("_rgb"):
        raise ValueError("Select the RAW source directory, not an existing _rgb output directory.")

    if wb_gains is None:
        wb_gains = (config.XIMEA_WB_KR, config.XIMEA_WB_KG, config.XIMEA_WB_KB)

    parent_dir = os.path.dirname(source_dir)
    folder_name = os.path.basename(source_dir.rstrip("\\/"))
    output_dir = os.path.join(parent_dir, f"{folder_name}_rgb")

    summary = {
        "input_dir": source_dir,
        "output_dir": output_dir,
        "converted": [],
        "skipped": [],
        "errors": [],
    }

    scan_dirs = [source_dir]
    with os.scandir(source_dir) as root_entries:
        for entry in root_entries:
            if entry.is_dir() and not entry.name.lower().endswith("_rgb"):
                scan_dirs.append(entry.path)

    for scan_dir in scan_dirs:
        rel_dir = os.path.relpath(scan_dir, source_dir)
        target_dir = output_dir if rel_dir == "." else os.path.join(output_dir, rel_dir)

        with os.scandir(scan_dir) as entries:
            for entry in sorted(entries, key=lambda e: e.name.lower()):
                if not entry.is_file():
                    continue
                if not entry.name.lower().endswith((".tif", ".tiff")):
                    continue
                if entry.name.lower().endswith("_rgb.tif") or entry.name.lower().endswith("_rgb.tiff"):
                    summary["skipped"].append({"path": entry.path, "reason": "rgb_suffix"})
                    continue

                image_data = cv2.imread(entry.path, cv2.IMREAD_UNCHANGED)
                if image_data is None:
                    summary["errors"].append({"path": entry.path, "reason": "read_failed"})
                    continue
                if image_data.ndim != 2 or image_data.dtype != np.uint16:
                    summary["skipped"].append({"path": entry.path, "reason": "not_raw16_2d"})
                    continue

                try:
                    # Empirically, XIMEA MQ022CG-CM GBRG RAW previews look correct
                    # with OpenCV's GB2BGR conversion in this preview-only path.
                    rgb16 = cv2.cvtColor(image_data, cv2.COLOR_BAYER_GB2BGR)
                    rgb = rgb16.astype(np.float32) / float(raw_max_value)
                    rgb[..., 0] *= wb_gains[0]
                    rgb[..., 1] *= wb_gains[1]
                    rgb[..., 2] *= wb_gains[2]
                    rgb = np.clip(rgb, 0.0, 1.0)
                    rgb = np.power(rgb, gamma)
                    rgb8 = np.clip(np.round(rgb * 255.0), 0, 255).astype(np.uint8)

                    stem, ext = os.path.splitext(entry.name)
                    output_path = os.path.join(target_dir, f"{stem}_rgb{ext}")
                    if not save_image(output_path, rgb8, color_mode="rgb"):
                        raise IOError(f"Failed to save RGB preview: {output_path}")

                    summary["converted"].append(
                        {
                            "input_path": entry.path,
                            "output_path": output_path,
                        }
                    )
                except Exception as e:
                    summary["errors"].append({"path": entry.path, "reason": str(e)})

    return summary
