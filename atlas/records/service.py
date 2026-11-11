"""Step 3 - RECORD: build, sign, store, version and verify spray records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from shapely.geometry import Polygon
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..compute.coverage import CoverageResult, to_geojson
from ..db import (
    Farmer, Field, Job, Outbox, RecordVersion, SigningKey, new_id, utcnow,
)
from ..ingest.track import Track
from .canonical import canonical_json, sha256_hex
from .schemas import RECORD_SCHEMA, JobIn
from .signing import ALGORITHM, Signer, verify_signature

VERIFIED = "verified"
MODIFIED = "modified"

# Job details that an amendment (new version) may correct. Results and the
# input log are computed, not typed, so they are never amended by hand.
AMENDABLE = ("crop", "product", "batch", "pilot", "drone_id")


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_key_registered(session: Session, signer: Signer, label: str = "") -> None:
    """Trust this installation's own signing key."""
    if session.get(SigningKey, signer.key_id) is None:
        session.add(SigningKey(key_id=signer.key_id, public_key=signer.public_b64,
                               label=label or "local signing key"))
        session.flush()


def _get_or_create_farmer(session: Session, job: JobIn) -> Farmer:
    farmer = session.scalar(select(Farmer).where(Farmer.phone == job.farmer.phone))
    if farmer is None:
        farmer = Farmer(name=job.farmer.name, phone=job.farmer.phone,
                        language=job.farmer.language)
        session.add(farmer)
        session.flush()
    return farmer


def _get_or_create_field(session: Session, farmer: Farmer, name: str,
                         boundary: Polygon, area_ha: float) -> Field:
    fld = session.scalar(select(Field).where(Field.farmer_id == farmer.id,
                                             Field.name == name))
    geojson = canonical_json(to_geojson(boundary))
    if fld is None:
        fld = Field(farmer_id=farmer.id, name=name, boundary_geojson=geojson,
                    area_ha=round(area_ha, 4))
        session.add(fld)
        session.flush()
    return fld


def _sign_and_store(session: Session, signer: Signer, job: Job, payload: dict,
                    prev_sha256: str | None) -> RecordVersion:
    text = canonical_json(payload)
    data = text.encode("utf-8")
    record = RecordVersion(
        id=payload["record_id"], job_id=job.id, version=payload["version"],
        payload=text, payload_sha256=sha256_hex(data), signature=signer.sign(data),
        key_id=signer.key_id, prev_sha256=prev_sha256)
    session.add(record)
    session.flush()
    # work for when the internet is back: deliver to the farmer, sync to server
    session.add(Outbox(kind="telegram_report", record_id=record.id, farmer_id=job.farmer_id))
    session.add(Outbox(kind="sync_record", record_id=record.id, farmer_id=job.farmer_id))
    session.flush()
    return record


def build_payload(*, record_id: str, job_id: str, farmer_id: str, farmer_name: str,
                  field_id: str, field_name: str, job_in: JobIn, track: Track,
                  coverage: CoverageResult, key_id: str) -> dict:
    """The record content for version 1 of a job (what gets signed)."""
    geo = coverage.geojson()
    return {
        "schema": RECORD_SCHEMA,
        "record_id": record_id,
        "job_id": job_id,
        "version": 1,
        "supersedes_sha256": None,
        "amendment_reason": None,
        "created_utc": _iso(utcnow()),
        "sample_data": job_in.sample_data,
        "farmer": {"id": farmer_id, "name": farmer_name},
        "field": {"id": field_id, "name": field_name,
                  "area_ha": round(coverage.field_area_ha, 4),
                  "boundary": geo["field"]},
        "job": {
            "crop": job_in.crop,
            "product": job_in.product,
            "batch": job_in.batch,
            "planned_rate_l_ha": round(job_in.planned_rate_l_ha, 2),
            "swath_m": round(job_in.swath_m, 2),
            "pilot": job_in.pilot,
            "drone_id": job_in.drone_id,
            "started_utc": _iso(track.started_utc),
            "ended_utc": _iso(track.ended_utc),
        },
        "weather": job_in.weather.model_dump(),
        "input": {
            "kind": track.source,
            "log_name": track.log_name,
            "log_sha256": track.log_sha256,
            "sprayer_source": track.sprayer.describe(),
            "fixes": len(track.samples),
            "rejected_fixes": track.rejected_fixes,
            "min_battery_v": (None if track.min_battery_v is None
                              else round(track.min_battery_v, 2)),
        },
        "results": coverage.numbers(),
        "geometry": {"sprayed": geo["sprayed"], "missed": geo["missed"],
                     "overlap": geo["overlap"]},
        "signer": {"key_id": key_id, "algorithm": ALGORITHM},
    }


