"""Step 6 - SHARE: expiring, revocable, logged."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from atlas.db import ShareEvent, utcnow
from atlas.shares import (
    ACTIVE, EXPIRED, OWNER, REVOKED, active_shares, create_share, find_share, log_event,
    open_count, owner_share, report_pdf, revoke_share, share_state, share_url,
)


def test_share_has_an_unguessable_token_and_an_expiry(session, processed):
    a = create_share(session, processed.record, "bank", ttl_days=30)
    b = create_share(session, processed.record, "bank", ttl_days=30)
    assert a.token != b.token and len(a.token) >= 32
    assert timedelta(days=29, hours=23) < a.expires_at - utcnow() <= timedelta(days=30)
    assert share_state(a) == ACTIVE
    assert find_share(session, a.token).id == a.id
    assert find_share(session, "nope") is None


def test_share_expires(session, processed):
    share = create_share(session, processed.record, "insurer", ttl_days=7)
    assert share_state(share, utcnow() + timedelta(days=6)) == ACTIVE
    assert share_state(share, utcnow() + timedelta(days=8)) == EXPIRED


def test_revoke_is_immediate_logged_and_idempotent(session, processed):
    share = create_share(session, processed.record, "buyer", ttl_days=30)
    assert revoke_share(session, share) is True
    assert share_state(share) == REVOKED
    assert revoke_share(session, share) is False
    kinds = [e.kind for e in session.scalars(
        select(ShareEvent).where(ShareEvent.share_id == share.id).order_by(ShareEvent.id))]
    assert kinds == ["created", "revoked"]


def test_revoked_wins_over_expired(session, processed):
    share = create_share(session, processed.record, "cluster", ttl_days=1)
    revoke_share(session, share)
    assert share_state(share, utcnow() + timedelta(days=5)) == REVOKED


def test_only_known_recipients_and_positive_lifetimes(session, processed):
    with pytest.raises(ValueError):
        create_share(session, processed.record, "journalist", ttl_days=30)
    with pytest.raises(ValueError):
        create_share(session, processed.record, "bank", ttl_days=0)


def test_owner_link_is_created_once_and_replaced_after_revocation(session, processed):
    first = owner_share(session, processed.record)
    assert first.recipient_type == OWNER and first.expires_at is None
    assert owner_share(session, processed.record).id == first.id
    revoke_share(session, first)
    second = owner_share(session, processed.record)
    assert second.id != first.id and second.token != first.token


def test_active_shares_lists_only_live_ones(session, processed):
    farmer_id = processed.record.job.farmer_id
    keep = create_share(session, processed.record, "bank", ttl_days=30)
    gone = create_share(session, processed.record, "insurer", ttl_days=30)
    revoke_share(session, gone)
    old = create_share(session, processed.record, "buyer", ttl_days=30)
    old.expires_at = utcnow() - timedelta(minutes=1)
    session.flush()
    assert [s.id for s in active_shares(session, farmer_id)] == [keep.id]


def test_open_count_counts_openings(session, processed):
    share = create_share(session, processed.record, "bank", ttl_days=30)
    assert open_count(session, share) == 0
    log_event(session, share, "opened", "verified")
    log_event(session, share, "opened", "verified")
    assert open_count(session, share) == 2


def test_each_share_gets_its_own_pdf_and_qr(session, settings, processed):
    bank = create_share(session, processed.record, "bank", ttl_days=30)
    insurer = create_share(session, processed.record, "insurer", ttl_days=30)
    assert share_url(settings, bank) == f"http://testserver/v/{bank.token}"
    bank_text = PdfReader(io.BytesIO(
        report_pdf(settings, processed.record, bank, "en"))).pages[0].extract_text()
    assert share_url(settings, bank) in bank_text
    assert share_url(settings, insurer) not in bank_text
    assert "Shared with: Bank" in bank_text
