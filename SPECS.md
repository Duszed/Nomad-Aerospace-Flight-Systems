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
| Hover rotor speed | **~2,150 rpm at 15 kg/rotor** (Hobbywing 54 V dyno curve) → rotation fundamental **~36 Hz**; two-blade blade-pass ~72 Hz, covered by the 2nd notch harmonic |

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
| Situational awareness | **SIYI A2 mini** FPV gimbal camera — 160° horizontal FOV, single-axis tilt (−90°…+25°), 1/2.7" starlight sensor, 1080p. Independent of the flight controller; requires no ArduPilot parameters |

## Spray System

| Item | Specification |
|---|---|
| Pump | **Hobbywing PUMP 30L** (HW 30L Flexible Impeller Pump) |
| Rated flow | 20 L/min (30 L/min max) |
| Recommended battery | 12–18S LiPo — covers the K30's 14S pack |
| Nozzle | Centrifugal atomiser, efficient droplet atomisation |
| Required flow at cruise | ~4.4 L/min (15 L/ha × 7 m swath × 7 m/s cruise) |
| Design margin | ~4.5× at rated flow — comfortable headroom for back-pressure, tubing losses and future rate increases |
| Control | `SERVO9_FUNCTION,22` (pump) / `SERVO10_FUNCTION,23` (spinner), flow slaved to ground speed via `SPRAY_PUMP_RATE` / `SPRAY_SPEED_MIN` — see `/config` |

## Control & Video Link

| Item | Specification |
|---|---|
| Ground controller | **SIYI MK15 Agriculture** — integrated RC, 1080p video and telemetry, 5.5 in 1000 cd/m² touchscreen, Android, IP53 |
| Air unit | SIYI MK15 air unit — S.Bus control + MAVLink telemetry UART to the flight controller, Ethernet port for the camera |
| FPV camera | **SIYI A2 mini** — video over **Ethernet** (its only video output), 12 V supply, ~2 W average / 12 W peak, 85 g, IP65 |
| Gimbal control | Camera tilt driven from MK15 dials/switches via the air unit (PWM, or optional S.Bus). Not routed through the flight controller |
| Ground software | QGroundControl / SIYI QGC / SIYI FPV app — RTSP video stream |
| Control link | 180 ms latency; ~3.5 km at 3 m agricultural flight height (15 km line-of-sight rating) |

**Confirmed:** there is **no separate 915/868 MHz telemetry radio.** The MK15
pair carries control, telemetry and FPV video on one link. `SERIAL2` in
`/config` connects to the MK15 air unit's telemetry UART.

Chosen over CubePilot Herelink on cost (MK15 Agriculture ≈ $448) and
because SIYI documents the MK15 ↔ A2 mini pairing directly, alongside
Pixhawk/ArduPilot and QGroundControl support.

## Field Operations

> How the aircraft knows where it is, where the field boundary is, and
> where to spray — entirely offline. No cellular or internet connection
> is required for any step below.

| Step | How it works | Connectivity |
|---|---|---|
| Position | Here4 RTK fuses GPS/GLONASS/Galileo with correction data from a local RTK base station over a direct radio link | Local radio only — no internet |
| Field boundary | Walked or flown once per field in Loiter/manual mode; saved as a polygon in the GCS (QGroundControl / Mission Planner) | One-time capture, stored locally on the ground station |
| Base map (optional) | Satellite imagery pre-downloaded and cached on the ground station tablet when internet is available | Cached locally; not required at flight time |
| Spray route | GCS Survey/Grid tool generates the full pass pattern from the saved boundary, swath width, altitude and overlap | Computed on-device, offline |
| Mission upload | Generated route uploaded to the Cube over the SIYI MK15 link | Local link only |
| Application rate | `/config` ties pump flow to real-time ground speed (`SPRAY_PUMP_RATE`, `SPRAY_SPEED_MIN`) — not derived from any map or cloud data | Onboard flight-controller logic |

**No SIM or cellular connectivity is used.** The MK15's SIM slot is left
unpopulated; all field data (coverage maps, spray records, telemetry)
routes only through the Nomad ground gateway — never through a SIYI
cloud account or third-party service.

## Ground & Field Segment

| Item | Specification |
|---|---|
| Telemetry downlink | MAVLink 2, SERIAL2 @ 57,600 — via the SIYI MK15 air unit (no separate telemetry radio) |
| Ground gateway | Python 3.9+ / pymavlink, alerting + NDJSON data feed |
| Field edge node MCU | **ESP32-C6** (RISC-V, Wi-Fi 6 / BLE 5 / 802.15.4) |
| Field node radio | **ESP-NOW** point-to-point to ground gateway (implemented). C6's 802.15.4 radio makes Zigbee/Thread mesh a firmware-only upgrade — no board change |
| Field node sensor | Sensirion SHT4x I2C temp/RH with conditional micro-heater |
| Node duty cycle | 5 min deep-sleep wake cycle, 18650 Li-Ion powered |

## Explicitly NOT in the current design (roadmap only)

- 360° radar coverage (would require 3–4 radar units)
- Zigbee/Thread mesh networking — hardware is C6/802.15.4 capable, mesh firmware not yet written
- Encrypted telemetry links
- Dual-antenna GNSS heading
- **NDVI / multispectral survey camera** (MAPIR Survey3 or similar) — planned, not fitted. Removed from the parameter stack until the hardware is actually on the aircraft
- Autonomous mission library (`/missions` removed until SITL-validated)

## Identity

| Item | Value |
|---|---|
| Company | **Nomad Aerospace** (formerly Logica Dynamics) |
| License | See `LICENSE` file — single license, no side statements |
