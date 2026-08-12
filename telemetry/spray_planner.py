"""
Nomad Aerospace - Spray Mission Planner
========================================
Generates the back-and-forth (boustrophedon) flight pattern the K30 flies
across a field, and computes the operational figures a farmer actually
needs: number of passes, total flight distance, tank refills required,
and estimated time on task.

Runs entirely offline - no cellular or internet connection, consistent
with the Field Operations section of SPECS.md. Field boundaries come
from a locally stored polygon captured once per field.

Usage
-----
    python spray_planner.py                 # demo with a sample 12 ha field
    python spray_planner.py --area 25 --width 400 --swath 7

Outputs a mission summary plus a waypoint list that can be written to a
QGroundControl .waypoints file for upload over the SIYI MK15 link.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field


# --- Platform constants (see SPECS.md) --------------------------------------
TANK_LITRES = 30.0
CRUISE_SPEED_MS = 7.0          # WPNAV_SPEED 700 cm/s
DEFAULT_SWATH_M = 7.0          # effective spray width
DEFAULT_RATE_L_PER_HA = 15.0   # typical application rate
TURN_PENALTY_S = 8.0           # deceleration, turn, re-acceleration per pass end


@dataclass
class Waypoint:
    seq: int
    lat: float
    lon: float
    alt_m: float
    spray_on: bool


@dataclass
class MissionPlan:
    field_area_ha: float
    swath_m: float
    rate_l_per_ha: float
    passes: int
    pass_length_m: float
    total_distance_m: float
    chemical_required_l: float
    tank_refills: int
    flight_time_min: float
    waypoints: list[Waypoint] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "NOMAD K30 - SPRAY MISSION PLAN",
            "=" * 46,
            f"Field area            {self.field_area_ha:>10.2f} ha",
            f"Swath width           {self.swath_m:>10.2f} m",
            f"Application rate      {self.rate_l_per_ha:>10.2f} L/ha",
            "-" * 46,
            f"Passes required       {self.passes:>10d}",
            f"Length per pass       {self.pass_length_m:>10.1f} m",
            f"Total flight distance {self.total_distance_m / 1000:>10.2f} km",
            "-" * 46,
            f"Chemical required     {self.chemical_required_l:>10.1f} L",
            f"Tank refills          {self.tank_refills:>10d}   ({TANK_LITRES:.0f} L tank)",
            f"Est. time on task     {self.flight_time_min:>10.1f} min",
            f"Waypoints generated   {len(self.waypoints):>10d}",
            "=" * 46,
        ]
        return "\n".join(lines)


def plan_mission(area_ha: float,
                 field_width_m: float,
                 swath_m: float = DEFAULT_SWATH_M,
                 rate_l_per_ha: float = DEFAULT_RATE_L_PER_HA,
                 alt_m: float = 3.0,
                 origin_lat: float = 40.8500,
                 origin_lon: float = 68.6600) -> MissionPlan:
    """Plan a boustrophedon spray mission over a rectangular field.

    area_ha        field area in hectares
    field_width_m  width of the field perpendicular to the flight passes
    swath_m        effective spray width of the aircraft
    """
    if area_ha <= 0 or field_width_m <= 0 or swath_m <= 0:
        raise ValueError("area, field width and swath must all be positive")

    area_m2 = area_ha * 10_000.0
    pass_length_m = area_m2 / field_width_m

    # Number of parallel passes needed to cover the full width
    passes = math.ceil(field_width_m / swath_m)

    # Flight distance = passes along the field + the cross-field turns between them
    along = passes * pass_length_m
    turns = (passes - 1) * swath_m
    total_distance_m = along + turns

    chemical_required_l = area_ha * rate_l_per_ha
    tank_refills = max(0, math.ceil(chemical_required_l / TANK_LITRES) - 1)

    flight_time_s = (total_distance_m / CRUISE_SPEED_MS) + (passes - 1) * TURN_PENALTY_S
    flight_time_min = flight_time_s / 60.0

    waypoints = _build_waypoints(passes, pass_length_m, swath_m,
                                 alt_m, origin_lat, origin_lon)

    return MissionPlan(
        field_area_ha=area_ha,
        swath_m=swath_m,
        rate_l_per_ha=rate_l_per_ha,
        passes=passes,
        pass_length_m=pass_length_m,
        total_distance_m=total_distance_m,
        chemical_required_l=chemical_required_l,
        tank_refills=tank_refills,
        flight_time_min=flight_time_min,
        waypoints=waypoints,
    )


def _build_waypoints(passes: int, pass_length_m: float, swath_m: float,
                     alt_m: float, origin_lat: float,
                     origin_lon: float) -> list[Waypoint]:
    """Alternating-direction waypoints, spray ON along passes, OFF in turns."""
    # local flat-earth approximation, adequate for a single field
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(origin_lat))

    wps: list[Waypoint] = []
    seq = 0
    for i in range(passes):
        offset_m = i * swath_m
        lon = origin_lon + offset_m / m_per_deg_lon

        # even passes run "north", odd passes run "south" - boustrophedon
        if i % 2 == 0:
            start_m, end_m = 0.0, pass_length_m
        else:
            start_m, end_m = pass_length_m, 0.0

        for point_m, spray in ((start_m, True), (end_m, True)):
            lat = origin_lat + point_m / m_per_deg_lat
            wps.append(Waypoint(seq=seq, lat=round(lat, 7), lon=round(lon, 7),
                                alt_m=alt_m, spray_on=spray))
            seq += 1
    return wps


def to_qgc_waypoints(plan: MissionPlan) -> str:
    """Export as a QGroundControl WPL 110 file for upload to the Cube."""
    out = ["QGC WPL 110"]
    for wp in plan.waypoints:
        # frame 3 = MAV_FRAME_GLOBAL_RELATIVE_ALT, command 16 = NAV_WAYPOINT
        out.append(
            f"{wp.seq}\t0\t3\t16\t0\t0\t0\t0\t"
            f"{wp.lat:.7f}\t{wp.lon:.7f}\t{wp.alt_m:.1f}\t1"
        )
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="NOMAD K30 spray mission planner")
    ap.add_argument("--area", type=float, default=12.0, help="field area (ha)")
    ap.add_argument("--width", type=float, default=300.0, help="field width (m)")
    ap.add_argument("--swath", type=float, default=DEFAULT_SWATH_M,
                    help="spray swath width (m)")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE_L_PER_HA,
                    help="application rate (L/ha)")
    ap.add_argument("--export", type=str, default=None,
                    help="write QGC .waypoints file to this path")
    args = ap.parse_args()

    plan = plan_mission(args.area, args.width, args.swath, args.rate)
    print(plan.summary())

    if args.export:
        with open(args.export, "w") as fh:
            fh.write(to_qgc_waypoints(plan))
        print(f"\nWaypoint file written: {args.export}")
    else:
        print("\nFirst 6 waypoints:")
        for wp in plan.waypoints[:6]:
            state = "SPRAY" if wp.spray_on else "off"
            print(f"  #{wp.seq:<3} {wp.lat:.6f}, {wp.lon:.6f}  "
                  f"{wp.alt_m:.1f} m  [{state}]")


if __name__ == "__main__":
    main()
