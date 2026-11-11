"""
Nomad Aerospace - MAVLink Ground Telemetry Gateway
==================================================
Platform : Python 3.9+ / pymavlink (synchronous, single-threaded)
Aircraft : NOMAD K30 (14S power system - see SPECS.md)

Purpose
-------
Receives live MAVLink 2 telemetry from the flight controller and emits a
newline-delimited JSON (NDJSON) stream on stdout for consumption by the
Nomad dashboard and VRA database ingest.

Authority model (important)
---------------------------
This gateway OBSERVES AND ALERTS ONLY. All failsafe execution (battery
RTL/Land, link loss, geofence) is performed autonomously by the flight
controller per the /config parameter stack. Ground software never
commands the aircraft.

Sprayer observation (for Nomad Atlas as-applied records)
--------------------------------------------------------
The gateway also forwards the signals needed to know when the sprayer was
actually on: SERVO_OUTPUT_RAW (pump PWM on SERVO9, see SPECS.md),
RELAY_STATUS, and NAMED_VALUE_FLOAT (e.g. a flow meter published by an
onboard script). These are read-only telemetry, like everything else here.
"""

import argparse
import json
import logging
import os
import sys
import time

# MAVLink 2 dialect: SERVO_OUTPUT_RAW carries channels 9-16 (the spray pump
# is SERVO9) only as MAVLink 2 extension fields. Must be set before import.
os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration - values defer to SPECS.md (14S system)
# ---------------------------------------------------------------------------
CONNECTION_STRING = "udp:0.0.0.0:14550"
BAUD_RATE = 57600

CELL_COUNT = 14
VOLT_WARN = 3.4 * CELL_COUNT      # 47.6 V - FC will initiate RTL
VOLT_CRIT = 3.3 * CELL_COUNT      # 46.2 V - FC will initiate Land

# MAVLink reports voltage_battery = 65535 (uint16 max) when unknown
MAV_VOLTAGE_UNKNOWN = 65535
# MAVLink reports current_battery = -1 when unknown
MAV_CURRENT_UNKNOWN = -1

RECONNECT_DELAY_S = 5
HEARTBEAT_TIMEOUT_S = 10

# Message IDs and desired stream intervals (microseconds)
STREAM_INTERVALS_US = {
    mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS: 1_000_000,           # 1 Hz
    mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 200_000,    # 5 Hz
    mavutil.mavlink.MAVLINK_MSG_ID_VFR_HUD: 100_000,                # 10 Hz
    mavutil.mavlink.MAVLINK_MSG_ID_SERVO_OUTPUT_RAW: 200_000,       # 5 Hz
}

# Message types forwarded to the NDJSON stream
FORWARDED_TYPES = [
    "VFR_HUD", "GLOBAL_POSITION_INT", "SYS_STATUS",
    "SERVO_OUTPUT_RAW", "RELAY_STATUS", "NAMED_VALUE_FLOAT",
]
SERVO_CHANNELS = 16

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NOMAD-GW] %(levelname)s %(message)s",
    stream=sys.stderr,  # keep stdout clean: stdout carries NDJSON data only
)
log = logging.getLogger("nomad_gateway")


