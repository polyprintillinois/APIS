# APIS v0.1.3 Release Notes

## Highlights

- Renamed polarization modes from `normal/crosspol` to `PPL/XPL` across the app, sequence flow, and documentation.
- Added configurable polarizer baselines with default acquisition angles `PPL = 5 deg` and `XPL = 95 deg`.
- Updated XPL/PPL sequence handling so PPL is derived from the reachable orthogonal angle relative to XPL.
- Added polarizer calibration workflow that finds the darkest XPL angle and saves the calibrated baseline for reuse.
- Separated default exposures by use case:
  - PPL acquisition: `18000 us`
  - XPL acquisition: `500000 us`
  - Polarizer calibration: `200000 us`
- Added `scripts/analyze_xpl_exposure_sweep.py` to compare RAW16 XPL exposure sweep datasets.
- Hardened Windows release packaging to stage files before zipping and avoid transient file-lock failures during archive creation.

## Behavior Changes

- Sequence output folders now use `ppl` and `xpl` names.
- Sequence metadata and CSV logs include updated mode naming and calibration signal metrics.
- The app restores the saved polarizer baseline on startup when calibration data is available.
- Distribution baseline documentation now matches the current acquisition defaults and polarizer baseline.

## Verification

- `python -m py_compile apis\\config.py apis\\io.py apis\\sequence.py apis\\utils.py app\\main.py app\\workers.py scripts\\analyze_xpl_exposure_sweep.py tests\\test_ximea_raw16_sequence.py`
- `python -m unittest tests.test_ximea_raw16_sequence tests.test_mock_serial`
- `.\build\build.ps1 -Version 0.1.3`
