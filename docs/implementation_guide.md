# APIS Hardware Implementation Guide

## 1. Purpose

This document explains how to assemble and reproduce the APIS (Automated Polarization Imaging System) hardware. It covers the BOM, wiring architecture, Arduino pin mapping, implementation steps, calibration, and the verification checklist. The guide is based on the current firmware in `firmware/APIS_Firmware/APIS_Firmware.ino` and the controller-side protocol in `apis/config.py` and `apis/controller.py`.

For the mechanical stack-up, printed parts, bearings, and stage assembly order, see `docs/hardware_assembly_guide.md`.

## 2. System Overview

APIS is a 2-axis polarization imaging platform built around two servos, an Arduino controller, a host PC, and a XIMEA camera.

- Axis 1: SG90 servo for polarizer rotation
- Axis 2: HS-318 servo for sample rotation
- Controller: Arduino Uno
- Host PC: Python GUI communicating over USB serial
- Camera: XIMEA USB camera
- Light source: MORITEX backlight or equivalent

The control flow is:

1. The PC sends `PPAAA` commands over USB serial at `9600` baud.
2. The Arduino drives the two servo axes.
3. The camera is connected directly to the PC.
4. The SG90 is powered from Arduino `5V/GND`, while the HS-318 is powered from the regulator output. Grounds are shared.

## 3. BOM

### 3.1 Required Parts

| Category | Item | Recommended / Used | Qty | Notes |
| --- | --- | --- | --- | --- |
| Controller | Arduino board | Arduino Uno | 1 | Matches current firmware |
| Servo motor | Polarizer axis servo | SG90 | 1 | Signal on pin 10 |
| Servo motor | Sample axis servo | HS-318 | 1 | Signal on pin 11 |
| Camera | Industrial camera | XIMEA USB 3.0/3.1 camera | 1 | Connected directly to PC |
| Illumination | Backlight | MORITEX MEBL-CW7050 + MLEK-A080W2LR | 1 | Lab-used combination |
| Power | External power adapter | UNIFIVE UN318-1215, 12V 1.5A | 1 | Main power input |
| Power regulation | DC-DC buck converter | LM2596 / LM2596S module | 1 | Powers HS-318 |
| Wiring | Jumper wires / hookup wires | Mixed male-female and male-male | 1 set | For signal and power |
| Mechanical | Fasteners | M3 screws | 1 set | Printed-part stack and support-layer fastening |
| Mechanical | Threaded inserts | Female-thread brass knurled threaded insert embedment nuts | 1 set | Heat-set into printed parts with a soldering iron |
| Mechanical | Polarizer holder / sample stage | Custom jig | 1 set | Depends on fixture design |

### 3.2 Recommended Accessories

| Item | Qty | Purpose |
| --- | --- | --- |
| Solderless breadboard or terminal block | 1 | Power distribution |
| Multimeter | 1 | Voltage setup and wiring validation |
| USB cable for Arduino | 1 | Firmware upload and serial connection |

## 4. Firmware Pin Map

The current firmware uses the following fixed pin map:

| Function | Arduino Pin | Device |
| --- | --- | --- |
| Polarizer servo signal | D10 | SG90 |
| Sample servo signal | D11 | HS-318 |
| Serial communication | USB Serial | PC GUI / scripts |

Serial protocol:

- Baudrate: `9600`
- Command format: `PPAAA\n`
- Examples:
  - `98000`: RESET / ARM
  - `99000`: ESTOP
  - `96000`: HOME
  - `10095`: Polarizer to 95 degrees
  - `11045`: Sample to 45 degrees

## 5. Power and Wiring Architecture

### 5.1 Power Distribution

Build the power path as follows:

1. Connect the `UNIFIVE UN318-1215 (12V 1.5A)` adapter to the Arduino power input.
2. Connect Arduino `VIN/GND` to the regulator `VIN/GND`.
3. Connect Arduino `5V/GND` to the SG90 power pins.
4. Connect regulator `VOUT/GND` to the HS-318 power pins.
5. Keep Arduino, SG90, and HS-318 on a common ground.

Reference wiring:

```text
UNIFIVE UN318-1215 12V/1.5A
  -> Arduino power input

Arduino VIN/GND
  -> regulator VIN/GND

Arduino 5V/GND
  -> SG90 V+/GND

Regulator VOUT/GND
  -> HS-318 V+/GND
```

### 5.2 Core Rules

- Power the SG90 from Arduino `5V/GND`.
- Power the HS-318 from the regulator `VOUT/GND`.
- Share ground between Arduino and both servos.
- Route only servo signal lines to the Arduino digital pins.

### 5.3 System Power Layout

