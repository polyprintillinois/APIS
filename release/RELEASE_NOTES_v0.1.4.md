# APIS v0.1.4 Release Notes

## Summary

This release simplifies the packaged distribution to the APIS app and improves XPL acquisition stability with adaptive top/bottom background ROI selection.

## Added

- Adaptive local XPL search now checks both the top and bottom background ROIs:
  - bottom: `x=700, y=1000, width=700, height=80 px`
  - top: `x=700, y=0, width=700, height=80 px`
- Calibration metadata records both ROI signals, the selected ROI, and the confirmation result.
- CSV logs store calibration-specific details in `details_json`.
- `scripts/test_adaptive_xpl_search.py` now mirrors the app's top/bottom ROI search, with legacy single-ROI mode available via `--roi-y`.

## Changed

- Polarizer calibration now reuses the same adaptive local XPL search path used before XPL sequence capture.
- Polarizer calibration uses a single XPL-style exposure setting instead of separate coarse/fine exposure controls.
- The confirmation threshold is now `<=65` ROI mean while still requiring `<=1.2x` of the selected scan minimum.
- Sequence XPL capture starts from `HOME`, moves the sample stage to `0 deg`, runs adaptive XPL search, then keeps the selected polarizer angle fixed while imaging.
- Sequence cleanup returns the sample stage to `0 deg` without homing/resetting the polarizer.
- Long calibration status messages no longer resize the app window; full messages remain available in the log and status tooltip.
- The packaged release now contains only the APIS app plus release documentation.
- RAW16 preview conversion remains available inside the APIS app for the selected folder and its immediate child folders.

## Removed

- Removed the separate packaged batch RAW16 conversion executable.
- Removed the polarizer repeatability diagnostic executable/script.
- Removed obsolete coarse/fine polarizer calibration API naming.

## Validation

- `python -m compileall app apis scripts`
- `python -m unittest tests.test_ximea_raw16_sequence`
- `.\build\build.ps1 -Version 0.1.4`

## Notes

- Sequence capture remains `RAW16` only.
- Reusing the same `SampleID` may append to the existing CSV log.
