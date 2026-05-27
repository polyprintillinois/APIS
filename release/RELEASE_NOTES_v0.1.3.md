# APIS v0.1.3 Release Notes

## Summary

This release updates the polarization workflow around `PPL/XPL`, adds a reusable polarizer calibration baseline, and sets validated default acquisition exposures for routine PPL/XPL imaging.

## Added

- Polarization mode naming is now standardized to `PPL` / `XPL` across the app, sequence flow, and documentation.
- Polarizer-angle calibration now performs a coarse scan plus a fine scan around the darkest XPL angle.
- Calibrated polarizer baseline values are saved and restored on later app launches.
- Reachable orthogonal-angle handling for PPL derivation from the calibrated XPL angle.
- `scripts/analyze_xpl_exposure_sweep.py` for reviewing RAW16 XPL exposure-sweep datasets.
- Release packaging now stages files before zipping to avoid transient file-lock failures during archive creation.

## Changed

- Default validated acquisition baseline:
  - `PPL`: `18000 us` at polarizer `5 deg`
  - `XPL`: `400000 us` at polarizer `95 deg`
- Default polarizer calibration exposure:
  - `200000 us`
- `PPL` is no longer chosen from a brightness search; it is derived from the reachable orthogonal angle relative to `XPL`.
- Sequence output folders now use `ppl` and `xpl`.
- Distribution and implementation docs now match the current PPL/XPL baseline and calibration flow.

## Sequence Behavior

- Live camera preview is paused before sequence capture and restored afterward.
- Sequence capture remains `XI_RAW16` only.
- Sequence images are saved as Bayer RAW `uint16 TIFF`.
- Sequence outputs include:
  - `{SampleID}_log.csv`
  - `{SampleID}_metadata.json`
- CSV logs now include calibration signal metrics used during XPL-angle selection.

## Polarizer Calibration

- The calibration scan searches for the darkest `XPL` angle first.
- A coarse scan identifies the minimum region, then a fine scan refines the result around that angle.
- `PPL` is assigned as `XPL + 90 deg` or `XPL - 90 deg`, whichever stays inside the reachable polarizer range.
- The resulting baseline is saved for reuse on later runs.

## Conversion Tool

- Converts saved RAW16 TIFF datasets into RGB preview images.
- Processes the selected folder and its immediate child folders.
- Saves results into a sibling `_rgb` folder.
- Uses demosaic plus fixed white balance plus gamma for preview generation.

## Validation

- `python -m py_compile apis\\config.py apis\\io.py apis\\sequence.py apis\\utils.py app\\main.py app\\workers.py scripts\\analyze_xpl_exposure_sweep.py tests\\test_ximea_raw16_sequence.py`
- `python -m unittest tests.test_ximea_raw16_sequence tests.test_mock_serial`
- `.\build\build.ps1 -Version 0.1.3`

## Notes

- Sequence capture is still `RAW16`-only in this release.
- Live view and Snapshot behavior are unchanged.
- Reusing the same `SampleID` may append to the existing CSV log.
