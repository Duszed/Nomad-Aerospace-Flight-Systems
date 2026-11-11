"""The common output of both ingest paths."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .sprayer import SprayerConfig


class IngestError(ValueError):
    """The input cannot be turned into a trustworthy track."""


@dataclass(frozen=True)
class Sample:
    t: float                 # seconds on the aircraft clock (monotonic)
    lat: float
    lon: float
    height_m: float | None   # height above home / take-off point
    speed_ms: float          # ground speed
    spray_on: bool
    flow_lpm: float | None = None


@dataclass
class Track:
    samples: list[Sample]
    source: str                      # "dataflash" | "ndjson"
    sprayer: SprayerConfig
    log_name: str
    log_sha256: str
    has_flow: bool = False
    started_utc: datetime | None = None
    ended_utc: datetime | None = None
    rejected_fixes: int = 0
    min_battery_v: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-1].t - self.samples[0].t


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
