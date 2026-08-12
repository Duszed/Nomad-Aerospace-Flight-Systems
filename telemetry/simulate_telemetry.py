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
"""

import time

from pymavlink import mavutil

CONNECTION = "udpout:127.0.0.1:14550"  # gateway listens on udp:0.0.0.0:14550

# 14S pack, ramps 58.8V (full) down toward 44V over the run - see the
# battery ladder in SPECS.md: LOW=47.6V, CRIT=46.2V
BATT_START_V = 58.8
BATT_END_V = 44.0
RUN_SECONDS = 90

SYSTEM_ID = 1
COMPONENT_ID = mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1


def main() -> None:
    conn = mavutil.mavlink_connection(
        CONNECTION, source_system=SYSTEM_ID, source_component=COMPONENT_ID
    )

    print(f"[SIM] Emitting synthetic K30 telemetry on {CONNECTION}")
    print(f"[SIM] Battery ramps {BATT_START_V}V -> {BATT_END_V}V over {RUN_SECONDS}s")
    print("[SIM] Start mavlink_ground_gateway.py in another terminal to observe it.\n")

    t0 = time.monotonic()
    lat, lon = 40.8500 * 1e7, 68.6600 * 1e7  # near Syrdarya region
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


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[SIM] Stopped.")
