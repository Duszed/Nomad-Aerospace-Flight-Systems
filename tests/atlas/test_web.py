"""Step 7 - VERIFY: Verified / Modified / Revoked, and laptop -> server sync."""

from __future__ import annotations

import base64
import io
import json
import re
import sqlite3
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select

from atlas.config import Settings
from atlas.db import Confirmation, Outbox, RecordVersion, ShareEvent, init_db, utcnow
from atlas.records import amend_record, verify_record
from atlas.shares import create_share, owner_share, revoke_share
from atlas.sync import build_bundle, push_pending, trust_key
from atlas.web import create_app


@pytest.fixture
def client(settings, Session):
    return TestClient(create_app(settings, Session))


@pytest.fixture
def owner(session, processed):
    share = owner_share(session, processed.record)
    session.commit()
    return share


@pytest.fixture
def bank(session, processed):
    share = create_share(session, processed.record, "bank", ttl_days=30)
    session.commit()
    return share


def status_of(html: str) -> str:
    return re.search(r'data-status="(\w+)"', html).group(1)


def tamper_one_byte(settings, record):
    """Change a single character of the stored record, behind Atlas's back."""
    i = record.payload.index('"coverage_pct":') + len('"coverage_pct":')
    tampered = record.payload[:i] + ("8" if record.payload[i] != "8" else "7") + record.payload[i + 1:]
    con = sqlite3.connect(settings.data_dir / "atlas.db")
    con.execute("UPDATE record_version SET payload=? WHERE id=?", (tampered, record.id))
    con.commit()
    con.close()


# --- Verified ----------------------------------------------------------------

def test_qr_link_shows_verified_with_record_and_map(client, owner, processed):
    r = client.get(f"/v/{owner.token}?lang=en")
    assert r.status_code == 200 and status_of(r.text) == "verified"
    assert "<h1>Verified</h1>" in r.text
    for value in ("Namuna Fermer (sample)", "Sample field 4.2 ha", "Cotton",
                  "Sample product 25 EC", "SAMPLE-0001", "K30-SIM-001", "97.2%",
                  processed.record.payload_sha256):
        assert value in r.text
    assert f'/v/{owner.token}/map.png' in r.text
    assert "Sample data – not a customer record" in r.text
    assert "estimated" in r.text


def test_verify_page_in_three_languages(client, owner):
    assert "<h1>Tasdiqlandi</h1>" in client.get(f"/v/{owner.token}").text       # default uz
    assert "<h1>Подтверждено</h1>" in client.get(f"/v/{owner.token}?lang=ru").text
    uz = client.get(f"/v/{owner.token}?lang=uz").text
    assert "Asosiy koʻrsatkichlar" in uz and "97,2%" in uz


def test_map_pdf_and_signed_json_downloads(client, owner, settings, processed):
    png = client.get(f"/v/{owner.token}/map.png")
    assert png.headers["content-type"] == "image/png" and png.content.startswith(b"\x89PNG")
    pdf = client.get(f"/v/{owner.token}/report.pdf?lang=en")
    assert pdf.headers["content-type"] == "application/pdf"
    text = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text()
    assert f"http://testserver/v/{owner.token}" in text      # QR points back at this link


def test_signed_json_can_be_verified_independently(client, owner):
    """A third party can check the signature with nothing but this download."""
    body = client.get(f"/v/{owner.token}/record.json").json()
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(body["public_key"]))
    key.verify(base64.b64decode(body["signature"]), body["payload"].encode("utf-8"))
    assert json.loads(body["payload"])["results"]["coverage_pct"] == 97.2
    keys = client.get("/.well-known/atlas-keys.json").json()["keys"]
    assert [k["key_id"] for k in keys] == [body["key_id"]]


def test_api_reports_verified(client, owner, processed):
    body = client.get(f"/api/verify/{owner.token}").json()
    assert body["status"] == "verified" and body["problems"] == []
    assert body["sha256"] == body["recomputed_sha256"] == processed.record.payload_sha256


def test_every_opening_is_logged(client, owner, session):
    client.get(f"/v/{owner.token}")
    client.get(f"/api/verify/{owner.token}")
    client.get(f"/v/{owner.token}/map.png")          # part of the page: not a new opening
    events = [(e.kind, e.detail) for e in session.scalars(
        select(ShareEvent).where(ShareEvent.share_id == owner.id).order_by(ShareEvent.id))]
    assert events == [("created", "by atlas for owner"), ("opened", "verified"),
                      ("opened", "verified")]


