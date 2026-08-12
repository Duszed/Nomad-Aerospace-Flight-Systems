"""
Nomad Aerospace - Coverage Logger Unit Tests
=============================================
Run with:  pytest test_coverage_logger.py -v

Verifies the as-applied record built from gateway telemetry: distance
integration, the speed gate that separates sprayed ground from transit,
GPS glitch rejection, and the area/chemical arithmetic.
"""

import json
import math

import pytest

from coverage_logger import (
    CoverageLogger,
    haversine_m,
    SWATH_M,
    RATE_L_PER_HA,
)


# --- geodesy ----------------------------------------------------------------

def test_haversine_zero_for_same_point():
    assert haversine_m(40.85, 68.66, 40.85, 68.66) == pytest.approx(0.0)


def test_haversine_one_degree_latitude_is_about_111km():
    d = haversine_m(40.0, 68.0, 41.0, 68.0)
    assert 110_000 < d < 112_000


def test_haversine_is_symmetric():
    a = haversine_m(40.85, 68.66, 40.86, 68.67)
    b = haversine_m(40.86, 68.67, 40.85, 68.66)
    assert a == pytest.approx(b)


# --- helpers ----------------------------------------------------------------

def pos(lat, lon, ts=1_700_000_000.0):
    return {"type": "GLOBAL_POSITION_INT", "lat": lat, "lon": lon, "ts": ts}


def speed(ms):
    return {"type": "VFR_HUD", "groundspeed_ms": ms}


def batt(v):
    return {"type": "SYS_STATUS", "battery_voltage_v": v}


# --- distance integration ---------------------------------------------------

def test_no_distance_from_a_single_fix():
    lg = CoverageLogger("test")
    lg.consume(speed(7.0))
    lg.consume(pos(40.850, 68.660))
    assert lg.rec.sprayed_distance_m == pytest.approx(0.0)


def test_distance_accumulates_along_a_track():
    lg = CoverageLogger("test")
    lg.consume(speed(7.0))
    lg.consume(pos(40.8500, 68.660))
    lg.consume(pos(40.8509, 68.660))   # ~100 m north
    assert 90 < lg.rec.sprayed_distance_m < 110


def test_area_is_distance_times_swath():
    lg = CoverageLogger("test", swath_m=7.0)
    lg.consume(speed(7.0))
    lg.consume(pos(40.8500, 68.660))
    lg.consume(pos(40.8590, 68.660))   # ~1000 m
    expected_ha = (lg.rec.sprayed_distance_m * 7.0) / 10_000.0
    assert lg.rec.covered_area_ha == pytest.approx(expected_ha)


def test_chemical_follows_area_and_rate():
    lg = CoverageLogger("test", swath_m=7.0, rate_l_per_ha=15.0)
    lg.consume(speed(7.0))
    lg.consume(pos(40.8500, 68.660))
    lg.consume(pos(40.8590, 68.660))
    assert lg.rec.chemical_applied_l == pytest.approx(
        lg.rec.covered_area_ha * 15.0)


# --- the speed gate ---------------------------------------------------------

def test_movement_below_spray_speed_is_transit_not_coverage():
    """SPRAY_SPEED_MIN gates the pump; slow ground must not count as sprayed."""
    lg = CoverageLogger("test")
    lg.consume(speed(0.4))             # below the 1.0 m/s gate
    lg.consume(pos(40.8500, 68.660))
    lg.consume(pos(40.8509, 68.660))
    assert lg.rec.sprayed_distance_m == pytest.approx(0.0)
    assert lg.rec.transit_distance_m > 50


def test_speed_change_switches_between_sprayed_and_transit():
    lg = CoverageLogger("test")
    lg.consume(speed(7.0))
    lg.consume(pos(40.8500, 68.660))
    lg.consume(pos(40.8509, 68.660))   # sprayed leg
    sprayed_after_first = lg.rec.sprayed_distance_m

    lg.consume(speed(0.2))             # slowed for a turn
    lg.consume(pos(40.8518, 68.660))   # transit leg

    assert lg.rec.sprayed_distance_m == pytest.approx(sprayed_after_first)
    assert lg.rec.transit_distance_m > 50


# --- robustness -------------------------------------------------------------

def test_gps_glitch_is_rejected_not_counted():
    """A single implausible jump must not inflate the as-applied record."""
    lg = CoverageLogger("test")
    lg.consume(speed(7.0))
    lg.consume(pos(40.8500, 68.660))
    lg.consume(pos(45.0000, 70.000))   # hundreds of km - clearly bad
    assert lg.rec.rejected_fixes == 1
    assert lg.rec.sprayed_distance_m == pytest.approx(0.0)


def test_fix_count_tracks_positions_received():
    lg = CoverageLogger("test")
    for i in range(5):
        lg.consume(pos(40.8500 + i * 0.0001, 68.660))
    assert lg.rec.fix_count == 5


def test_missing_lat_lon_is_ignored_safely():
    lg = CoverageLogger("test")
    lg.consume({"type": "GLOBAL_POSITION_INT", "ts": 1.0})
    assert lg.rec.fix_count == 0


def test_min_battery_is_tracked():
    lg = CoverageLogger("test")
    for v in (58.8, 52.0, 47.1, 49.0):
        lg.consume(batt(v))
    assert lg.rec.min_battery_v == pytest.approx(47.1)


def test_unknown_message_types_are_ignored():
    lg = CoverageLogger("test")
    lg.consume({"type": "SOMETHING_ELSE", "value": 1})
    assert lg.rec.fix_count == 0


# --- output -----------------------------------------------------------------

def test_record_serialises_to_json():
    lg = CoverageLogger("North 12")
    lg.consume(speed(7.0))
    lg.consume(pos(40.8500, 68.660))
    lg.consume(pos(40.8509, 68.660))
    blob = json.dumps(lg.rec.as_dict())
    parsed = json.loads(blob)
    assert parsed["field_name"] == "North 12"
    assert parsed["covered_area_ha"] > 0


def test_timestamps_are_recorded():
    lg = CoverageLogger("test")
    lg.consume(pos(40.8500, 68.660, ts=1_700_000_000.0))
    lg.consume(pos(40.8509, 68.660, ts=1_700_000_060.0))
    assert lg.rec.started_utc != ""
    assert lg.rec.ended_utc != ""
    assert lg.rec.ended_utc > lg.rec.started_utc


def test_summary_renders_without_error():
    lg = CoverageLogger("North 12")
    lg.consume(speed(7.0))
    lg.consume(pos(40.8500, 68.660))
    out = lg.summary()
    assert "AS-APPLIED SPRAY RECORD" in out
    assert "North 12" in out
