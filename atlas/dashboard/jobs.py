"""Jobs as the dashboard sees them: drafts, signing, lists, totals, delivery status."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..compute.coverage import compute_coverage
from ..db import Confirmation, Draft, Job, Outbox, RecordVersion, Share, utcnow
from ..i18n import TASHKENT
from ..ingest.sprayer import SprayerConfig
from ..pipeline import process_log, read_track
from ..records import JobIn, build_payload, latest_version, verify_record
from ..shares import OWNER, open_count, owner_share, share_state
from .geo import boundary_of

# delivery states shown in the dashboard
DRAFT, QUEUED, SENT, CONFIRMED, FAILED = "draft", "queued", "sent", "confirmed", "failed"
SYNC_WAIT, SYNCED = "sync_wait", "synced"
STATES = (QUEUED, SENT, CONFIRMED, FAILED, SYNC_WAIT, SYNCED)


# --- drafts -------------------------------------------------------------------

def draft_details(draft: Draft) -> dict:
    try:
        return json.loads(draft.details or "{}")
    except ValueError:
        return {}


def job_in_for(draft: Draft, sprayer: dict) -> JobIn:
    """The validated job input for a draft (raises pydantic ValidationError)."""
    d = draft_details(draft)
    return JobIn.model_validate({
        "farmer": {"name": draft.farmer.name, "phone": draft.farmer.phone,
                   "language": draft.farmer.language},
        "field_name": draft.field.name,
        "crop": d.get("crop", ""),
        "product": d.get("product", ""),
        "batch": d.get("batch", ""),
        "planned_rate_l_ha": d.get("planned_rate_l_ha"),
        "swath_m": d.get("swath_m"),
        "pilot": d.get("pilot", ""),
        "drone_id": d.get("drone_id", ""),
        "weather": d.get("weather") or {},
        "tank_litres_used": d.get("tank_litres_used"),
        "sprayer": sprayer,
        "sample_data": draft.sample_data,
    })


def upload_dir(settings, draft_id: str) -> Path:
    return settings.data_dir / "uploads" / draft_id


def compute_preview(rt, draft: Draft) -> dict:
    """Read the draft's log and compute its results. Nothing is signed."""
    job = job_in_for(draft, rt.store["sprayer"])
    track = read_track(Path(draft.log_path), SprayerConfig.from_dict(job.sprayer))
    coverage = compute_coverage(track, boundary_of(draft.field), job.swath_m,
                                job.planned_rate_l_ha, tank_litres_used=job.tank_litres_used)
    payload = build_payload(
        record_id=draft.id, job_id=draft.id, farmer_id=draft.farmer_id,
        farmer_name=draft.farmer.name, field_id=draft.field_id, field_name=draft.field.name,
        job_in=job, track=track, coverage=coverage, key_id=rt.signer.key_id)
    draft.preview = json.dumps(payload, ensure_ascii=False)
    return payload


def sign_draft(rt, session: Session, draft: Draft) -> RecordVersion:
    """Turn a draft into a signed record and queue it for the farmer."""
    job = job_in_for(draft, rt.store["sprayer"])
    done = process_log(session, rt.settings, rt.signer, Path(draft.log_path), job,
                       boundary_of(draft.field))
    owner_share(session, done.record)        # the QR on the farmer's own PDF
    discard_draft(rt, session, draft)
    return done.record


def discard_draft(rt, session: Session, draft: Draft) -> None:
    shutil.rmtree(upload_dir(rt.settings, draft.id), ignore_errors=True)
    session.delete(draft)
    session.flush()


# --- signed jobs ----------------------------------------------------------------