def test_third_party_link_names_the_recipient(client, bank):
    html = client.get(f"/v/{bank.token}?lang=en").text
    assert status_of(html) == "verified"
    assert "Shared with: <strong>Bank</strong>" in html and "valid until" in html


def test_farmer_confirmation_is_shown(client, owner, session, processed):
    session.add(Confirmation(record_id=processed.record.id,
                             farmer_id=processed.record.job.farmer_id))
    session.commit()
    assert "Confirmed by the farmer on" in client.get(f"/v/{owner.token}?lang=en").text


def test_old_version_links_say_a_newer_version_exists(client, owner, session, signer, processed):
    v2 = amend_record(session, signer, processed.record, {"batch": "SAMPLE-0002"},
                      reason="Batch number mistyped")
    new_link = owner_share(session, v2)
    session.commit()
    old = client.get(f"/v/{owner.token}?lang=en").text
    assert status_of(old) == "verified"                     # v1 is still intact
    assert "A newer version of this record exists (version 2)" in old
    new = client.get(f"/v/{new_link.token}?lang=en").text
    assert "SAMPLE-0002" in new and "Batch number mistyped" in new


# --- Modified ----------------------------------------------------------------

def test_changing_one_byte_shows_modified(client, owner, settings, processed):
    assert status_of(client.get(f"/v/{owner.token}").text) == "verified"
    tamper_one_byte(settings, processed.record)

    r = client.get(f"/v/{owner.token}?lang=en")
    assert r.status_code == 200 and status_of(r.text) == "modified"
    assert "<h1>Modified</h1>" in r.text
    assert "record hash does not match its content" in r.text
    # the altered content is not presented as a record
    assert "Sample product 25 EC" not in r.text and "map.png" not in r.text

    body = client.get(f"/api/verify/{owner.token}").json()
    assert body["status"] == "modified" and body["sha256"] != body["recomputed_sha256"]
    for path in ("map.png", "report.pdf", "record.json"):
        assert client.get(f"/v/{owner.token}/{path}").status_code == 409


# --- Revoked / Expired / unknown ---------------------------------------------

def test_revoking_a_share_shows_revoked_and_hides_the_record(client, bank, owner, session):
    assert status_of(client.get(f"/v/{bank.token}").text) == "verified"
    revoke_share(session, bank)
    session.commit()

    r = client.get(f"/v/{bank.token}?lang=en")
    assert r.status_code == 410 and status_of(r.text) == "revoked"
    assert "<h1>Revoked</h1>" in r.text
    for secret in ("Namuna Fermer", "Sample field", "Cotton", "97"):
        assert secret not in r.text
    assert client.get(f"/api/verify/{bank.token}").json() == {
        "status": "revoked", "checked_utc": client.get(f"/api/verify/{bank.token}").json()["checked_utc"]}
    for path in ("map.png", "report.pdf", "record.json"):
        assert client.get(f"/v/{bank.token}/{path}").status_code == 410
    kinds = [e.kind for e in session.scalars(
        select(ShareEvent).where(ShareEvent.share_id == bank.id).order_by(ShareEvent.id))]
    assert kinds[:3] == ["created", "opened", "revoked"] and "refused" in kinds

    # revoking one share leaves the others alone
    assert status_of(client.get(f"/v/{owner.token}").text) == "verified"


def test_expired_share_shows_expired(client, bank, session):
    bank.expires_at = utcnow() - timedelta(seconds=1)
    session.commit()
    r = client.get(f"/v/{bank.token}?lang=en")
    assert r.status_code == 410 and status_of(r.text) == "expired"
    assert "Namuna Fermer" not in r.text


def test_unknown_token_is_not_found(client, owner):
    r = client.get("/v/this-token-does-not-exist?lang=en")
    assert r.status_code == 404 and status_of(r.text) == "not_found"
    assert client.get("/api/verify/nope").status_code == 404


# --- privacy -----------------------------------------------------------------

