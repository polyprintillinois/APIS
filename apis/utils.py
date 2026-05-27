import os
import datetime
import re

def get_timestamp_iso():
    """Return current timestamp in ISO 8601 format."""
    return datetime.datetime.now().isoformat()

def get_timestamp_file():
    """Return timestamp suitable for filenames (YYYYMMDD_HHMMSS)."""
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def ensure_dir(directory):
    """Ensure a directory exists."""
    if not os.path.exists(directory):
        os.makedirs(directory)

def sanitize_filename(name):
    """Sanitize string for usage as filename."""
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def format_command(pp, aaa):
    """
    Format command string PPAAA.
    pp: int (2 digits)
    aaa: int (3 digits)
    Returns string 'PPAAA'
    """
    return f"{pp:02d}{aaa:03d}"


def choose_orthogonal_polarizer_angle(xpl_angle, min_angle, max_angle, prefer_positive=True):
    """
    Choose an orthogonal polarizer angle that stays inside the reachable stage range.

    Candidates are xpl_angle +/- 90 deg. If both are valid, prefer +90 by default.
    Returns (ppl_angle, offset_deg).
    """
    xpl_angle = int(xpl_angle)
    min_angle = int(min_angle)
    max_angle = int(max_angle)

    candidates = []
    offsets = (90, -90) if prefer_positive else (-90, 90)
    for offset in offsets:
        candidate = xpl_angle + offset
        if min_angle <= candidate <= max_angle:
            candidates.append((candidate, offset))

    if not candidates:
        raise ValueError(
            f"No valid PPL angle for XPL={xpl_angle} deg within range {min_angle}-{max_angle}."
        )

    return candidates[0]


def derive_ppl_angle_from_xpl(xpl_angle, min_angle, max_angle, prefer_positive=True):
    """Return the reachable orthogonal PPL angle for a given XPL angle."""
    ppl_angle, _ = choose_orthogonal_polarizer_angle(
        xpl_angle,
        min_angle,
        max_angle,
        prefer_positive=prefer_positive,
    )
    return ppl_angle


def build_angle_window(center_angle, radius, min_angle, max_angle, step=1):
    """Build an inclusive angle window clipped to the calibrated stage limits."""
    start = max(min_angle, int(center_angle) - int(radius))
    end = min(max_angle, int(center_angle) + int(radius))
    return list(range(start, end + 1, int(step)))
