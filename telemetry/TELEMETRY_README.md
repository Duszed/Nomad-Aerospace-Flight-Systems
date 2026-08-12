# Nomad Aerospace — Software Components

All software here runs **without the aircraft**, so the flight logic and
agronomic calculations can be reviewed, tested and demonstrated today.

## Quick start

```bash
pip install -r requirements.txt
pytest -v                 # 44 tests, no hardware required
```

## Components

| File | What it does |
|---|---|
| `mavlink_ground_gateway.py` | Receives live MAVLink 2 telemetry from the Cube Orange+, emits NDJSON for the dashboard and as-applied records. Observes and alerts only — the flight controller is the sole failsafe authority. |
| `spray_planner.py` | Generates the boustrophedon spray pattern, computes passes, chemical volume, tank refills and time on task. Exports QGroundControl `.waypoints` files. Fully offline. |
| `simulate_telemetry.py` | Emits synthetic MAVLink telemetry so the gateway can be run end-to-end with no aircraft. Battery ramps down to exercise the failsafe alert thresholds. |
| `esp32_field_sensor_node.ino` | ESP32-C6 field node firmware — SHT4x microclimate sampling with conditional dew-clearing heater cycle, ESP-NOW uplink, deep-sleep duty cycle. |
| `test_gateway.py` | 9 tests — battery-state classification against the 14S ladder in SPECS.md. |
| `coverage_logger.py` | Consumes the gateway's NDJSON stream and produces an as-applied spray record: distance actually sprayed, area covered, chemical applied, with GPS-glitch rejection and a speed gate matching `SPRAY_SPEED_MIN`. This is the VRA ingest stage in the architecture diagram. |
| `test_spray_planner.py` | 18 tests — swath geometry, consumables, timing, waypoint export, input validation. |
| `test_coverage_logger.py` | 17 tests — geodesy, distance integration, speed gating, glitch rejection, serialisation. |

## Demonstrating the telemetry pipeline without hardware

Terminal 1:
```bash
python simulate_telemetry.py
```

Terminal 2:
```bash
python mavlink_ground_gateway.py
```

The gateway connects, requests telemetry streams, and prints live NDJSON.
As the simulated pack discharges it crosses the thresholds published in
`SPECS.md`, and the gateway logs the LOW (47.6 V) and CRITICAL (46.2 V)
transitions — the same events the flight controller acts on independently.

## Planning a spray mission

```bash
python spray_planner.py --area 12 --width 300
python spray_planner.py --area 12 --width 300 --export mission.waypoints
```

Example output for a 12 ha field:

```
Passes required               43
Total flight distance      17.49 km
Chemical required          180.0 L
Tank refills                   5   (30 L tank)
Est. time on task           47.3 min
```

These figures are what underpin the conservative 20 ha per aircraft per
day operating assumption used in our planning — a test in
`test_spray_planner.py` asserts that assumption stays conservative.

## Producing an as-applied record

The planner says what *should* happen. The coverage logger records what
*did*:

```bash
python mavlink_ground_gateway.py | python coverage_logger.py --field "North 12" --out record.json
```

Output:

```
AS-APPLIED SPRAY RECORD
Field                North 12
Sprayed distance          4820.4 m
Area covered               3.374 ha
Chemical applied           50.62 L
GPS fixes used               612
GPS fixes rejected             1
Min battery               47.10 V
```

Ground covered below `SPRAY_SPEED_MIN` is counted as transit, not treated
area — matching the pump's speed gate in the flight controller — and
implausible GPS jumps are rejected rather than inflating the record.

## Continuous integration

`.github/workflows/tests.yml` runs the full suite on Python 3.9, 3.11 and
3.12 on every push, and verifies the planner produces a valid
QGroundControl waypoint file. Test status is visible on the repository.