```text
UNIFIVE UN318-1215 12V/1.5A
   -> Arduino power input

Arduino VIN/GND
   -> regulator input VIN/GND

Arduino 5V/GND
   -> SG90 V+/GND

Regulator output VOUT/GND
   -> HS-318 V+/GND
```

This wiring matches the current firmware and control application.

## 6. Why Calibration Is Required

This system should be calibrated after assembly.

Calibration is necessary because:

- The gears are 3D printed rather than machined
- Servo gears are mounted directly onto the servo shafts with screws
- Female-thread brass heat-set inserts are embedded into printed parts during assembly
- Printed fit, backlash, concentricity, and servo mounting can vary from build to build
- The effective stage rotation can differ from the nominal design ratio

In practice, this means the commanded angle sent by software may not match the true stage angle unless the system is calibrated.

Current implementation status:

- Both axes use an image-derived calibration ratio in Python
- The current calibration dataset should be a PPL reference capture set under `data/calibrationsample/`
- The current polarizer-stage and sample-stage stage-to-servo ratio is `1.059`
- This corresponds to an effective measured stage response of about `0.944 x commanded_angle`
- With the current `0-180` servo command range, the current software limit is `169 deg` per stage

Because the calibrated ratio is greater than `1.0`, the maximum stage angle is still lower than the maximum raw servo command. With the current settings, `169 deg` is the largest stage angle that still maps inside the `0-180` servo command range.

For the PPL/XPL crystallinity imaging workflow targeted by this build, the required operating states fall within a limited angular working envelope. For that reason, a compact servo-driven transmission was appropriate for the current system architecture.

If a future version needs reliable travel well beyond this range, the preferred upgrade paths are:

- Change the transmission ratio so the required stage range fits inside the servo's usable motion
- Replace the current servo-driven axes with a stepper-motor-based drive for a wider and more repeatable rotation range

## 7. Wiring Guide

### 6.1 Polarizer Axis (SG90)

Typical SG90 wire colors are:

- Brown or black: GND
- Red: +5V
- Orange or yellow: Signal

Connect:

- SG90 Signal -> Arduino `D10`
- SG90 V+ -> Arduino `5V`
- SG90 GND -> Arduino `GND`

### 6.2 Sample Axis (HS-318)

Wire colors may vary by vendor, so confirm against the actual servo before powering it.

Connect:

- HS-318 Signal -> Arduino `D11`
- HS-318 V+ -> Regulator `VOUT`
- HS-318 GND -> Regulator `GND`

### 6.3 Arduino

Connect:

- Arduino USB -> Host PC
- UNIFIVE UN318-1215 12V 1.5A -> Arduino power input
- Arduino `VIN/GND` -> Regulator `VIN/GND`
- Arduino `5V/GND` -> SG90 `V+/GND`
- Arduino `D10` -> SG90 Signal
- Arduino `D11` -> HS-318 Signal

Notes:

- Confirm regulator input and output labels on the actual module before powering the system.
- Keep all grounds tied together.

### 6.4 Text Wiring Diagram

```text
PC USB
  -> Arduino Uno USB

UNIFIVE UN318-1215 12V/1.5A
  -> Arduino power input

Arduino VIN/GND
  -> regulator VIN/GND

Arduino 5V/GND
  -> SG90 V+/GND

Arduino D10
  -> SG90 signal

Arduino D11
  -> HS-318 signal

Regulator VOUT/GND
  -> HS-318 V+/GND
```

## 8. Implementation Procedure

### Step 1. Prepare the Power Path

1. Connect the `UNIFIVE UN318-1215 12V 1.5A` adapter to the Arduino power input.
2. Connect Arduino `VIN/GND` to regulator `VIN/GND`.
3. Use a multimeter to set the regulator output voltage for the HS-318.
4. Connect Arduino `5V/GND` to SG90 `V+/GND`.

### Step 2. Connect the Signal Lines

1. Connect the SG90 signal line to Arduino `D10`.
2. Connect the HS-318 signal line to Arduino `D11`.
3. Verify that Arduino `GND`, SG90 `GND`, and regulator `GND` are common.

### Step 3. Connect Servo Power

1. Connect SG90 `V+/GND` to Arduino `5V/GND`.
2. Connect HS-318 `V+/GND` to regulator `VOUT/GND`.
3. Recheck polarity before applying power.

### Step 4. Upload Firmware

1. Open `firmware/APIS_Firmware/APIS_Firmware.ino` in the Arduino IDE.
2. Select the Arduino Uno board profile.
3. Select the correct COM port and upload the firmware.

### Step 5. Initial Verification

