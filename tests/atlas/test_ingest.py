"""Step 1 - RECEIVE: DataFlash .bin and gateway NDJSON become the same Track."""

from __future__ import annotations

import hashlib
import json

import pytest

from atlas.ingest import (
    IngestError, SprayerConfig, boundary_from_geojson, capture_stream,
    load_boundary, track_from_dataflash, track_from_ndjson,
)
from atlas.ingest.dataflash_writer import write_dataflash

from conftest import write_ndjson


# --- NDJSON (live gateway stream) -------------------------------------------

def test_ndjson_track_has_every_fix(perfect_ndjson, perfect_samples):
    track = track_from_ndjson(perfect_ndjson)
    assert track.source == "ndjson"
    assert len(track.samples) == len(perfect_samples)
    assert track.rejected_fixes == 0


def test_ndjson_extracts_all_required_fields(perfect_ndjson):
    s = track_from_ndjson(perfect_ndjson).samples[10]
    assert s.t == pytest.approx(2.0, abs=0.01)
    assert 40.41 < s.lat < 40.42 and 68.84 < s.lon < 68.85
    assert s.height_m == pytest.approx(3.0)
    assert s.speed_ms == pytest.approx(7.0)
    assert s.spray_on is True


def test_ndjson_sprayer_state_follows_pump_servo(faulty_ndjson, faulty_samples):
    track = track_from_ndjson(faulty_ndjson)
    assert [s.spray_on for s in track.samples] == [s["spray_on"] for s in faulty_samples]


def test_ndjson_uses_aircraft_clock_for_duration(perfect_ndjson, perfect_samples):
    track = track_from_ndjson(perfect_ndjson)
    assert track.duration_s == pytest.approx(perfect_samples[-1]["t"], abs=0.01)
    assert (track.ended_utc - track.started_utc).total_seconds() == pytest.approx(
        track.duration_s, abs=0.01)


def test_ndjson_log_hash_is_sha256_of_the_file(perfect_ndjson):
    track = track_from_ndjson(perfect_ndjson)
    assert track.log_sha256 == hashlib.sha256(perfect_ndjson.read_bytes()).hexdigest()


def test_ndjson_min_battery_recorded(perfect_ndjson):
    assert 50.0 < track_from_ndjson(perfect_ndjson).min_battery_v < 58.8


def test_ndjson_without_sprayer_signal_is_refused(tmp_path, perfect_samples):
    """No silent guessing: a servo source with no servo rows is an error."""
    path = tmp_path / "old_gateway.ndjson"
    rows = [json.loads(l) for l in open(write_ndjson(tmp_path / "x.ndjson", perfect_samples))]
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            if row["type"] != "SERVO_OUTPUT_RAW":
                fh.write(json.dumps(row) + "\n")
    with pytest.raises(IngestError, match="no sprayer signal"):
        track_from_ndjson(path)


def test_ndjson_speed_source_is_an_explicit_fallback(tmp_path, perfect_samples):
    path = write_ndjson(tmp_path / "x.ndjson", perfect_samples)
    track = track_from_ndjson(path, SprayerConfig(kind="speed"))
    # everything above SPRAY_SPEED_MIN counts, including the hops between passes
    assert sum(s.spray_on for s in track.samples) > sum(
        s["spray_on"] for s in perfect_samples)


def test_ndjson_flow_meter_source(tmp_path, faulty_samples):
    path = write_ndjson(tmp_path / "flow.ndjson", faulty_samples, flow_meter=True)
    track = track_from_ndjson(path, SprayerConfig(kind="flow"))
    assert track.has_flow
    assert [s.spray_on for s in track.samples] == [s["spray_on"] for s in faulty_samples]
    assert track.samples[10].flow_lpm == pytest.approx(4.41, abs=0.01)


def test_ndjson_ignores_non_json_lines(tmp_path, perfect_samples):
    path = write_ndjson(tmp_path / "x.ndjson", perfect_samples)
    path.write_text("[NOMAD-GW] stray log line\n" + path.read_text(encoding="utf-8"),
                    encoding="utf-8")
    assert len(track_from_ndjson(path).samples) == len(perfect_samples)


def test_ndjson_gps_glitch_is_rejected(tmp_path, perfect_samples):
    path = write_ndjson(tmp_path / "x.ndjson", perfect_samples)
    rows = [json.loads(l) for l in open(path)]
    fixes = [r for r in rows if r["type"] == "GLOBAL_POSITION_INT"]
    fixes[50]["lat"] += 0.05  # ~5.5 km jump
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    track = track_from_ndjson(path)
    assert track.rejected_fixes == 2  # the jump out and the jump back
    assert all(abs(s.lat - 40.41) < 0.01 for s in track.samples)


def test_capture_stream_saves_what_was_received(tmp_path):
    out = tmp_path / "logs" / "live.ndjson"
    n = capture_stream(['{"type":"VFR_HUD"}\n', "\n", '{"type":"SYS_STATUS"}\n'], out)
    assert n == 2
    assert out.read_text(encoding="utf-8").count("\n") == 2