def test_no_way_to_list_or_browse_records(client, owner, processed):
    for path in ("/v/", "/v", "/api/records", "/records", f"/v/{processed.record.id}",
                 f"/api/verify/{processed.record.id}", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    home = client.get("/")
    assert home.status_code == 200 and "Namuna" not in home.text


def test_pages_load_nothing_from_other_sites(client, owner):
    r = client.get(f"/v/{owner.token}")
    assert not re.search(r'(src|href)="(https?:)?//', r.text)
    assert "<script" not in r.text
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert "noindex" in r.headers["x-robots-tag"]


def test_page_escapes_record_text(client, session, settings, signer, demo_boundary,
                                  faulty_ndjson):
    from atlas.pipeline import process_log
    from atlas.records import JobIn
    from conftest import DEMO_JOB
    job = JobIn.model_validate({**DEMO_JOB, "product": "<script>alert(1)</script>"})
    p = process_log(session, settings, signer, faulty_ndjson, job, demo_boundary)
    share = owner_share(session, p.record)
    session.commit()
    html = client.get(f"/v/{share.token}").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# --- sync: laptop -> server --------------------------------------------------

@pytest.fixture
def server(tmp_path):
    """A second, empty Atlas installation: the server in Uzbekistan."""
    cfg = Settings(data_dir=tmp_path / "server", base_url="http://testserver",
                   sync_token="sample-sync-token")
    cfg.ensure_dirs()
    ServerSession = init_db(cfg.database_url)
    return cfg, ServerSession, TestClient(create_app(cfg, ServerSession))


def laptop_settings(settings):
    return replace(settings, server_url="http://testserver", sync_token="sample-sync-token")


def test_sync_is_refused_until_the_server_trusts_the_laptop_key(settings, Session, session,
                                                               processed, server):
    _, ServerSession, http = server
    sent, pending = push_pending(Session, laptop_settings(settings), client=http)
    assert (sent, pending) == (0, 0)
    session.expire_all()
    row = session.scalar(select(Outbox).where(Outbox.kind == "sync_record"))
    assert row.status == "failed" and "not trusted" in row.last_error
    with ServerSession() as s:
        assert s.scalars(select(RecordVersion)).all() == []


def test_sync_pushes_the_signed_record_and_queues_delivery(settings, Session, session, signer,
                                                          processed, server):
    _, ServerSession, http = server
    with ServerSession() as s:
        trust_key(s, signer.key_id, signer.public_b64, "field laptop 1")
        s.commit()

    sent, pending = push_pending(Session, laptop_settings(settings), client=http)
    assert (sent, pending) == (1, 0)
    with ServerSession() as s:
        record = s.get(RecordVersion, processed.record.id)
        assert record.payload == processed.record.payload
        assert verify_record(s, record).ok
        queued = s.scalars(select(Outbox)).all()
        assert [(q.kind, q.status) for q in queued] == [("telegram_report", "pending")]

    # sending the same record again changes nothing
    again = http.post("/api/sync/record", content=json.dumps(build_bundle(session, processed.record)),
                      headers={"Authorization": "Bearer sample-sync-token"})
    assert again.status_code == 200 and again.json() == {"ok": True, "created": False}


def test_sync_rejects_bad_token_and_tampered_bundles(session, signer, processed, server,
                                                     settings, Session):
    _, ServerSession, http = server
    with ServerSession() as s:
        trust_key(s, signer.key_id, signer.public_b64)
        s.commit()
    bundle = build_bundle(session, processed.record)
    body = json.dumps(bundle)
    assert http.post("/api/sync/record", content=body).status_code == 401
    assert http.post("/api/sync/record", content=body,
                     headers={"Authorization": "Bearer wrong"}).status_code == 401

    bundle["record"]["payload"] = bundle["record"]["payload"].replace("Cotton", "Wheat")
    r = http.post("/api/sync/record", content=json.dumps(bundle),
                  headers={"Authorization": "Bearer sample-sync-token"})
    assert r.status_code == 422 and "hash" in r.json()["detail"]
    with ServerSession() as s:
        assert s.scalars(select(RecordVersion)).all() == []

    # a server with no sync token configured accepts nothing
    closed = TestClient(create_app(settings, Session))
    assert closed.post("/api/sync/record", content=body,
                       headers={"Authorization": "Bearer sample-sync-token"}).status_code == 403


def test_sync_waits_while_offline(settings, Session, session, processed):
    def refuse(request):
        raise httpx.ConnectError("no route to host")
    offline = httpx.Client(transport=httpx.MockTransport(refuse))
    sent, pending = push_pending(Session, laptop_settings(settings), client=offline)
    assert (sent, pending) == (0, 1)
    session.expire_all()
    row = session.scalar(select(Outbox).where(Outbox.kind == "sync_record"))
    assert row.status == "pending" and row.last_error.startswith("network")


def test_trust_key_checks_the_key_id(server, signer):
    from atlas.sync import SyncError
    _, ServerSession, _ = server
    with ServerSession() as s, pytest.raises(SyncError):
        trust_key(s, "0" * 16, signer.public_b64)