class NomadTelemetryGateway:
    """Connects to the K30, requests telemetry streams, emits NDJSON."""

    def __init__(self, connection_string: str = CONNECTION_STRING,
                 baud: int = BAUD_RATE) -> None:
        self.conn_string = connection_string
        self.baud = baud
        self.vehicle = None
        self._batt_alarm_state = "OK"  # OK -> WARN -> CRIT, alert on change

    # -- connection lifecycle ------------------------------------------------

    def connect(self) -> None:
        """Block until a heartbeat is received, retrying indefinitely."""
        while True:
            try:
                log.info("Connecting on %s ...", self.conn_string)
                self.vehicle = mavutil.mavlink_connection(
                    self.conn_string, baud=self.baud
                )
                self.vehicle.wait_heartbeat(timeout=HEARTBEAT_TIMEOUT_S)
                log.info(
                    "Heartbeat OK. system=%d component=%d",
                    self.vehicle.target_system,
                    self.vehicle.target_component,
                )
                self._request_streams()
                return
            except Exception as exc:  # noqa: BLE001 - keep gateway alive
                log.error("Connect failed (%s); retry in %ds",
                          exc, RECONNECT_DELAY_S)
                time.sleep(RECONNECT_DELAY_S)

    def _request_streams(self) -> None:
        """Ask the FC for specific message rates (MAV_CMD_SET_MESSAGE_INTERVAL)."""
        for msg_id, interval_us in STREAM_INTERVALS_US.items():
            self.vehicle.mav.command_long_send(
                self.vehicle.target_system,
                self.vehicle.target_component,
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                0,
                msg_id, interval_us, 0, 0, 0, 0, 0,
            )
        log.info("Requested %d telemetry streams", len(STREAM_INTERVALS_US))

    # -- battery alerting (observation only) ---------------------------------

    def _check_battery(self, voltage: float) -> str:
        """Classify pack voltage; log ONLY on state transitions."""
        if voltage < VOLT_CRIT:
            state = "CRIT"
        elif voltage < VOLT_WARN:
            state = "WARN"
        else:
            state = "OK"

        if state != self._batt_alarm_state:
            if state == "WARN":
                log.warning(
                    "Battery LOW: %.1f V (<%.1f V). "
                    "FC failsafe will command RTL.", voltage, VOLT_WARN)
            elif state == "CRIT":
                log.critical(
                    "Battery CRITICAL: %.1f V (<%.1f V). "
                    "FC failsafe will command LAND.", voltage, VOLT_CRIT)
            else:
                log.info("Battery voltage recovered: %.1f V", voltage)
            self._batt_alarm_state = state
        return state

    # -- MAVLink -> NDJSON row (pure, unit-testable) -------------------------

    def to_payload(self, msg) -> dict:
        """Convert one MAVLink message into one NDJSON row."""
        kind = msg.get_type()
        payload = {"ts": round(time.time(), 3), "type": kind}

        if kind == "VFR_HUD":
            payload.update(
                groundspeed_ms=round(msg.groundspeed, 2),
                heading_deg=msg.heading,
                alt_msl_m=round(msg.alt, 2),
                climb_ms=round(msg.climb, 2),
                throttle_pct=msg.throttle,
            )

        elif kind == "GLOBAL_POSITION_INT":
            payload.update(
                lat=msg.lat / 1e7,
                lon=msg.lon / 1e7,
                alt_rel_m=round(msg.relative_alt / 1000.0, 2),
                # flight-controller clock: lets consumers integrate over
                # aircraft time rather than gateway wall-clock time
                time_boot_ms=msg.time_boot_ms,
            )

        elif kind == "SYS_STATUS":
            if msg.voltage_battery != MAV_VOLTAGE_UNKNOWN:
                voltage = msg.voltage_battery / 1000.0  # mV -> V
                payload["battery_voltage_v"] = round(voltage, 2)
                payload["battery_state"] = self._check_battery(voltage)
            if msg.current_battery != MAV_CURRENT_UNKNOWN:
                payload["battery_current_a"] = round(
                    msg.current_battery / 100.0, 1)  # cA -> A

        elif kind == "SERVO_OUTPUT_RAW":
            # index 0 = SERVO1 ... index 8 = SERVO9 (spray pump, SPECS.md)
            payload["servo_raw"] = [
                getattr(msg, "servo%d_raw" % n, 0)
                for n in range(1, SERVO_CHANNELS + 1)
            ]

        elif kind == "RELAY_STATUS":
            payload.update(relay_on=msg.on, relay_present=msg.present)

        elif kind == "NAMED_VALUE_FLOAT":
            name = msg.name
            if isinstance(name, bytes):
                name = name.decode("ascii", "ignore")
            payload.update(name=name.rstrip("\x00"),
                           value=round(float(msg.value), 4))

        return payload

    # -- main loop -----------------------------------------------------------

    def run(self) -> None:
        """Receive loop. Blocking reads with timeout; reconnect on link loss."""
        last_msg_time = time.monotonic()

        while True:
            msg = self.vehicle.recv_match(
                type=FORWARDED_TYPES,
                blocking=True,
                timeout=1.0,
            )

            if msg is None:
                if time.monotonic() - last_msg_time > HEARTBEAT_TIMEOUT_S:
                    log.error("Telemetry silent >%ds - reconnecting",
                              HEARTBEAT_TIMEOUT_S)
                    self.connect()
                    last_msg_time = time.monotonic()
                continue

            last_msg_time = time.monotonic()
            payload = self.to_payload(msg)

            sys.stdout.write(json.dumps(payload) + "\n")
            sys.stdout.flush()


def main() -> None:
    ap = argparse.ArgumentParser(description="NOMAD MAVLink ground gateway")
    ap.add_argument("--connection",
                    default=os.environ.get("NOMAD_GW_CONNECTION", CONNECTION_STRING),
                    help="pymavlink connection string (default %(default)s)")
    ap.add_argument("--baud", type=int, default=BAUD_RATE)
    args = ap.parse_args()

    gateway = NomadTelemetryGateway(args.connection, args.baud)
    gateway.connect()
    try:
        gateway.run()
    except KeyboardInterrupt:
        log.info("Gateway stopped by operator.")


if __name__ == "__main__":
    main()