1. Confirm the serial connection shows `READY`.
2. Use the APIS app or `scripts/check_hardware.py` to verify controller connection.
3. Send `RESET` and verify both axes move correctly.
4. Trigger `ESTOP` and verify torque is released immediately.
5. If needed, run `scripts/test_servo_limits.py` to probe raw servo endpoint behavior inside the firmware-supported `0-180` range.

### Step 6. Run Stage Calibration

1. Place a calibration target or asymmetric sample on the stage being calibrated.
2. Capture a `PPL` sequence across known commanded angles such as `0, 15, 30, ...`.
3. Run:

```bash
python scripts/analyze_stage_calibration.py data/calibrationsample/<ppl_reference_set>
```

4. Review the reported `Recommended stage_to_servo_ratio`.
5. Update `POLARIZER_STAGE_TO_SERVO_RATIO` and/or `SAMPLE_STAGE_TO_SERVO_RATIO` in `apis/config.py` if new calibrated values are required.
6. Re-test manual motion and sequence acquisition after changing the ratio.

## 9. Operational Behavior

The firmware state machine directly affects the hardware behavior:

- Boot state: `LATCHED`
- `LATCHED`: servos detached, motion commands blocked
- `RESET (98)`: servos attached, system enters `ARMED`
- `ESTOP (99)`: servos detached, system returns to `LATCHED`
- `HOME (96)`: both axes move to 0 degrees
- `10AAA`: polarizer angle command
- `11AAA`: sample angle command

Practical implications:

- The servos do not move automatically at power-on.
- Motion is enabled only after `RESET`.
- During `ESTOP`, torque is released, so the mechanical structure must tolerate free movement.

## 10. Sequence Behavior

The Python sequence controller uses the hardware as follows:

- XPL mode:
  - Polarizer = 95 degrees in the current baseline
  - Sample = stepped through multiple angles
- PPL mode:
  - Polarizer = 5 degrees in the current baseline
  - Sample = stepped through multiple angles

Default sample angle example:

- `90, 60, 45, 30, 0`

Mechanical design should therefore support:

- Stable polarizer movement across at least `0-90` degrees
- Stable sample movement across the full calibrated range
- A clearly defined physical zero position for each axis

### 10.1 Camera Acquisition Baseline

For the current XIMEA-based acquisition setup:

- Auto exposure / auto gain should remain disabled during capture
- Camera gamma should remain fixed at `1.0`
- Auto white balance should remain disabled during capture
- The current fixed white-balance baseline is `R=1.40`, `G=1.00`, `B=1.20`
- The same fixed white-balance values should be used for both PPL and XPL images so color differences remain comparable across modes
- Re-evaluate the fixed white-balance values if the illumination path, optics, or analyzer/polarizer alignment changes
- Live preview should use `XI_RGB24`
- Sequence capture should use `XI_RAW16`
- The current validated sequence exposure baseline is:
  - PPL: `18000 us`
  - XPL: `500000 us`
- The current default polarizer baseline is:
  - PPL: `5 deg`
  - XPL: `95 deg`
- Polarizer-angle calibration should use a separate default exposure baseline:
  - Polarizer calibration scan: `200000 us`
- Sequence outputs should be saved as Bayer RAW `uint16 TIFF` plus per-sequence CSV and JSON metadata

## 11. Verification Checklist

- The UNIFIVE UN318-1215 12V 1.5A adapter is connected correctly to the Arduino power input
- Arduino `VIN/GND` is connected correctly to regulator `VIN/GND`
- Arduino `5V/GND` is connected correctly to the SG90
- Regulator `VOUT/GND` is connected correctly to the HS-318
- Regulator `GND` and Arduino `GND` are common
- SG90 is on `D10` and HS-318 is on `D11`
- `READY -> RESET -> move -> ESTOP` works as expected
- Motion commands are rejected after `ESTOP`
- No mechanical interference occurs across the full calibrated motion range
- Sample-stage calibration has been run after assembly or rebuild
- Polarizer-stage calibration has been run after assembly or rebuild
- The configured stage max angle matches the current calibration result
- XIMEA acquisition uses fixed white balance rather than auto white balance

## 12. Notes

- Effective range, torque, and deadband vary by servo model and clone quality.
- Servo wire colors may differ by vendor.
- This document focuses on the servo control and system integration path rather than full camera or lighting electrical detail.
- `ESTOP` detaches the servos, so the axes may move freely afterward.

## 13. Improvement Opportunities

The current implementation is suitable for operation, but the following upgrades would improve maintainability and repeatability:

- Replace loose power branching with a perma-proto board or small distribution board
- Use connectorized wiring between servos, regulator, and Arduino
- Add labels for power input, servo power output, and signal lines
- Add a simple wiring schematic image for faster assembly by new users
- Add fixture photos, mechanical sketches, and an initial calibration document
