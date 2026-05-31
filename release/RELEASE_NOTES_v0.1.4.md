# APIS v0.1.4 Release Notes

## Summary

This release updates the XPL calibration/search workflow so APIS can handle film that is not centered vertically, and tightens the app UI around the sequence controls.

## Changed Since v0.1.3

- Local XPL search now checks both background ROIs during the coarse scan:
  - bottom ROI: `x=700, y=1000, width=700, height=80 px`
  - top ROI: `x=700, y=0, width=700, height=80 px`
- Fine XPL search and confirmation now focus on whichever ROI produced the darker signal.
- XPL confirmation now requires ROI mean `<=65` and `<=1.2x` of the selected scan minimum.
- Polarizer calibration now uses the same adaptive local XPL search path as sequence capture.
- Polarizer calibration now uses one calibration exposure setting instead of separate coarse/fine exposure settings.
- Calibration metadata and CSV logs now include structured detail for top/bottom ROI readings, selected ROI, and confirmation results.
- Sequence XPL capture starts from `HOME`, moves the sample stage to `0 deg`, runs adaptive XPL search, then keeps the selected polarizer angle fixed while imaging.
- Sequence cleanup returns the sample stage to `0 deg` without homing/resetting the polarizer.
- Long calibration/status messages no longer resize the app window; full text remains available through the log and tooltip.
- Sequence control button sizing was adjusted so labels such as `START SEQUENCE` do not clip at the default window size.

## Validation

- `python -m compileall app apis scripts`
- `python -m unittest tests.test_ximea_raw16_sequence`
- `.\build\build.ps1 -Version 0.1.4`

## Notes

- Sequence capture remains `RAW16` only.
- RAW16 preview conversion remains available inside the APIS app.
- Reusing the same `SampleID` may append to the existing CSV log.
