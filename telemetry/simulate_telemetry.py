"""
Nomad Aerospace - Synthetic Telemetry Simulator
================================================
Purpose
-------
Emits realistic MAVLink 2 telemetry over UDP so the ground gateway
(mavlink_ground_gateway.py) can be run, tested, and demonstrated end to
end WITHOUT a physical aircraft. This is a development and CI tool, not
flight software - it never touches a flight controller or ArduPilot.

Usage
-----
    python simulate_telemetry.py

Then, in a second terminal:

    python mavlink_ground_gateway.py

The gateway will connect exactly as it would to a real Cube Orange+,
receive HEARTBEAT/SYS_STATUS/VFR_HUD/GLOBAL_POSITION_INT messages, and
print live NDJSON to stdout. Battery voltage ramps down over time so
the LOW and CRITICAL failsafe log transitions (see SPECS.md) can be
observed without needing an actual battery.

Mission mode (used by the Nomad Atlas demo)
-------------------------------------------
    python simulate_telemetry.py --mission --speedup 40

Flies the spray_planner.py pattern over the demo field (4.2 ha, 7 m
swath, 20 passes) and reports the pump output on SERVO9, so the as-applied
pipeline can be exercised end to end. By default the flight is slightly
imperfect (one pump dropout, one pass flown off-line) so that missed and
double-sprayed areas show up in the record. Everything it produces is
SAMPLE DATA, not a customer record.
"""

from __future__ import annotations

import argparse
import math
import os
import time

# MAVLink 2 dialect: SERVO_OUTPUT_RAW channels 9-16 are MAVLink 2 extension
# fields, and the spray pump is SERVO9. Must be set before pymavlink import.
os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil  # noqa: E402

from spray_planner import (  # noqa: E402
    CRUISE_SPEED_MS, DEFAULT_RATE_L_PER_HA, DEFAULT_SWATH_M, plan_mission,
)

CONNECTION = "udpout:127.0.0.1:14550"  # gateway listens on udp:0.0.0.0:14550

# 14S pack, ramps 58.8V (full) down toward 44V over the run - see the
# battery ladder in SPECS.md: LOW=47.6V, CRIT=46.2V
BATT_START_V = 58.8
BATT_END_V = 44.0
RUN_SECONDS = 90

SYSTEM_ID = 1
COMPONENT_ID = mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1

# --- mission mode -----------------------------------------------------------
DEMO_AREA_HA = 4.2            # demo field: 140 m x 300 m
DEMO_WIDTH_M = 140.0          # -> 20 passes at 7 m swath
SPRAY_HEIGHT_M = 3.0
TURN_SPEED_MS = 2.0           # cross-field hop between passes, pump off
PUMP_SERVO = 9                # SERVO9_FUNCTION,22 (SPECS.md)
PUMP_PWM_OFF = 1100
PUMP_PWM_ON = 1500
MISSION_BATT_END_V = 50.8     # stays above the LOW threshold for the demo

# Deliberate imperfections so the record has something to show:
#   pump dropout on pass 8 between 120 m and 180 m along the pass
#   pass 14 flown 2.5 m off-line (overlaps pass 13, leaves a strip unsprayed)
DEFAULT_FAULTS = {
    "dropout": {"pass": 7, "from_m": 120.0, "to_m": 180.0},
    "offset": {"pass": 13, "metres": -2.5},
}

_M_PER_DEG_LAT = 111_320.0


