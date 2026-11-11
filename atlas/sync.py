"""Laptop -> server sync, for when the internet returns.

Steps 1-4 happen offline on the field laptop. Each new record leaves a
`sync_record` row in the outbox; `push_pending` sends those to the Atlas
server (in Uzbekistan), which is where the bot and the verify page run.

The server stores a record only if its signature verifies against a key the
server already trusts - the key itself never travels with the record.
"""

from __future__ import annotations

import base64
import json

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db import (
    Farmer, Field, Job, Outbox, RecordVersion, Share, ShareEvent, SigningKey, utcnow,
)
from .records.canonical import canonical_json, sha256_hex
from .records.signing import key_id_for, verify_signature

MAX_SYNC_ATTEMPTS = 20


class SyncError(ValueError):
    """The server refuses a bundle."""


# --- laptop side -------------------------------------------------------------

def build_bundle(session: Session, record: RecordVersion) -> dict:
    job = session.get(Job, record.job_id)
    farmer = session.get(Farmer, job.farmer_id)
    fld = session.get(Field, job.field_id)
    from .shares import owner_share      # the QR already printed on the farmer's PDF
    return {
        "owner_share": {"token": owner_share(session, record).token},
        "record": {"id": record.id, "job_id": record.job_id, "version": record.version,
                   "payload": record.payload, "payload_sha256": record.payload_sha256,
                   "signature": record.signature, "key_id": record.key_id,
                   "prev_sha256": record.prev_sha256},
        "job": {"id": job.id, "sample_data": job.sample_data},
        "farmer": {"name": farmer.name, "phone": farmer.phone, "language": farmer.language},
        "field": {"name": fld.name, "boundary_geojson": fld.boundary_geojson,
                  "area_ha": fld.area_ha},
    }


def push_pending(Session: sessionmaker, settings: Settings,
                 client: httpx.Client | None = None) -> tuple[int, int]:
    """Send queued records to the server. Returns (sent, still_pending).

    With no connection nothing is lost: rows stay pending for the next run.
    """
    if not settings.server_url or not settings.sync_token:
        raise RuntimeError("set ATLAS_SERVER_URL and ATLAS_SYNC_TOKEN to sync")
    own_client = client is None
    client = client or httpx.Client(timeout=30.0)
    sent = 0
    try:
        with Session() as session:
            rows = session.scalars(select(Outbox).where(
                Outbox.kind == "sync_record", Outbox.status == "pending")
                .order_by(Outbox.id)).all()
            for row in rows:
                record = session.get(RecordVersion, row.record_id)
                row.attempts += 1
                try:
                    resp = client.post(
                        f"{settings.server_url}/api/sync/record",
                        content=json.dumps(build_bundle(session, record)),
                        headers={"Authorization": f"Bearer {settings.sync_token}",
                                 "Content-Type": "application/json"})
                except httpx.TransportError as exc:
                    row.last_error = f"network: {exc}"[:500]
                    session.commit()
                    break  # offline: try again later
                if resp.status_code in (200, 201):
                    row.status, row.sent_at, row.last_error = "sent", utcnow(), ""
                    sent += 1
                else:
                    row.last_error = f"HTTP {resp.status_code}: {resp.text}"[:500]
                    if resp.status_code in (400, 422) or row.attempts >= MAX_SYNC_ATTEMPTS:
                        row.status = "failed"  # the server will never accept this one
                session.commit()
            pending = len(session.scalars(select(Outbox).where(
                Outbox.kind == "sync_record", Outbox.status == "pending")).all())
    finally:
        if own_client:
            client.close()
    return sent, pending


# --- server side -------------------------------------------------------------

def receive_bundle(session: Session, bundle: dict) -> bool:
    """Store a synced record. Returns False if the server already had it."""
    try:
        rec, job_in = bundle["record"], bundle["job"]
        farmer_in, field_in = bundle["farmer"], bundle["field"]
        record_id, payload_text = rec["id"], rec["payload"]
    except (KeyError, TypeError) as exc:
        raise SyncError(f"malformed bundle: missing {exc}") from None

    if session.get(RecordVersion, record_id) is not None:
        return False

    # 1. the record must be intact and signed by a key this server trusts
    key = session.get(SigningKey, rec["key_id"])
    if key is None:
        raise SyncError(f"signing key {rec['key_id']} is not trusted by this server")
    try:
        payload = json.loads(payload_text)
    except ValueError:
        raise SyncError("record payload is not JSON") from None
    data = payload_text.encode("utf-8")
    if canonical_json(payload) != payload_text:
        raise SyncError("record payload is not canonical JSON")
    if sha256_hex(data) != rec["payload_sha256"]:
        raise SyncError("record hash does not match its content")
    if not verify_signature(key.public_key, data, rec["signature"]):
        raise SyncError("record signature does not verify")
    if (payload.get("record_id") != record_id or payload.get("job_id") != rec["job_id"]
            or payload.get("version") != rec["version"] or job_in["id"] != rec["job_id"]):
        raise SyncError("record identity does not match its content")

    # 2. farmer (by phone) and field (by farmer + name)
    farmer = session.scalar(select(Farmer).where(Farmer.phone == farmer_in["phone"]))
    if farmer is None:
        farmer = Farmer(name=farmer_in["name"], phone=farmer_in["phone"],
                        language=farmer_in.get("language", "uz"))
        session.add(farmer)
        session.flush()
    fld = session.scalar(select(Field).where(Field.farmer_id == farmer.id,
                                             Field.name == field_in["name"]))
    if fld is None:
        fld = Field(farmer_id=farmer.id, name=field_in["name"],
                    boundary_geojson=field_in["boundary_geojson"],
                    area_ha=field_in["area_ha"])
        session.add(fld)
        session.flush()

    # 3. job, record, and the Telegram delivery this server now owes the farmer
    if session.get(Job, job_in["id"]) is None:
        session.add(Job(id=job_in["id"], farmer_id=farmer.id, field_id=fld.id,
                        sample_data=bool(job_in.get("sample_data"))))
        session.flush()
    session.add(RecordVersion(
        id=record_id, job_id=rec["job_id"], version=rec["version"], payload=payload_text,
        payload_sha256=rec["payload_sha256"], signature=rec["signature"],
        key_id=rec["key_id"], prev_sha256=rec.get("prev_sha256")))
    session.flush()
    # 4. the farmer's own link: keep the token the laptop already printed as a QR,
    #    so a PDF made in the field verifies on this server
    token = (bundle.get("owner_share") or {}).get("token")
    if (isinstance(token, str) and 24 <= len(token) <= 64
            and session.scalar(select(Share).where(Share.token == token)) is None):
        share = Share(token=token, record_id=record_id, farmer_id=farmer.id,
                      recipient_type="owner")
        session.add(share)
        session.flush()
        session.add(ShareEvent(share_id=share.id, kind="created",
                               detail="by atlas for owner (synced from the laptop)"))
    session.add(Outbox(kind="telegram_report", record_id=record_id, farmer_id=farmer.id))
    session.flush()
    return True


def trust_key(session: Session, key_id: str, public_key: str, label: str = "") -> None:
    """Server operator: trust a laptop's public signing key."""
    if key_id_for(base64.b64decode(public_key)) != key_id:
        raise SyncError("key id does not match the public key")
    if session.get(SigningKey, key_id) is None:
        session.add(SigningKey(key_id=key_id, public_key=public_key, label=label))
        session.flush()
