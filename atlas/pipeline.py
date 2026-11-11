"""Steps 1-3 in one call: log file -> track -> coverage -> signed record.

Runs entirely offline.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Polygon
from sqlalchemy.orm import Session

from .compute.coverage import CoverageResult, compute_coverage
from .config import Settings
from .db import RecordVersion
from .ingest import SprayerConfig, Track, track_from_dataflash, track_from_ndjson
from .ingest.track import IngestError
from .records import JobIn, Signer, create_record


@dataclass
class Processed:
    record: RecordVersion
    track: Track
    coverage: CoverageResult


def read_track(log_path: Path, sprayer: SprayerConfig | None = None) -> Track:
    """Pick the ingest path from the file type."""
    suffix = Path(log_path).suffix.lower()
    if suffix == ".bin":
        return track_from_dataflash(log_path, sprayer)
    if suffix in (".ndjson", ".jsonl"):
        return track_from_ndjson(log_path, sprayer)
    raise IngestError(
        f"{Path(log_path).name}: unsupported log type {suffix!r} "
        "(expected a DataFlash .bin or a gateway .ndjson)")


def process_log(session: Session, settings: Settings, signer: Signer, log_path: Path,
                job: JobIn, boundary: Polygon) -> Processed:
    """Ingest a log, compute coverage and store a signed record."""
    log_path = Path(log_path)
    track = read_track(log_path, SprayerConfig.from_dict(job.sprayer))
    coverage = compute_coverage(track, boundary, job.swath_m, job.planned_rate_l_ha,
                                tank_litres_used=job.tank_litres_used)

    # keep the exact input next to the database, named by its hash
    settings.ensure_dirs()
    kept = settings.logs_dir / f"{track.log_sha256[:16]}{log_path.suffix.lower()}"
    if not kept.exists():
        shutil.copyfile(log_path, kept)

    record = create_record(session, signer, job, boundary, track, coverage)
    return Processed(record=record, track=track, coverage=coverage)
