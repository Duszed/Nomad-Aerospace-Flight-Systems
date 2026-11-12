"""
Nomad Aerospace - As-Applied Coverage Logger
=============================================
Consumes the NDJSON telemetry stream produced by mavlink_ground_gateway.py
and turns it into an as-applied spray record: where the aircraft actually
flew, how much ground it actually covered, and how much chemical it
actually applied.

This is the "VRA database" ingest stage shown in the architecture diagram.
It closes the loop between a planned mission (spray_planner.py) and what
was really delivered to the field - the record a farmer keeps, and the
evidence an agronomist or regulator can audit.

Runs locally. Records are written to a local file. Nothing is transmitted
off-site, consistent with the data-sovereignty position in SPECS.md.

Usage
-----
    # live, piped from the gateway
    python mavlink_ground_gateway.py | python coverage_logger.py --field "North 12"

    # or replay a saved stream
    python coverage_logger.py --field "North 12" < telemetry.ndjson

Sprayer state
-------------
By default spray is inferred from ground speed (the SPRAY_SPEED_MIN gate).
Pass `spray_source` to use what the aircraft actually reported instead:

    {"kind": "servo", "channel": 9, "pwm_on": 1150}   pump PWM (SERVO9)
    {"kind": "relay", "relay": 0}                      relay bit
    {"kind": "flow", "flow_name": "FLOW", "flow_on_lpm": 0.2}   flow meter

With `keep_track=True` the logger also keeps every accepted fix (time,
position, height, speed, sprayer state, flow) in `.track`, which is what
Nomad Atlas uses to compute the as-applied coverage geometry.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone


SWATH_M = 7.0                 # effective spray width, see SPECS.md
RATE_L_PER_HA = 15.0          # nominal application rate
SPRAY_MIN_SPEED_MS = 1.0      # SPRAY_SPEED_MIN 100 cm/s - below this, spray is off
MAX_PLAUSIBLE_STEP_M = 200.0  # reject GPS jumps larger than this
DEFAULT_PUMP_CHANNEL = 9      # SERVO9_FUNCTION,22 - see SPECS.md
DEFAULT_PWM_ON = 1150         # above SERVO min (1100) = pump running
DEFAULT_FLOW_NAME = "FLOW"    # NAMED_VALUE_FLOAT name of a flow meter, L/min
DEFAULT_FLOW_ON_LPM = 0.2


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class CoverageRecord:
    field_name: str
    started_utc: str = ""
    ended_utc: str = ""
    sprayed_distance_m: float = 0.0
    transit_distance_m: float = 0.0
    covered_area_ha: float = 0.0
    chemical_applied_l: float = 0.0
    fix_count: int = 0
    rejected_fixes: int = 0
    min_battery_v: float | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["sprayed_distance_m"] = round(self.sprayed_distance_m, 1)
        d["transit_distance_m"] = round(self.transit_distance_m, 1)
        d["covered_area_ha"] = round(self.covered_area_ha, 4)
        d["chemical_applied_l"] = round(self.chemical_applied_l, 2)
        return d


class CoverageLogger:
    """Builds an as-applied record from a stream of gateway NDJSON rows."""

    def __init__(self, field_name: str, swath_m: float = SWATH_M,
                 rate_l_per_ha: float = RATE_L_PER_HA,
                 spray_source: dict | None = None,
                 keep_track: bool = False) -> None:
        self.rec = CoverageRecord(field_name=field_name)
        self.swath_m = swath_m
        self.rate_l_per_ha = rate_l_per_ha
        self._last_pos: tuple[float, float] | None = None
        self._last_speed: float = 0.0

        # Sprayer observation. kind "speed" (or None) keeps the original
        # behaviour: spray is inferred from the SPRAY_SPEED_MIN gate.
        self.spray_source = dict(spray_source or {"kind": "speed"})
        self._explicit = self.spray_source.get("kind", "speed") != "speed"
        self._spray_on = False        # last state reported by the aircraft
        self._prev_fix_spray = False  # state at the previous accepted fix
        self._flow_lpm: float | None = None
        self.saw_flow = False
        self.saw_spray_signal = False
        self.keep_track = keep_track
        self.track: list[dict] = []

    # -- ingest --------------------------------------------------------------

    def consume(self, row: dict) -> None:
        """Process one NDJSON row from the gateway."""
        kind = row.get("type")

        if kind == "VFR_HUD":
            self._last_speed = row.get("groundspeed_ms", 0.0)

        elif kind == "SYS_STATUS":
            v = row.get("battery_voltage_v")
            if v is not None:
                if self.rec.min_battery_v is None or v < self.rec.min_battery_v:
                    self.rec.min_battery_v = v

        elif kind == "GLOBAL_POSITION_INT":
            self._consume_position(row)

        elif kind == "SERVO_OUTPUT_RAW":
            if self.spray_source.get("kind") == "servo":
                ch = int(self.spray_source.get("channel", DEFAULT_PUMP_CHANNEL))
                raw = row.get("servo_raw") or []
                if 1 <= ch <= len(raw):
                    self.saw_spray_signal = True
                    self._spray_on = raw[ch - 1] >= self.spray_source.get(
                        "pwm_on", DEFAULT_PWM_ON)

        elif kind == "RELAY_STATUS":
            if self.spray_source.get("kind") == "relay":
                bit = int(self.spray_source.get("relay", 0))
                self.saw_spray_signal = True
                self._spray_on = bool((row.get("relay_on", 0) >> bit) & 1)

        elif kind == "NAMED_VALUE_FLOAT":
            if row.get("name") == self.spray_source.get(
                    "flow_name", DEFAULT_FLOW_NAME):
                # a flow meter is recorded whatever decides on/off, so
                # litres can be measured rather than estimated
                self._flow_lpm = float(row.get("value", 0.0))
                self.saw_flow = True
                if self.spray_source.get("kind") == "flow":
                    self.saw_spray_signal = True
                    self._spray_on = self._flow_lpm >= self.spray_source.get(
                        "flow_on_lpm", DEFAULT_FLOW_ON_LPM)

    def _spraying_now(self) -> bool:
        if self._explicit:
            return self._spray_on
        return self._last_speed >= SPRAY_MIN_SPEED_MS

    def _consume_position(self, row: dict) -> None:
        lat, lon = row.get("lat"), row.get("lon")
        if lat is None or lon is None:
            return

        ts = row.get("ts")
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
        if not self.rec.started_utc:
            self.rec.started_utc = stamp
        self.rec.ended_utc = stamp

        self.rec.fix_count += 1

        if self._last_pos is not None:
            step = haversine_m(self._last_pos[0], self._last_pos[1], lat, lon)

            if step > MAX_PLAUSIBLE_STEP_M:
                # GPS glitch - do not let it inflate the coverage record
                self.rec.rejected_fixes += 1
                self._last_pos = (lat, lon)
                return

            # Spray is speed-gated in the flight controller (SPRAY_SPEED_MIN),
            # so distance below that threshold is transit, not treated ground.
            # With an explicit sprayer source, a step counts as sprayed when
            # the sprayer was reported on at the fix that started it.
            sprayed = (self._prev_fix_spray if self._explicit
                       else self._last_speed >= SPRAY_MIN_SPEED_MS)
            if sprayed:
                self.rec.sprayed_distance_m += step
            else:
                self.rec.transit_distance_m += step

        self._last_pos = (lat, lon)
        self._prev_fix_spray = self._spraying_now()
        if self.keep_track:
            boot_ms = row.get("time_boot_ms")
            self.track.append({
                # aircraft clock when available, else gateway wall clock
                "t": boot_ms / 1000.0 if boot_ms is not None else (ts or 0.0),
                "ts": ts,
                "lat": lat,
                "lon": lon,
                "alt_rel_m": row.get("alt_rel_m"),
                "groundspeed_ms": self._last_speed,
                "spray_on": self._prev_fix_spray,
                "flow_lpm": self._flow_lpm,
            })
        self._recompute()

    def _recompute(self) -> None:
        area_m2 = self.rec.sprayed_distance_m * self.swath_m
        self.rec.covered_area_ha = area_m2 / 10_000.0
        self.rec.chemical_applied_l = self.rec.covered_area_ha * self.rate_l_per_ha

    # -- output --------------------------------------------------------------

    def summary(self) -> str:
        r = self.rec
        return "\n".join([
            "AS-APPLIED SPRAY RECORD",
            "=" * 44,
            f"Field                {r.field_name}",
            f"Start (UTC)          {r.started_utc or '-'}",
            f"End   (UTC)          {r.ended_utc or '-'}",
            "-" * 44,
            f"Sprayed distance     {r.sprayed_distance_m:>12.1f} m",
            f"Transit distance     {r.transit_distance_m:>12.1f} m",
            f"Area covered         {r.covered_area_ha:>12.3f} ha",
            f"Chemical applied     {r.chemical_applied_l:>12.2f} L",
            "-" * 44,
            f"GPS fixes used       {r.fix_count:>12d}",
            f"GPS fixes rejected   {r.rejected_fixes:>12d}",
            f"Min battery          "
            f"{(f'{r.min_battery_v:.2f} V' if r.min_battery_v else '-'):>12}",
            "=" * 44,
        ])


def main() -> None:
    ap = argparse.ArgumentParser(description="NOMAD as-applied coverage logger")
    ap.add_argument("--field", required=True, help="field name for the record")
    ap.add_argument("--swath", type=float, default=SWATH_M)
    ap.add_argument("--rate", type=float, default=RATE_L_PER_HA)
    ap.add_argument("--out", type=str, default=None,
                    help="write the record as JSON to this path")
    args = ap.parse_args()

    logger = CoverageLogger(args.field, args.swath, args.rate)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            logger.consume(json.loads(line))
        except json.JSONDecodeError:
            continue  # ignore non-JSON lines (e.g. stray log output)

    print(logger.summary(), file=sys.stderr)

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(logger.rec.as_dict(), fh, indent=2)
        print(f"\nRecord written: {args.out}", file=sys.stderr)
    else:
        print(json.dumps(logger.rec.as_dict()))


if __name__ == "__main__":
    main()