# --- DataFlash .bin ----------------------------------------------------------

@pytest.fixture
def faulty_bin(tmp_path, faulty_samples):
    return write_dataflash(faulty_samples, tmp_path / "flight.bin")


def test_dataflash_track_matches_simulated_flight(faulty_bin, faulty_samples):
    track = track_from_dataflash(faulty_bin)
    assert track.source == "dataflash"
    assert len(track.samples) == len(faulty_samples)
    assert [s.spray_on for s in track.samples] == [s["spray_on"] for s in faulty_samples]


def test_dataflash_extracts_all_required_fields(faulty_bin, faulty_samples):
    track = track_from_dataflash(faulty_bin)
    s, ref = track.samples[10], faulty_samples[10]
    assert s.lat == pytest.approx(ref["lat"], abs=1e-7)
    assert s.lon == pytest.approx(ref["lon"], abs=1e-7)
    assert s.height_m == pytest.approx(3.0)
    assert s.speed_ms == pytest.approx(7.0)
    assert track.duration_s == pytest.approx(faulty_samples[-1]["t"], abs=0.01)


def test_dataflash_utc_times_come_from_gps(faulty_bin, faulty_samples):
    track = track_from_dataflash(faulty_bin)
    assert track.started_utc.isoformat().startswith("2026-05-14T05:30:00")
    assert (track.ended_utc - track.started_utc).total_seconds() == pytest.approx(
        faulty_samples[-1]["t"], abs=0.5)


def test_dataflash_log_hash_and_battery(faulty_bin):
    track = track_from_dataflash(faulty_bin)
    assert track.log_sha256 == hashlib.sha256(faulty_bin.read_bytes()).hexdigest()
    assert 50.0 < track.min_battery_v < 58.8


def test_dataflash_configurable_servo_channel(tmp_path, faulty_samples):
    path = write_dataflash(faulty_samples, tmp_path / "ch7.bin", pump_channel=7)
    track = track_from_dataflash(path, SprayerConfig(kind="servo", channel=7))
    assert [s.spray_on for s in track.samples] == [s["spray_on"] for s in faulty_samples]
    # the default channel (9) sits at a constant 1500 in this log: always "on"
    assert all(s.spray_on for s in track_from_dataflash(path).samples)


def test_dataflash_flow_meter(tmp_path, faulty_samples):
    path = write_dataflash(faulty_samples, tmp_path / "flow.bin", flow_meter=True)
    track = track_from_dataflash(path, SprayerConfig(kind="flow"))
    assert track.has_flow
    assert [s.spray_on for s in track.samples] == [s["spray_on"] for s in faulty_samples]


def test_dataflash_without_sprayer_signal_is_refused(faulty_bin):
    with pytest.raises(IngestError, match="no sprayer signal"):
        track_from_dataflash(faulty_bin, SprayerConfig(kind="relay"))


def test_dataflash_rejects_garbage_file(tmp_path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"this is not a dataflash log" * 10)
    with pytest.raises(IngestError):
        track_from_dataflash(bad)


def test_bin_and_ndjson_agree(faulty_bin, faulty_ndjson):
    a, b = track_from_dataflash(faulty_bin), track_from_ndjson(faulty_ndjson)
    assert len(a.samples) == len(b.samples)
    for x, y in zip(a.samples[::50], b.samples[::50]):
        assert x.lat == pytest.approx(y.lat, abs=2e-7)
        assert x.lon == pytest.approx(y.lon, abs=2e-7)
        assert x.spray_on == y.spray_on


# --- sprayer config + boundary ----------------------------------------------

def test_sprayer_config_validates_kind():
    with pytest.raises(ValueError):
        SprayerConfig(kind="guess")


def test_sprayer_config_round_trip():
    cfg = SprayerConfig.from_dict({"kind": "servo", "channel": 7, "unknown": 1})
    assert cfg.channel == 7 and cfg.to_dict()["pwm_on"] == 1150
    assert "channel 7" in cfg.describe()


def test_boundary_from_geojson_feature_collection():
    ring = [[68.0, 40.0], [68.001, 40.0], [68.001, 40.001], [68.0, 40.001], [68.0, 40.0]]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry":
            {"type": "Polygon", "coordinates": [ring]}}]}
    assert boundary_from_geojson(fc).area > 0


def test_boundary_from_qgc_plan(tmp_path):
    plan = {"fileType": "Plan", "geoFence": {"polygons": [{"inclusion": True, "polygon": [
        [40.0, 68.0], [40.0, 68.001], [40.001, 68.001], [40.001, 68.0]]}]}}
    path = tmp_path / "field.plan"
    path.write_text(json.dumps(plan), encoding="utf-8")
    poly = load_boundary(path)
    assert poly.bounds == pytest.approx((68.0, 40.0, 68.001, 40.001))


def test_boundary_rejects_points():
    with pytest.raises(IngestError):
        boundary_from_geojson({"type": "Point", "coordinates": [68.0, 40.0]})
