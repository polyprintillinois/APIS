# APIS v0.1.5 Release Notes

## Summary

This source-and-documentation follow-up ensures that the finalized APIS first-run workflow, hardware safety guidance, and Zenodo archival link are included in the tagged release.

## Changed Since v0.1.4

- Finalized the APIS-specific first-run workflow using the Arduino Uno, XIMEA camera, `PPL` mode, and a three-angle blank-slide RAW16 sequence.
- Documented the verified no-camera test command: `python -m unittest tests.test_mock_serial`.
- Expanded safety guidance for LM2596 voltage setup, servo-driven rotation stages, component heating, and optical-surface handling.
- Replaced the pending Zenodo placeholder with the version-independent Concept DOI.

## Validation

- `python -m compileall app apis scripts`
- `python -m unittest tests.test_ximea_raw16_sequence` (12 tests)
- `python -m unittest tests.test_mock_serial` (6 tests)
- Zenodo JSON validation with ORCIDs for Changhyun Hwang and Ying Diao

## Windows Application

There are no application or firmware changes in v0.1.5, so the existing [APIS v0.1.4 Windows package](https://github.com/polyprintillinois/APIS/releases/download/v0.1.4/APIS-win64-v0.1.4.zip) remains current and does not need to be rebuilt.