def create_record(session: Session, signer: Signer, job_in: JobIn, boundary: Polygon,
                  track: Track, coverage: CoverageResult) -> RecordVersion:
    """Create version 1 of a job's signed record."""
    ensure_key_registered(session, signer)
    farmer = _get_or_create_farmer(session, job_in)
    fld = _get_or_create_field(session, farmer, job_in.field_name, boundary,
                               coverage.field_area_ha)
    job = Job(farmer_id=farmer.id, field_id=fld.id, sample_data=job_in.sample_data)
    session.add(job)
    session.flush()
    payload = build_payload(
        record_id=new_id(), job_id=job.id, farmer_id=farmer.id, farmer_name=farmer.name,
        field_id=fld.id, field_name=fld.name, job_in=job_in, track=track,
        coverage=coverage, key_id=signer.key_id)
    return _sign_and_store(session, signer, job, payload, prev_sha256=None)


def latest_version(session: Session, job_id: str) -> RecordVersion:
    return session.scalars(
        select(RecordVersion).where(RecordVersion.job_id == job_id)
        .order_by(RecordVersion.version.desc())).first()


def amend_record(session: Session, signer: Signer, record: RecordVersion,
                 changes: dict, reason: str) -> RecordVersion:
    """Correct job details by creating a NEW signed version.

    The previous version is left untouched and stays verifiable; the new one
    carries the hash of the version it supersedes.
    """
    if not reason.strip():
        raise ValueError("an amendment needs a reason")
    bad = sorted(set(changes) - set(AMENDABLE))
    if bad:
        raise ValueError(f"cannot amend {bad}; amendable fields are {list(AMENDABLE)}")
    current = latest_version(session, record.job_id)
    if current.id != record.id:
        raise ValueError("only the latest version of a record can be amended")

    ensure_key_registered(session, signer)
    payload = json.loads(record.payload)
    payload["job"].update(changes)
    payload.update(
        record_id=new_id(), version=record.version + 1,
        supersedes_sha256=record.payload_sha256, amendment_reason=reason.strip(),
        created_utc=_iso(utcnow()),
        signer={"key_id": signer.key_id, "algorithm": ALGORITHM})
    return _sign_and_store(session, signer, record.job, payload,
                           prev_sha256=record.payload_sha256)


@dataclass
class Verification:
    status: str                       # VERIFIED | MODIFIED
    problems: list[str] = field(default_factory=list)
    payload: dict | None = None       # parsed record, None if unreadable
    recomputed_sha256: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED


def verify_record(session: Session, record: RecordVersion) -> Verification:
    """Recompute the hash from the stored record and check the signature."""
    problems: list[str] = []
    payload = None
    recomputed = None

    try:
        payload = json.loads(record.payload)
        if not isinstance(payload, dict):
            raise ValueError("record is not a JSON object")
    except ValueError:
        payload = None
        problems.append("record content is not readable JSON")

    if payload is not None:
        canonical = canonical_json(payload)
        data = canonical.encode("utf-8")
        recomputed = sha256_hex(data)
        if canonical != record.payload:
            problems.append("record content is not in its original signed form")
        if recomputed != record.payload_sha256:
            problems.append("record hash does not match its content")
        if payload.get("record_id") != record.id or payload.get("version") != record.version:
            problems.append("record identity does not match its content")

        key = session.get(SigningKey, record.key_id)
        if key is None:
            problems.append(f"signing key {record.key_id} is not trusted here")
        elif not verify_signature(key.public_key, data, record.signature):
            problems.append("signature does not match the record content")
        if (payload.get("signer") or {}).get("key_id") != record.key_id:
            problems.append("signing key does not match the record content")

    return Verification(status=MODIFIED if problems else VERIFIED, problems=problems,
                        payload=payload, recomputed_sha256=recomputed)
