# APIS Hardware Assembly Guide

## 1. Purpose

This document explains the mechanical assembly of the APIS platform using the STL files in `parts/`. It covers the printed parts, stage stack-up, bearing layout, gear pairing, and the order of assembly for the polarizer and sample rotation system.

## 2. Printed Parts

The current mechanical design uses the following STL files:

- `parts/support_1stfloor.stl`
- `parts/support_2ndfloor.stl`
- `parts/support_3rdfloor.stl`
- `parts/support_4thfloor.stl`
- `parts/polarizer_stage_gear.stl`
- `parts/film_stage_gear.stl`
- `parts/servo_gear.stl`

## 3. Purchased Mechanical and Optical Parts

- Servo for polarizer axis: SG90
- Servo for sample axis: HS-318
- Bearing balls: McMaster-Carr `9292K74`, hard wear-resistant 52100 alloy steel balls, `5.5 mm` diameter
- Polarizer film: Edmund Optics `50 mm Dia. Linear Polarizing Film (XP42-18)`, PN `29490`
- Analyzer polarizer for camera: mount a second linear polarizer in front of the camera lens
- Fasteners: M3 screws
- Threaded inserts: female-thread brass knurled threaded insert embedment nuts

The current build uses heat-set brass inserts in the printed parts. The female-thread inserts are embedded into the printed plastic with a soldering iron before the M3 screws are installed.

## 4. Gear Set Definition

The motion system is based on two gear sizes.

### Large Gear

- Teeth: `80T`
- Module: `0.8`
- Pitch diameter: `64 mm`
- Outside diameter: `65.6 mm`

Used for:

- `polarizer_stage_gear`
- `film_stage_gear`

### Small Gear

- Teeth: `70T`
- Module: `0.8`
- Pitch diameter: `56 mm`
- Outside diameter: `57.6 mm`

Used for:

- `servo_gear`

Each servo receives one `servo_gear`, and each `servo_gear` meshes horizontally with one large stage gear.

## 5. Stage Architecture

The system is assembled as a vertical stack of floors.

### First Floor

- Base support structure
- SG90 mounted for the polarizer axis
- HS-318 mounted for the sample axis
- Backlight mounted into the first-floor support

### Second Floor

- Supports and fixes the backlight position
- Provides the bearing race for the polarizer stage
- Works with the underside of the polarizer gear to create a ball-bearing rotation interface

### Polarizer Stage

- `polarizer_stage_gear` sits above the second-floor support
- The stage holds the circular polarizer film
- A SG90-mounted `servo_gear` meshes with the `polarizer_stage_gear`
- A second polarizer is mounted in front of the camera lens so the system can operate in XPL mode

### Third Floor

- Supports the upper sample stage assembly
- Provides another circular bearing race for the sample stage
- Works with the underside of the sample gear/stage assembly to create a second ball-bearing rotation interface

### Sample Stage

- `film_stage_gear` sits above the third-floor support
- The printed part combines the large gear and the sample stage
- An HS-318-mounted `servo_gear` meshes with the `film_stage_gear`

### Fourth Floor

- Top support / retention structure
- Final mechanical stabilization for the upper assembly

## 6. Bearing Interface Design

Both rotating stages use the same bearing concept.

1. The underside of the rotating gear includes five cylindrical bearing pockets.
2. The supporting floor below includes a donut-shaped circular race.
3. Five `5.5 mm` steel balls are placed into the race/pocket interface.
4. The rotating gear rests on the five balls and can rotate smoothly.

This structure is used twice:

- Between `support_2ndfloor` and `polarizer_stage_gear`
- Between `support_3rdfloor` and `film_stage_gear`

## 7. Polarizer Stage Assembly

Build the polarizer stage as follows:

1. Heat-set the brass threaded inserts into the printed parts that receive M3 fasteners.
2. Install the SG90 into `support_1stfloor`.
3. Mount the backlight into `support_1stfloor`.
4. Install `support_2ndfloor` so the backlight position is fixed.
5. Place five McMaster-Carr `9292K74` balls into the donut-shaped bearing path in the second-floor support.
6. Insert the polarizer film into `polarizer_stage_gear`.
7. Position the `polarizer_stage_gear` so its lower bearing pockets sit on the five balls.
8. Mount a `servo_gear` onto the SG90 shaft.
9. Mesh the SG90 `servo_gear` horizontally with the `polarizer_stage_gear`.

## 8. Sample Stage Assembly

Build the sample stage as follows:

1. Confirm that all required brass inserts are embedded in the upper printed supports before stack-up continues.
2. Install `support_3rdfloor` above the polarizer stage assembly.
3. Place five McMaster-Carr `9292K74` balls into the donut-shaped bearing path in the third-floor support.
4. Position `film_stage_gear` above the balls so the lower bearing pockets sit correctly on the ball set.
5. Mount a second `servo_gear` onto the HS-318 shaft.
6. Mesh the HS-318 `servo_gear` horizontally with `film_stage_gear`.
7. Install `support_4thfloor` as the upper retaining/support layer.

## 9. Full Assembly Order

Assemble the full system in this order:

1. Print all STL parts in `parts/`.
2. Heat-set the female-thread brass threaded inserts into the printed parts using a soldering iron.
3. Mount SG90 and HS-318 to `support_1stfloor`.
4. Mount the backlight to `support_1stfloor`.
5. Install `support_2ndfloor`.
6. Add five bearing balls to the second-floor bearing race.
7. Install the `polarizer_stage_gear` with polarizer film inserted.
8. Mount the SG90 `servo_gear` and mesh it with the polarizer stage gear.
9. Install `support_3rdfloor`.
10. Add five bearing balls to the third-floor bearing race.
11. Install the `film_stage_gear`.
12. Mount the HS-318 `servo_gear` and mesh it with the sample stage gear.
13. Install `support_4thfloor`.
14. Secure printed parts and retained components with M3 screws into the heat-set inserts.
15. Complete electrical wiring using `docs/implementation_guide.md`.
16. Upload firmware and verify stage movement.
17. Run stage calibration as described in `docs/implementation_guide.md`.

## 10. Motion Summary

The motion path is:

- SG90 -> `servo_gear` -> `polarizer_stage_gear` -> polarizer rotation
- HS-318 -> `servo_gear` -> `film_stage_gear` -> sample rotation

The large gears carry the functional stage bodies, while the small gears are mounted directly to the servo shafts.

For XPL imaging:

- One polarizer is mounted in `polarizer_stage_gear`
- A second polarizer is mounted in front of the camera lens and acts as the analyzer
- XPL is produced by the relative orientation between the stage polarizer and the lens-side analyzer

For the PPL/XPL crystallinity imaging workflow targeted by this build, the required operating states fit within a limited angular working envelope, so the present servo-driven gear train is appropriate for the current assembly.

## 11. Assembly Checks

- Both servos are firmly fixed to `support_1stfloor`
- Backlight is centered and secured before upper floors are installed
- Each bearing interface contains exactly five `5.5 mm` steel balls
- The stage gears sit evenly on the ball sets without binding
- Each `servo_gear` meshes cleanly with its matching large gear
- All required heat-set brass inserts are seated flush and retain M3 screws securely
- The polarizer film is seated securely in `polarizer_stage_gear`
- The sample holder area on `film_stage_gear` is level and centered
- Upper support layers do not rub against the rotating gears
- A fresh calibration is performed after final assembly

## 12. Improvement Opportunities

- Add an exploded assembly diagram showing the full floor-by-floor stack
- Add dimensions or tolerances for servo mounting holes and bearing pockets
- Add a fixture drawing for polarizer film insertion and sample mounting
- Use a perma-proto board or fixed wiring bracket to clean up cable routing around the moving stages
- If future versions require reliable rotation well beyond the current working envelope, update the gear ratio or upgrade the axes to a stepper-motor-based drive