def _when(payload: dict) -> datetime:
    iso = payload["job"].get("started_utc") or payload["created_utc"]
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def delivery(rt, session: Session, record: RecordVersion, farmer) -> dict:
    """Where the farmer's report is: queued, sent, confirmed ..."""
    confirmation = session.get(Confirmation, record.id)
    if confirmation is not None:
        return {"state": CONFIRMED, "at": confirmation.confirmed_at, "why": ""}

    def row(kind: str) -> Outbox | None:
        return session.scalars(select(Outbox).where(
            Outbox.kind == kind, Outbox.record_id == record.id).order_by(Outbox.id.desc())).first()

    if rt.server_mode:
        sync = row("sync_record")
        if sync is not None and sync.status == "sent":
            return {"state": SYNCED, "at": sync.sent_at, "why": ""}
        if sync is not None and sync.status == "failed":
            return {"state": FAILED, "at": None, "why": sync.last_error}
        return {"state": SYNC_WAIT, "at": None, "why": ""}

    tele = row("telegram_report")
    if tele is not None and tele.status == "sent":
        return {"state": SENT, "at": tele.sent_at, "why": ""}
    if tele is not None and tele.status == "failed":
        return {"state": FAILED, "at": None, "why": tele.last_error}
    if farmer.telegram_chat_id is None:
        why = "unreg"
    elif not rt.settings.bot_token and not rt.demo:
        why = "nobot"
    else:
        why = "offline"
    return {"state": QUEUED, "at": None, "why": why}


def job_view(rt, session: Session, job: Job) -> dict:
    record = latest_version(session, job.id)
    try:
        payload = json.loads(record.payload)
        res, details = payload["results"], payload["job"]
        when = _when(payload)
    except (ValueError, KeyError, TypeError):      # a tampered record still lists
        payload, res, details, when = None, {}, {}, record.created_at
    return {
        "job": job, "record": record, "payload": payload, "res": res, "details": details,
        "when": when, "local": when.astimezone(TASHKENT),
        "farmer": job.farmer, "field": job.field,
        "delivery": delivery(rt, session, record, job.farmer),
        "sample": job.sample_data,
    }


def list_jobs(rt, session: Session) -> list[dict]:
    jobs = session.scalars(select(Job)).all()
    views = [job_view(rt, session, job) for job in jobs if job.versions]
    return sorted(views, key=lambda v: v["when"], reverse=True)


def filter_jobs(views: list[dict], q: str = "", state: str = "", crop: str = "",
                date_from: str = "", date_to: str = "") -> list[dict]:
    q = (q or "").strip().lower()
    out = []
    for v in views:
        if q:
            hay = " ".join(str(x) for x in (
                v["farmer"].name, v["field"].name, v["details"].get("crop"),
                v["details"].get("product"), v["details"].get("batch"),
                v["details"].get("pilot"), v["details"].get("drone_id"))).lower()
            if q not in hay:
                continue
        if state and v["delivery"]["state"] != state:
            continue
        if crop and (v["details"].get("crop") or "") != crop:
            continue
        day = v["local"].strftime("%Y-%m-%d")
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        out.append(v)
    return out


def totals(views: list[dict], now: datetime | None = None) -> dict:
    """Hectares sprayed this week (Monday start) and this season (calendar year)."""
    local_now = (now or utcnow()).astimezone(TASHKENT)
    week_start = (local_now - timedelta(days=local_now.weekday())).date()
    out = {"week_ha": 0.0, "week_jobs": 0, "season_ha": 0.0, "season_jobs": 0, "today": []}
    for v in views:
        ha = float(v["res"].get("covered_in_field_ha") or 0.0)
        day = v["local"].date()
        if day.year == local_now.year:
            out["season_ha"] += ha
            out["season_jobs"] += 1
        if week_start <= day <= local_now.date():
            out["week_ha"] += ha
            out["week_jobs"] += 1
        if day == local_now.date():
            out["today"].append(v)
    return out


def job_detail(rt, session: Session, job: Job) -> dict:
    """Everything the job page shows."""
    view = job_view(rt, session, job)
    record = view["record"]
    view["check"] = verify_record(session, record)
    record_ids = [r.id for r in job.versions]
    shares = session.scalars(select(Share).where(Share.record_id.in_(record_ids))
                             .order_by(Share.created_at)).all()
    rows = []
    for share in shares:
        rows.append({"share": share, "state": share_state(share),
                     "opened": open_count(session, share),
                     "version": next(r.version for r in job.versions
                                     if r.id == share.record_id)})
    view["shares"] = [r for r in rows if r["share"].recipient_type != OWNER]
    view["owner_links"] = [r for r in rows if r["share"].recipient_type == OWNER
                           and r["share"].record_id == record.id and r["state"] == "active"]
    view["versions"] = list(job.versions)
    return view