def mission_samples(area_ha: float = DEMO_AREA_HA,
                    field_width_m: float = DEMO_WIDTH_M,
                    swath_m: float = DEFAULT_SWATH_M,
                    rate_l_per_ha: float = DEFAULT_RATE_L_PER_HA,
                    dt: float = 0.2,
                    faults: dict | None = None,
                    origin_lat: float = 40.410983,
                    origin_lon: float = 68.841300) -> list:
    """Fly the planner's boustrophedon pattern and return telemetry samples.

    Pure function (no I/O, deterministic): one dict per `dt` seconds with
    t, lat, lon, alt_rel_m, groundspeed_ms, heading_deg, pump_pwm, spray_on,
    flow_lpm and battery_v. `faults=None` flies a perfect mission; pass
    DEFAULT_FAULTS for the demo's imperfect one.
    """
    faults = faults or {}
    plan = plan_mission(area_ha, field_width_m, swath_m, rate_l_per_ha,
                        alt_m=SPRAY_HEIGHT_M, origin_lat=origin_lat,
                        origin_lon=origin_lon)
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(origin_lat))

    def to_local(wp):
        return ((wp.lon - origin_lon) * m_per_deg_lon,
                (wp.lat - origin_lat) * _M_PER_DEG_LAT)

    offset = faults.get("offset")
    dropout = faults.get("dropout")

    # Build legs: (x0, y0, x1, y1, speed, pass_index or None)
    legs = []
    prev_end = None
    for i in range(plan.passes):
        x0, y0 = to_local(plan.waypoints[2 * i])
        x1, y1 = to_local(plan.waypoints[2 * i + 1])
        if offset and offset["pass"] == i:
            x0 += offset["metres"]
            x1 += offset["metres"]
        if prev_end is not None:
            legs.append((prev_end[0], prev_end[1], x0, y0, TURN_SPEED_MS, None))
        legs.append((x0, y0, x1, y1, CRUISE_SPEED_MS, i))
        prev_end = (x1, y1)

    total_time = sum(math.hypot(l[2] - l[0], l[3] - l[1]) / l[4] for l in legs)

    samples = []
    t = 0.0
    for x0, y0, x1, y1, speed, pass_idx in legs:
        length = math.hypot(x1 - x0, y1 - y0)
        if length <= 0:
            continue
        heading = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360.0
        steps = max(1, int(round(length / (speed * dt))))
        for k in range(steps):
            along = length * k / steps
            x = x0 + (x1 - x0) * k / steps
            y = y0 + (y1 - y0) * k / steps
            spraying = pass_idx is not None
            if (spraying and dropout and dropout["pass"] == pass_idx
                    and dropout["from_m"] <= along < dropout["to_m"]):
                spraying = False
            flow = (rate_l_per_ha * swath_m * speed * 60.0 / 10_000.0
                    if spraying else 0.0)
            frac = t / total_time if total_time else 0.0
            samples.append({
                "t": round(t, 3),
                "lat": origin_lat + y / _M_PER_DEG_LAT,
                "lon": origin_lon + x / m_per_deg_lon,
                "alt_rel_m": SPRAY_HEIGHT_M,
                "groundspeed_ms": speed,
                "heading_deg": heading,
                "pump_pwm": PUMP_PWM_ON if spraying else PUMP_PWM_OFF,
                "spray_on": spraying,
                "flow_lpm": round(flow, 3),
                "battery_v": round(
                    BATT_START_V - frac * (BATT_START_V - MISSION_BATT_END_V), 2),
            })
            t += length / steps / speed
    # final sample: arrived, pump off
    if samples:
        last = dict(samples[-1])
        x1, y1 = legs[-1][2], legs[-1][3]
        last.update(t=round(t, 3), lat=origin_lat + y1 / _M_PER_DEG_LAT,
                    lon=origin_lon + x1 / m_per_deg_lon, groundspeed_ms=0.0,
                    pump_pwm=PUMP_PWM_OFF, spray_on=False, flow_lpm=0.0)
        samples.append(last)
    return samples


def run_mission(connection: str = CONNECTION, speedup: float = 40.0,
                samples: list | None = None, flow_meter: bool = False,
                lead_in_s: float = 1.5, quiet: bool = False) -> int:
    """Send a mission's samples as MAVLink, `speedup` times faster than real time.

    time_boot_ms carries simulated aircraft time, so consumers that use the
    flight-controller clock see the true mission duration.
    """
    samples = samples if samples is not None else mission_samples(faults=DEFAULT_FAULTS)
    conn = mavutil.mavlink_connection(
        connection, source_system=SYSTEM_ID, source_component=COMPONENT_ID
    )

    def heartbeat():
        conn.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_QUADROTOR,
            mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
            0, 0, mavutil.mavlink.MAV_STATE_ACTIVE,
        )

    if not quiet:
        print(f"[SIM] SAMPLE DATA - mission of {len(samples)} samples "
              f"({samples[-1]['t'] / 60:.1f} min of flight) on {connection}, "
              f"{speedup:g}x real time", flush=True)

    # lead-in: let the gateway see heartbeats before the mission starts
    end = time.monotonic() + lead_in_s
    while time.monotonic() < end:
        heartbeat()
        time.sleep(0.2)

    t0 = time.monotonic()
    next_slow = 0.0
    for s in samples:
        target = t0 + s["t"] / speedup
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)

        boot_ms = int(s["t"] * 1000)
        if s["t"] >= next_slow:  # 1 Hz (aircraft time)
            next_slow = s["t"] + 1.0
            heartbeat()
            conn.mav.sys_status_send(
                0, 0, 0, 500, int(s["battery_v"] * 1000), 3600,
                -1, 0, 0, 0, 0, 0, 0,
            )

        conn.mav.vfr_hud_send(
            groundspeed=s["groundspeed_ms"], airspeed=s["groundspeed_ms"],
            heading=int(s["heading_deg"]), throttle=55,
            alt=s["alt_rel_m"], climb=0.0,
        )
        servos = [1500] * 16
        servos[PUMP_SERVO - 1] = s["pump_pwm"]
        conn.mav.servo_output_raw_send(boot_ms * 1000 % 2**32, 0, *servos)
        if flow_meter:
            conn.mav.named_value_float_send(boot_ms, b"FLOW", s["flow_lpm"])
        # position last: it is the row that commits a track sample downstream
        conn.mav.global_position_int_send(
            boot_ms, int(round(s["lat"] * 1e7)), int(round(s["lon"] * 1e7)),
            int(s["alt_rel_m"] * 1000), int(s["alt_rel_m"] * 1000),
            0, 0, 0, int(s["heading_deg"] * 100) % 36000,
        )

    for _ in range(3):
        heartbeat()
        time.sleep(0.1)
    if not quiet:
        print("[SIM] Mission complete.", flush=True)
    return len(samples)


