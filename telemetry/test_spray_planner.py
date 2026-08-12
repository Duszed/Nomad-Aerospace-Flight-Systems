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


def test_qgc_export_has_valid_header_and_row_count():
    plan = plan_mission(area_ha=5, field_width_m=70, swath_m=7)
    text = to_qgc_waypoints(plan)
    lines = text.splitlines()
    assert lines[0] == "QGC WPL 110"
    assert len(lines) == len(plan.waypoints) + 1


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
