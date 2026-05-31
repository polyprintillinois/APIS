APIS (Automated Polarization Imaging System)

Package version: v0.1.4

Installation and Run (Windows)
1) Install prerequisites (see prereq_checklist.md)
2) Unzip the distribution package
3) Run APIS\APIS.exe

Recommended first-time checks
- Connect camera and confirm live view
- Connect Arduino, press RESET/ARM, and perform a manual move

Notes
- Do not move APIS.exe out of the APIS folder. Keep the whole folder.
- If the camera is not detected, verify XIMEA drivers are installed.
- Live view and Snapshot use RGB preview output.
- Sequence capture uses RAW16 TIFF output with per-sequence CSV and JSON metadata.
- Default acquisition baseline: PPL = polarizer 5 deg @ 18000 us, XPL = polarizer 95 deg @ 400000 us.
- Default polarizer calibration uses adaptive local XPL search at 400000 us with top/bottom background ROIs.
- XPL sequence capture starts from HOME, then performs adaptive background-ROI XPL search at bottom x=700, y=1000, width=700, height=80 px and top x=700, y=0, width=700, height=80 px before imaging.
- XPL capture proceeds only when final background-ROI confirmation mean is <= 65.
- Sequence cleanup returns the sample stage to 0 deg and avoids homing/resetting the polarizer.
- Current XIMEA capture baseline uses fixed white balance: R=1.40, G=1.00, B=1.20.
- The app also includes a RAW16 to RGB preview conversion tool for saved datasets.
