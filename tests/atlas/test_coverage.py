"""Step 2 - COMPUTE: sprayed area, coverage, missed strips, overlap, litres."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon, shape

from atlas.compute import (
    LITRES_ESTIMATED, LITRES_MEASURED, LITRES_OPERATOR, compute_coverage, utm_epsg,
)
from atlas.ingest import SprayerConfig, track_from_ndjson
from atlas.ingest.track import Sample, Track

from conftest import write_ndjson

SWATH, RATE = 7.0, 15.0


def make_track(points, spray=True, speed=5.0, flow=None):
    """A track from (lon, lat) points one second apart."""
    samples = [Sample(t=float(i), lat=lat, lon=lon, height_m=3.0, speed_ms=speed,
                      spray_on=spray, flow_lpm=flow)
               for i, (lon, lat) in enumerate(points)]
    return Track(samples=samples, source="ndjson", sprayer=SprayerConfig(),
                 log_name="t", log_sha256="0" * 64, has_flow=flow is not None)


# --- projection --------------------------------------------------------------

def test_utm_zone_for_syrdarya_is_42n():
    assert utm_epsg(68.84, 40.41) == 32642


def test_utm_zone_tashkent_and_southern_hemisphere():
    assert utm_epsg(69.24, 41.31) == 32642
    assert utm_epsg(63.9, 40.1) == 32641      # Bukhara side of the 66E boundary
    assert utm_epsg(-58.4, -34.6) == 32721


# --- the demo field ----------------------------------------------------------

def test_field_area_is_about_4_2_ha(perfect_ndjson, demo_boundary):
    r = compute_coverage(track_from_ndjson(perfect_ndjson), demo_boundary, SWATH, RATE)
    # planner uses a flat-earth approximation; true (UTM) area is within 0.5 %
    assert r.field_area_ha == pytest.approx(4.2, rel=0.005)
    assert r.utm_epsg == 32642


def test_perfect_mission_covers_the_whole_field(perfect_ndjson, demo_boundary):
    r = compute_coverage(track_from_ndjson(perfect_ndjson), demo_boundary, SWATH, RATE)
    assert r.passes == 20
    assert r.coverage_pct == pytest.approx(100.0, abs=0.05)
    assert r.missed_strips == 0 and r.missed_area_ha == 0
    assert r.overlap_area_ha == pytest.approx(0.0, abs=0.001)
    assert r.outside_field_ha == pytest.approx(0.0, abs=0.002)
    # the planner spaces passes with a flat-earth approximation, so they sit
    # ~1 cm further apart than 7 true metres: sprayed is 0.1 % under the field
    assert r.sprayed_area_ha == pytest.approx(r.field_area_ha, rel=0.003)


def test_perfect_mission_distances(perfect_ndjson, demo_boundary):
    r = compute_coverage(track_from_ndjson(perfect_ndjson), demo_boundary, SWATH, RATE)
    assert r.sprayed_distance_m == pytest.approx(20 * 300, rel=0.005)
    assert r.transit_distance_m == pytest.approx(19 * 7, rel=0.01)
    assert r.avg_spray_speed_ms == pytest.approx(7.0)
    assert r.avg_spray_height_m == pytest.approx(3.0)


def test_faulty_mission_reports_missed_and_overlap(faulty_ndjson, demo_boundary):
    """Demo faults: 60 m pump dropout on pass 8, pass 14 flown 2.5 m off-line."""
    r = compute_coverage(track_from_ndjson(faulty_ndjson), demo_boundary, SWATH, RATE)
    dropout_ha = 60 * 7 / 10_000
    offline_ha = 300 * 2.5 / 10_000
    assert r.missed_strips == 2
    assert r.missed_area_ha == pytest.approx(dropout_ha + offline_ha, rel=0.03)
    assert r.overlap_area_ha == pytest.approx(offline_ha, rel=0.03)
    assert r.coverage_pct == pytest.approx(
        100 * (1 - (dropout_ha + offline_ha) / r.field_area_ha), abs=0.1)
    assert 96.5 < r.coverage_pct < 98.0


def test_covered_plus_missed_equals_field(faulty_ndjson, demo_boundary):
    r = compute_coverage(track_from_ndjson(faulty_ndjson), demo_boundary, SWATH, RATE)
    assert r.covered_in_field_ha + r.missed_area_ha == pytest.approx(r.field_area_ha)


def test_geometry_is_inside_the_field_bounds(faulty_ndjson, demo_boundary):
    r = compute_coverage(track_from_ndjson(faulty_ndjson), demo_boundary, SWATH, RATE)
    gj = r.geojson()
    assert set(gj) == {"field", "sprayed", "missed", "overlap"}
    grown = demo_boundary.buffer(1e-5)
    assert grown.contains(shape(gj["missed"]))
    assert grown.contains(shape(gj["overlap"]))


# --- small synthetic cases ---------------------------------------------------

LON0, LAT0 = 68.84, 40.41
DLAT = 100 / 111_000.0     # ~100 m north


def test_single_pass_area_is_length_times_swath():
    track = make_track([(LON0, LAT0), (LON0, LAT0 + DLAT)])
    field = Polygon([(LON0 - 0.01, LAT0 - 0.01), (LON0 + 0.01, LAT0 - 0.01),
                     (LON0 + 0.01, LAT0 + 0.02), (LON0 - 0.01, LAT0 + 0.02)])
    r = compute_coverage(track, field, SWATH, RATE)
    assert r.sprayed_area_ha * 10_000 == pytest.approx(r.sprayed_distance_m * SWATH, rel=1e-3)
    assert r.passes == 1 and r.overlap_area_ha == 0


def test_same_line_flown_twice_is_all_overlap():
    track = make_track([(LON0, LAT0), (LON0, LAT0 + DLAT), (LON0, LAT0)])
    field = Polygon([(LON0 - 0.01, LAT0 - 0.01), (LON0 + 0.01, LAT0 - 0.01),
                     (LON0 + 0.01, LAT0 + 0.02), (LON0 - 0.01, LAT0 + 0.02)])
    r = compute_coverage(track, field, SWATH, RATE)
    assert r.passes == 2
    assert r.overlap_area_ha == pytest.approx(r.sprayed_area_ha, rel=0.02)


def test_sprayer_off_means_nothing_sprayed():
    track = make_track([(LON0, LAT0), (LON0, LAT0 + DLAT)], spray=False)
    field = Polygon([(LON0 - 0.001, LAT0), (LON0 + 0.001, LAT0),
                     (LON0 + 0.001, LAT0 + DLAT), (LON0 - 0.001, LAT0 + DLAT)])
    r = compute_coverage(track, field, SWATH, RATE)
    assert r.sprayed_area_ha == 0 and r.coverage_pct == 0
    assert r.missed_area_ha == pytest.approx(r.field_area_ha)
    assert r.litres_applied == 0 and r.actual_rate_l_ha == 0


def test_spray_outside_the_field_is_reported_separately():
    track = make_track([(LON0, LAT0), (LON0, LAT0 + DLAT)])
    # field covers only the southern half of the pass
    field = Polygon([(LON0 - 0.001, LAT0), (LON0 + 0.001, LAT0),
                     (LON0 + 0.001, LAT0 + DLAT / 2), (LON0 - 0.001, LAT0 + DLAT / 2)])
    r = compute_coverage(track, field, SWATH, RATE)
    assert r.outside_field_ha == pytest.approx(r.sprayed_area_ha / 2, rel=0.02)


def test_gap_in_the_data_is_never_bridged():
    pts = [(LON0, LAT0), (LON0, LAT0 + DLAT)]
    samples = make_track(pts).samples
    late = Sample(t=500.0, lat=LAT0 + 2 * DLAT, lon=LON0, height_m=3.0, speed_ms=5.0,
                  spray_on=True)
    track = make_track(pts)
    track.samples = samples + [late]
    field = Polygon([(LON0 - 0.01, LAT0 - 0.01), (LON0 + 0.01, LAT0 - 0.01),
                     (LON0 + 0.01, LAT0 + 0.02), (LON0 - 0.01, LAT0 + 0.02)])
    r = compute_coverage(track, field, SWATH, RATE)
    assert r.sprayed_distance_m == pytest.approx(100, rel=0.01)


def test_rejects_bad_parameters(perfect_ndjson, demo_boundary):
    track = track_from_ndjson(perfect_ndjson)
    with pytest.raises(ValueError):
        compute_coverage(track, demo_boundary, 0, RATE)
    with pytest.raises(ValueError):
        compute_coverage(track, demo_boundary, SWATH, 0)


# --- litres ------------------------------------------------------------------

def test_litres_are_labelled_estimated_without_a_flow_meter(perfect_ndjson, demo_boundary):
    r = compute_coverage(track_from_ndjson(perfect_ndjson), demo_boundary, SWATH, RATE)
    assert r.litres_basis == LITRES_ESTIMATED
    assert r.litres_applied == pytest.approx(r.field_area_ha * RATE, rel=0.005)
    assert r.actual_rate_l_ha == pytest.approx(RATE, rel=0.005)
    assert abs(r.rate_deviation_pct) < 0.5


def test_overlap_raises_the_actual_rate(faulty_ndjson, demo_boundary):
    r = compute_coverage(track_from_ndjson(faulty_ndjson), demo_boundary, SWATH, RATE)
    assert r.actual_rate_l_ha > RATE
    assert r.rate_deviation_pct > 0


def test_flow_meter_litres_are_measured(tmp_path, perfect_samples, demo_boundary):
    path = write_ndjson(tmp_path / "flow.ndjson", perfect_samples, flow_meter=True)
    r = compute_coverage(track_from_ndjson(path), demo_boundary, SWATH, RATE)
    assert r.litres_basis == LITRES_MEASURED
    # 4.41 L/min for 20 passes x 300 m at 7 m/s
    assert r.litres_applied == pytest.approx(4.41 * (20 * 300 / 7) / 60, rel=0.01)


def test_operator_reported_tank_volume(perfect_ndjson, demo_boundary):
    r = compute_coverage(track_from_ndjson(perfect_ndjson), demo_boundary, SWATH, RATE,
                         tank_litres_used=58.0)
    assert r.litres_basis == LITRES_OPERATOR
    assert r.litres_applied == 58.0
    assert r.rate_deviation_pct < 0   # under-applied against the 15 L/ha plan


def test_numbers_are_rounded_and_json_safe(faulty_ndjson, demo_boundary):
    import json
    r = compute_coverage(track_from_ndjson(faulty_ndjson), demo_boundary, SWATH, RATE)
    n = r.numbers()
    assert n["coverage_pct"] == round(n["coverage_pct"], 2)
    assert n["litres_basis"] == "estimated"
    json.dumps({"numbers": n, "geometry": r.geojson()}, allow_nan=False)
