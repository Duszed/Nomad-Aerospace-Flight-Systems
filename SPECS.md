# NOMAD AEROSPACE — NOMAD K30 · Master Specification (Rev B, Aug 2026)

> **This file is the single source of truth.** Every other document, comment,
> diagram, and code constant in this repository defers to the values below.
> If any file disagrees with SPECS.md, that file is wrong.

## Aircraft

| Item | Specification |
|---|---|
| Product | NOMAD K30 agricultural spraying UAS |
| Airframe | EFT K30 quadcopter frame, X configuration |
| Diagonal span | 1,781 mm motor-to-motor |
| Payload | 30 L liquid tank |
| Target AUW | 60 kg (15 kg per rotor axis at hover) |
| Autopilot | Hex Cube Orange+ · ArduCopter 4.5.x stable |

## Propulsion & Power

| Item | Specification |
|---|---|
| Powertrain | 4× Hobbywing X11 G2 FOC integrated units |
| Propellers | 43×14 in folding carbon (1,082 mm blade) |
| Battery | **14S** LiPo, 30,000 mAh (58.8 V full / 51.8 V nominal) |
| Hover point (design) | ~15 kg/axis → ~1.92 kW/axis → **~36 A per axis at 54 V** |
| Design efficiency at hover | ~7.8 g/W (to be validated on dynamometer) |
| ESC control | Standard PWM 1,100–1,940 µs; FOC internal to ESC |
| ESC telemetry | Hobbywing DataLink (independent of flight controller) |
| Est. hover rotor speed | ~1,300–1,500 rpm → blade-pass fundamental ~22 Hz |

## Battery failsafe ladder (14S)

| Stage | Threshold | Action |
|---|---|---|
| Arming gate | 50.4 V (3.6 V/cell) | Takeoff refused below |
| LOW | 47.6 V (3.4 V/cell) | Return to Launch |
| CRITICAL | 46.2 V (3.3 V/cell) | Immediate controlled landing |

Voltage source: sag-compensated, 10 s filter. The **flight controller is the
sole failsafe authority** — ground software alerts operators but never
commands the aircraft.

## Sensors & Navigation

| Item | Specification |
|---|---|
| GNSS | CubePilot Here4 RTK, **single antenna**, DroneCAN on CAN1 |
| Obstacle radar | **1× Nanoradar MR72, 77 GHz, forward sector (~112°)**, RadarCAN on CAN2 (dedicated bus) |
| Terrain altimeter | Benewake TF03 LiDAR, **UART on SERIAL4** |
| Terrain following | TF03-driven, canopy-relative height hold in spray missions |
| Avoidance behaviour | STOP 3 m before obstacle (no slide) |

## Ground & Field Segment

| Item | Specification |
|---|---|
| Telemetry downlink | MAVLink 2 over long-range radio, SERIAL2 @ 57,600 |
| Ground gateway | Python 3.9+ / pymavlink, alerting + NDJSON data feed |
| Field edge node MCU | **ESP32-C3** (RISC-V, single core) |
| Field node radio | **ESP-NOW** point-to-point to ground gateway (LoRa = upgrade path) |
| Field node sensor | Sensirion SHT4x I2C temp/RH with conditional micro-heater |
| Node duty cycle | 5 min deep-sleep wake cycle, 18650 Li-Ion powered |

## Explicitly NOT in the current design (roadmap only)

- 360° radar coverage (would require 3–4 radar units)
- Zigbee/Thread mesh (requires ESP32-C6 hardware)
- Encrypted telemetry links
- Dual-antenna GNSS heading
- Multispectral survey camera missions (`/missions` removed until SITL-validated)

## Identity

| Item | Value |
|---|---|
| Company | **Nomad Aerospace** (formerly Logica Dynamics) |
| License | See `LICENSE` file — single license, no side statements |
