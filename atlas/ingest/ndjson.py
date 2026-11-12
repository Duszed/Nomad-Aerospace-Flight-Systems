"""Ingest path (b): live MAVLink via the existing gateway's NDJSON stream.

Builds on telemetry/coverage_logger.py (extended, not rewritten): the logger
does the row parsing, GPS-glitch rejection and sprayer-state tracking, and
hands back the accepted fixes as a track.

A live stream is first captured to a file (`capture_stream`) so that the
record can carry the SHA-256 of exactly what was received.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Iterable

from .._telemetry import coverage_logger
from .sprayer import SprayerConfig
from .track import IngestError, Sample, Track, sha256_file


def capture_stream(lines: Iterable[str] | IO[str], out_path: Path) -> int:
    """Write an NDJSON stream to `out_path` as received. Returns rows written."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            fh.write(line + "\n")
            n += 1
    return n


def track_from_ndjson(path: Path, sprayer: SprayerConfig | None = None) -> Track:
    path = Path(path)
    sprayer = sprayer or SprayerConfig()
    logger = coverage_logger.CoverageLogger(
        field_name="", spray_source=sprayer.to_dict(), keep_track=True)

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray log output mixed into the stream
            if isinstance(row, dict):
                logger.consume(row)

    if len(logger.track) < 2:
        raise IngestError(f"{path.name}: fewer than two position fixes in the stream")
    if sprayer.kind != "speed" and not logger.saw_spray_signal:
        raise IngestError(
            f"{path.name}: no sprayer signal found for source '{sprayer.describe()}'. "
            "Check the sprayer source configuration (or use kind 'speed' to infer "
            "spraying from ground speed).")

    samples = [
        Sample(t=r["t"], lat=r["lat"], lon=r["lon"], height_m=r["alt_rel_m"],
               speed_ms=r["groundspeed_ms"], spray_on=r["spray_on"],
               flow_lpm=r["flow_lpm"])
        for r in logger.track
    ]

    # Wall-clock start comes from the gateway; duration from the aircraft clock.
    started = ended = None
    first_ts = next((r["ts"] for r in logger.track if r["ts"]), None)
    if first_ts:
        started = datetime.fromtimestamp(first_ts, tz=timezone.utc)
        ended = started + timedelta(seconds=samples[-1].t - samples[0].t)

    return Track(
        samples=samples, source="ndjson", sprayer=sprayer,
        log_name=path.name, log_sha256=sha256_file(path),
        has_flow=logger.saw_flow, started_utc=started, ended_utc=ended,
        rejected_fixes=logger.rec.rejected_fixes,
        min_battery_v=logger.rec.min_battery_v,
    )
