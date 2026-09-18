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

# --- geodesy ---

def test_haversine_zero_for_same_point():
    assert haversine_m(40.41, 68.84, 40.41, 68.84) == pytest.approx(0.0)

def test_haversine_one_degree_latitude_is_about_111km():
    d = haversine_m(40.0, 68.0, 41.0, 68.0)
    assert 110_000 < d < 112_000

def test_haversine_is_symmetric():
    a = haversine_m(40.41, 68.84, 40.42, 68.85)
    b = haversine_m(40.42, 68.85, 40.41, 68.84)
    assert a == pytest.approx(b)

# --- helpers ---

def pos(lat, lon, ts=1_700_000_000.0):
    return {"type": "GLOBAL_POSITION_INT", "lat": lat, "lon": lon, "ts": ts}

def speed(ms):
    return {"type": "VFR_HUD", "groundspeed_ms": ms}

def batt(v):
    return {"type": "SYS_STATUS", "battery_voltage_v": v}

# --- distance integration ---

def test_no_distance_from_a_single_fix():
    lg = CoverageLogger("test")
    lg.consume(speed(7.0))
    lg.consume(pos(40.410, 68.840))
    assert lg.rec.sprayed_distance_m == pytest.approx(0.0)

def test_distance_accumulates_along_a_track():
    lg = CoverageLogger("test")
    lg.consume(speed(7.0))
    lg.consume(pos(40.4100, 68.840))
    lg.consume(pos(40.4109, 68.840))   # ~100 m north
    assert 90 < lg.rec.sprayed_distance_m < 110

def test_area_is_distance_times_swath():
    lg = CoverageLogger("test", swath_m=7.0)
    lg.consume(speed(7.0))
    lg.consume(pos(40.4100, 68.840))
    lg.consume(pos(40.4190, 68.840))   # ~1000 m
    expected_ha = (lg.rec.sprayed_distance_m * 7.0) / 10_000.0
    assert lg.rec.covered_area_ha == pytest.approx(expected_ha)

def test_chemical_follows_area_and_rate():
    lg = CoverageLogger("test", swath_m=7.0, rate_l_per_ha=15.0)
    lg.consume(speed(7.0))
    lg.consume(pos(40.4100, 68.840))
    lg.consume(pos(40.4190, 68.840))
    assert lg.rec.chemical_applied_l == pytest.approx(
        lg.rec.covered_area_ha * 15.0)

# --- the speed gate ---

def test_movement_below_spray_speed_is_transit_not_coverage():
    lg = CoverageLogger("test")
    lg.consume(speed(0.4))
    lg.consume(pos(40.4100, 68.840))
    lg.consume(pos(40.4109, 68.840))
    assert lg.rec.sprayed_distance_m == pytest.approx(0.0)
    assert lg.rec.transit_distance_m > 50

def test_speed_change_switches_between_sprayed_and_transit():
    lg = CoverageLogger("test")
    lg.consume(speed(7.0))
    lg.consume(pos(40.4100, 68.840))
    lg.consume(pos(40.4109, 68.840))
    sprayed_after_first = lg.rec.sprayed_distance_m

    lg.consume(speed(0.2))
    lg.consume(pos(40.4118, 68.840))

    assert lg.rec.sprayed_distance_m == pytest.approx(sprayed_after_first)
    assert lg.rec.transit_distance_m > 50

# --- robustness ---

def test_gps_glitch_is_rejected_not_counted():
    lg = CoverageLogger("test")
    lg.consume(speed(7.0))
    lg.consume(pos(40.4100, 68.840))
    lg.consume(pos(45.0000, 70.000))
    assert lg.rec.rejected_fixes == 1
    assert lg.rec.sprayed_distance_m == pytest.approx(0.0)

def test_fix_count_tracks_positions_received():
    lg = CoverageLogger("test")
    for i in range(5):
        lg.consume(pos(40.4100 + i * 0.0001, 68.840))
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

# --- output ---

def test_record_serialises_to_json():
    lg = CoverageLogger("North 12")
    lg.consume(speed(7.0))
    lg.consume(pos(40.4100, 68.840))
    lg.consume(pos(40.4109, 68.840))
    blob = json.dumps(lg.rec.as_dict())
    parsed = json.loads(blob)
    assert parsed["field_name"] == "North 12"
    assert parsed["covered_area_ha"] > 0

def test_timestamps_are_recorded():
    lg = CoverageLogger("test")
    lg.consume(pos(40.4100, 68.840, ts=1_700_000_000.0))
    lg.consume(pos(40.4109, 68.840, ts=1_700_000_060.0))
    assert lg.rec.started_utc != ""
    assert lg.rec.ended_utc != ""
    assert lg.rec.ended_utc > lg.rec.started_utc

def test_summary_renders_without_error():
    lg = CoverageLogger("North 12")
    lg.consume(speed(7.0))
    lg.consume(pos(40.4100, 68.840))
    out = lg.summary()
    assert "AS-APPLIED SPRAY RECORD" in out
    assert "North 12" in out
