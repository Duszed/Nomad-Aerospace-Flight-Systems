"""
Nomad Aerospace - Spray Planner Unit Tests
===========================================
Run with:  pytest test_spray_planner.py -v

Verifies the mission geometry and consumable calculations independently
of the planner implementation, and cross-checks the result against the
operational assumption published in our materials (20 ha per aircraft
per day).
"""

import math

import pytest

from spray_planner import (
    plan_mission,
    to_qgc_waypoints,
    TANK_LITRES,
    CRUISE_SPEED_MS,
)


# --- geometry ---------------------------------------------------------------

def test_pass_count_covers_full_width():
    """Passes x swath must be >= field width, or part of the field is missed."""
    plan = plan_mission(area_ha=10, field_width_m=300, swath_m=7)
    assert plan.passes * plan.swath_m >= 300


def test_pass_length_matches_area():
    """area = width x length, so length must follow from the inputs."""
    plan = plan_mission(area_ha=12, field_width_m=300)
    assert plan.pass_length_m == pytest.approx(400.0)


def test_exact_multiple_needs_no_extra_pass():
    plan = plan_mission(area_ha=10, field_width_m=70, swath_m=7)
    assert plan.passes == 10


def test_partial_swath_rounds_up():
    """71 m of width with a 7 m swath needs 11 passes, not 10."""
    plan = plan_mission(area_ha=10, field_width_m=71, swath_m=7)
    assert plan.passes == 11


def test_narrower_swath_means_more_passes():
    wide = plan_mission(area_ha=10, field_width_m=300, swath_m=9)
    narrow = plan_mission(area_ha=10, field_width_m=300, swath_m=5)
    assert narrow.passes > wide.passes
    assert narrow.total_distance_m > wide.total_distance_m


# --- consumables ------------------------------------------------------------

def test_chemical_scales_with_area():
    plan = plan_mission(area_ha=12, field_width_m=300, rate_l_per_ha=15)
    assert plan.chemical_required_l == pytest.approx(180.0)


def test_tank_refills_excludes_the_initial_fill():
    """180 L needs 6 tank-loads => 5 REFILLS after the first fill."""
    plan = plan_mission(area_ha=12, field_width_m=300, rate_l_per_ha=15)
    assert plan.tank_refills == 5


def test_small_field_needs_no_refill():
    """A field needing under one tank should require zero refills."""
    plan = plan_mission(area_ha=1, field_width_m=100, rate_l_per_ha=15)
    assert plan.chemical_required_l <= TANK_LITRES
    assert plan.tank_refills == 0


# --- timing -----------------------------------------------------------------

def test_flight_time_is_at_least_distance_over_speed():
    """Turn penalties can only ADD to the pure-cruise time, never subtract."""
    plan = plan_mission(area_ha=12, field_width_m=300)
    pure_cruise_min = (plan.total_distance_m / CRUISE_SPEED_MS) / 60.0
    assert plan.flight_time_min >= pure_cruise_min


def test_daily_rate_assumption_is_conservative():
    """Cross-check: our published figure is 20 ha per aircraft per day.

    If 20 ha required more airborne time than a realistic working day of
    flying (allowing for refills, battery swaps, transit and wind holds),
    the published figure would be optimistic. It should sit well inside
    a day's operation.
    """
    plan = plan_mission(area_ha=20, field_width_m=400)
    assert plan.flight_time_min < 180, (
        "20 ha should need well under 3 h airborne for the daily "
        "figure to remain conservative"
    )


# --- export -----------------------------------------------------------------

def test_waypoints_are_generated_two_per_pass():
    plan = plan_mission(area_ha=10, field_width_m=70, swath_m=7)
    assert len(plan.waypoints) == plan.passes * 2


def test_waypoint_sequence_is_contiguous():
    plan = plan_mission(area_ha=5, field_width_m=70, swath_m=7)
    seqs = [wp.seq for wp in plan.waypoints]
    assert seqs == list(range(len(seqs)))


def test_passes_alternate_direction():
    """Boustrophedon: consecutive passes must run in opposite directions."""
    plan = plan_mission(area_ha=5, field_width_m=70, swath_m=7)
    first_pass = plan.waypoints[0], plan.waypoints[1]
    second_pass = plan.waypoints[2], plan.waypoints[3]

    first_goes_north = first_pass[1].lat > first_pass[0].lat
    second_goes_north = second_pass[1].lat > second_pass[0].lat
    assert first_goes_north != second_goes_north


def _rows(text):
    return [line.split("\t") for line in text.splitlines()[1:]]


def test_qgc_export_has_valid_header_and_row_count():
    """Home + takeoff + (waypoint + sprayer command) per point + RTL."""
    plan = plan_mission(area_ha=5, field_width_m=70, swath_m=7)
    text = to_qgc_waypoints(plan)
    lines = text.splitlines()
    assert lines[0] == "QGC WPL 110"
    assert len(lines) == 1 + 2 + 2 * len(plan.waypoints) + 1


def test_mission_starts_with_home_then_takeoff_and_ends_with_rtl():
    """Row 0 is home (overwritten on arming), so it must not be a spray point."""
    rows = _rows(to_qgc_waypoints(plan_mission(area_ha=5, field_width_m=70, swath_m=7)))
    assert rows[0][0] == "0" and rows[0][1] == "1"   # seq 0, current = home
    assert rows[1][3] == "22"                        # NAV_TAKEOFF
    assert rows[-1][3] == "20"                       # NAV_RETURN_TO_LAUNCH


def test_spray_legs_use_terrain_frame():
    """Frame 10 makes the LiDAR hold height above the crop, not above home."""
    rows = _rows(to_qgc_waypoints(plan_mission(area_ha=5, field_width_m=70, swath_m=7)))
    legs = [r for r in rows[2:-1] if r[3] == "16"]
    assert legs and all(r[2] == "10" for r in legs)


def test_sprayer_switches_on_and_off_once_per_pass():
    plan = plan_mission(area_ha=5, field_width_m=70, swath_m=7)
    rows = _rows(to_qgc_waypoints(plan))
    sprayer = [r[4] for r in rows if r[3] == "216"]
    assert sprayer == ["1", "0"] * plan.passes


def test_seq_numbers_are_contiguous_in_export():
    rows = _rows(to_qgc_waypoints(plan_mission(area_ha=5, field_width_m=70, swath_m=7)))
    assert [int(r[0]) for r in rows] == list(range(len(rows)))


def test_first_pass_sits_half_a_swath_inside_the_edge():
    """Centreline at swath/2 so the outer swath covers the boundary."""
    plan = plan_mission(area_ha=5, field_width_m=70, swath_m=7)
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(plan.waypoints[0].lat))
    first = plan_mission(area_ha=5, field_width_m=70, swath_m=7, origin_lon=0.0).waypoints[0]
    assert first.lon * m_per_deg_lon == pytest.approx(3.5, abs=0.05)


def test_last_pass_stays_inside_the_field():
    plan = plan_mission(area_ha=10, field_width_m=71, swath_m=7, origin_lon=0.0)
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(plan.waypoints[0].lat))
    last = plan.waypoints[-1]
    assert last.lon * m_per_deg_lon <= 71 - 3.5 + 0.05


# --- input validation -------------------------------------------------------

@pytest.mark.parametrize("area,width,swath", [
    (0, 300, 7),
    (-5, 300, 7),
    (10, 0, 7),
    (10, 300, 0),
])
def test_invalid_inputs_are_rejected(area, width, swath):
    with pytest.raises(ValueError):
        plan_mission(area_ha=area, field_width_m=width, swath_m=swath)