def main() -> None:
    conn = mavutil.mavlink_connection(
        CONNECTION, source_system=SYSTEM_ID, source_component=COMPONENT_ID
    )

    print(f"[SIM] Emitting synthetic K30 telemetry on {CONNECTION}")
    print(f"[SIM] Battery ramps {BATT_START_V}V -> {BATT_END_V}V over {RUN_SECONDS}s")
    print("[SIM] Start mavlink_ground_gateway.py in another terminal to observe it.\n")

    t0 = time.monotonic()
    lat, lon = 40.410983 * 1e7, 68.841300 * 1e7  # Syrdarya farm parcel
    heading = 0

    while True:
        elapsed = time.monotonic() - t0
        frac = min(elapsed / RUN_SECONDS, 1.0)
        voltage_v = BATT_START_V - frac * (BATT_START_V - BATT_END_V)
        current_a = 36.0 + 4.0 * (elapsed % 10 < 5)  # oscillates like a real spray run

        conn.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_QUADROTOR,
            mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
            0, 0, mavutil.mavlink.MAV_STATE_ACTIVE,
        )

        conn.mav.sys_status_send(
            0, 0, 0, 500,
            int(voltage_v * 1000),      # mV
            int(current_a * 100),       # cA
            -1, 0, 0, 0, 0, 0, 0,
        )

        heading = (heading + 3) % 360
        conn.mav.vfr_hud_send(
            groundspeed=7.0, airspeed=7.0, heading=heading,
            throttle=55, alt=15.0 + 0.3 * (elapsed % 6),
            climb=0.0,
        )

        lat += 2  # slow drift, simulates a survey pass
        conn.mav.global_position_int_send(
            int(elapsed * 1000), int(lat), int(lon),
            15000, 15000, 0, 0, 0, heading * 100,
        )

        print(f"[SIM] t={elapsed:5.1f}s  batt={voltage_v:5.2f}V  "
              f"current={current_a:4.1f}A  heading={heading:3d}deg")

        time.sleep(1.0)


def cli() -> None:
    ap = argparse.ArgumentParser(description="NOMAD synthetic telemetry simulator")
    ap.add_argument("--mission", action="store_true",
                    help="fly the demo spray mission once, then exit")
    ap.add_argument("--connection", default=CONNECTION)
    ap.add_argument("--speedup", type=float, default=40.0,
                    help="mission mode: times faster than real time")
    ap.add_argument("--area", type=float, default=DEMO_AREA_HA)
    ap.add_argument("--width", type=float, default=DEMO_WIDTH_M)
    ap.add_argument("--swath", type=float, default=DEFAULT_SWATH_M)
    ap.add_argument("--origin-lat", type=float, default=None,
                    help="mission mode: latitude of the field's south-west corner")
    ap.add_argument("--origin-lon", type=float, default=None,
                    help="mission mode: longitude of the field's south-west corner")
    ap.add_argument("--perfect", action="store_true",
                    help="mission mode: no dropout / off-line pass")
    ap.add_argument("--flow-meter", action="store_true",
                    help="mission mode: also publish NAMED_VALUE_FLOAT 'FLOW' (L/min)")
    args = ap.parse_args()

    if args.mission:
        origin = {}
        if args.origin_lat is not None and args.origin_lon is not None:
            origin = {"origin_lat": args.origin_lat, "origin_lon": args.origin_lon}
        samples = mission_samples(args.area, args.width, args.swath,
                                  faults=None if args.perfect else DEFAULT_FAULTS, **origin)
        run_mission(args.connection, args.speedup, samples, args.flow_meter)
    else:
        main()


if __name__ == "__main__":
    try:
        cli()
    except KeyboardInterrupt:
        print("\n[SIM] Stopped.")
