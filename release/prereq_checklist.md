# APIS Prerequisite Checklist (Windows)

## 1) XIMEA Software Package
- Install XIMEA Windows Software Package (drivers + xiAPI)
- Reboot after driver installation
- Confirm camera shows in Device Manager or XIMEA tools

## 2) Run APIS
- Launch APIS.exe
- Click Connect Camera and confirm live view
- Confirm the camera stream looks reasonable with the fixed white-balance baseline used by the app

## 3) Arduino / Motor Check
- Connect the Arduino from the APIS app
- Press RESET / ARM
- Move Polarizer and Sample manually
- Press ESTOP and confirm torque release, then RESET

## 4) Sequence Smoke Test
- Set Sample ID and Save Directory
- Run a short sequence
- Default sequence baseline: PPL `18000 us` @ `5 deg`, XPL `400000 us` @ `95 deg`
- Default polarizer calibration baseline: adaptive local XPL search at `400000 us`, using top/bottom background ROIs
- Verify RAW16 TIFF images, `{SampleID}/{SampleID}_log.csv`, and `{SampleID}/{SampleID}_metadata.json` are created
- Verify live view returns after the sequence finishes

Troubleshooting order
1) XIMEA drivers
2) Camera connection
3) Arduino COM/serial
4) APIS app
