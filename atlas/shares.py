"""Step 6 - SHARE: explicit, expiring, revocable links to one record.

Nothing leaves a farmer's record without one of these. Every creation,
opening and revocation is written to the share_event log.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .db import Job, RecordVersion, Share, ShareEvent, utcnow
from .report import render_pdf

OWNER = "owner"
RECIPIENTS = ("bank", "insurer", "buyer", "cluster")

ACTIVE = "active"
REVOKED = "revoked"
EXPIRED = "expired"


def share_state(share: Share, now: datetime | None = None) -> str:
    now = now or utcnow()
    if share.revoked_at is not None:
        return REVOKED
    if share.expires_at is not None and share.expires_at <= now:
        return EXPIRED
    return ACTIVE


def log_event(session: Session, share: Share, kind: str, detail: str = "") -> None:
    session.add(ShareEvent(share_id=share.id, kind=kind, detail=detail[:300]))
    session.flush()


def record_farmer_id(session: Session, record: RecordVersion) -> str:
    return session.get(Job, record.job_id).farmer_id


def create_share(session: Session, record: RecordVersion, recipient_type: str,
                 ttl_days: int | None, actor: str = "farmer") -> Share:
    """Create a share link. `ttl_days=None` means it does not expire."""
    if recipient_type != OWNER and recipient_type not in RECIPIENTS:
        raise ValueError(f"recipient must be one of {RECIPIENTS}")
    if ttl_days is not None and ttl_days <= 0:
        raise ValueError("share lifetime must be positive")
    share = Share(
        token=secrets.token_urlsafe(24), record_id=record.id,
        farmer_id=record_farmer_id(session, record), recipient_type=recipient_type,
        expires_at=None if ttl_days is None else utcnow() + timedelta(days=ttl_days))
    session.add(share)
    session.flush()
    log_event(session, share, "created", f"by {actor} for {recipient_type}")
    return share


def owner_share(session: Session, record: RecordVersion) -> Share:
    """The farmer's own link (the QR on their PDF). Created on first use."""
    for share in session.scalars(select(Share).where(
            Share.record_id == record.id, Share.recipient_type == OWNER)
            .order_by(Share.created_at.desc())):
        if share_state(share) == ACTIVE:
            return share
    return create_share(session, record, OWNER, ttl_days=None, actor="atlas")


def revoke_share(session: Session, share: Share, actor: str = "farmer") -> bool:
    """Revoke a share. Returns False if it was already revoked."""
    if share.revoked_at is not None:
        return False
    share.revoked_at = utcnow()
    session.flush()
    log_event(session, share, "revoked", f"by {actor}")
    return True


def active_shares(session: Session, farmer_id: str) -> list[Share]:
    shares = session.scalars(select(Share).where(Share.farmer_id == farmer_id)
                             .order_by(Share.created_at)).all()
    return [s for s in shares if share_state(s) == ACTIVE]


def open_count(session: Session, share: Share) -> int:
    return session.scalar(select(func.count()).select_from(ShareEvent).where(
        ShareEvent.share_id == share.id, ShareEvent.kind == "opened")) or 0


def find_share(session: Session, token: str) -> Share | None:
    return session.scalar(select(Share).where(Share.token == token))


def share_url(settings: Settings, share: Share) -> str:
    return f"{settings.base_url}/v/{share.token}"


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def report_pdf(settings: Settings, record: RecordVersion, share: Share, lang: str) -> bytes:
    """The PDF for one share: its QR code is that share's link."""
    return render_pdf(
        json.loads(record.payload), record.payload_sha256, share_url(settings, share), lang,
        share={"recipient_type": share.recipient_type, "expires_utc": _iso(share.expires_at)})


def report_filename(record: RecordVersion, lang: str) -> str:
    return f"nomad-atlas-{record.id[:8]}-v{record.version}-{lang}.pdf"
