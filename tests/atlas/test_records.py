"""Step 3 - RECORD: canonical JSON, Ed25519 signature, immutability, versions."""

from __future__ import annotations

import json
import sqlite3

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from atlas.db import (
    Farmer, Field, ImmutableRecordError, Job, Outbox, RecordVersion, SigningKey,
)
from atlas.pipeline import process_log, read_track
from atlas.ingest import IngestError
from atlas.records import (
    MODIFIED, RECORD_SCHEMA, VERIFIED, JobIn, Signer, amend_record, canonical_json,
    create_record, latest_version, normalize_phone, sha256_hex, verify_record,
    verify_signature,
)

from conftest import DEMO_JOB


# --- canonical JSON ----------------------------------------------------------

def test_canonical_json_is_key_order_independent():
    a = canonical_json({"b": 1, "a": {"y": [1, 2.5], "x": None}})
    b = canonical_json({"a": {"x": None, "y": [1, 2.5]}, "b": 1})
    assert a == b == '{"a":{"x":null,"y":[1,2.5]},"b":1}'


def test_canonical_json_keeps_uzbek_and_cyrillic_text():
    text = canonical_json({"name": "Gʻolib oʻgʻli", "ru": "Поле"})
    assert "Gʻolib oʻgʻli" in text and "Поле" in text
    assert json.loads(text)["name"] == "Gʻolib oʻgʻli"


def test_canonical_json_rejects_nan():
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_canonical_json_round_trips():
    obj = {"lat": 40.4109830, "n": 15.0, "s": "x", "l": [0.1, 68.8413]}
    assert canonical_json(json.loads(canonical_json(obj))) == canonical_json(obj)


# --- signing -----------------------------------------------------------------

def test_signature_verifies_and_detects_changes():
    signer = Signer.generate()
    sig = signer.sign(b"record")
    assert verify_signature(signer.public_b64, b"record", sig)
    assert not verify_signature(signer.public_b64, b"recorD", sig)
    assert not verify_signature(Signer.generate().public_b64, b"record", sig)
    assert not verify_signature(signer.public_b64, b"record", "not-base64!!")


def test_signing_key_is_created_once_and_reloaded(tmp_path):
    path = tmp_path / "keys" / "k.pem"
    a = Signer.load_or_create(path)
    b = Signer.load_or_create(path)
    assert a.key_id == b.key_id and len(a.key_id) == 16
    assert b"PRIVATE KEY" in path.read_bytes()


# --- job input validation ----------------------------------------------------

def test_phone_numbers_are_normalised():
    assert normalize_phone("90 123 45 67") == "+998901234567"
    assert normalize_phone("+998 (90) 123-45-67") == "+998901234567"
    assert normalize_phone("998901234567") == "+998901234567"
    with pytest.raises(ValueError):
        normalize_phone("12345")


def test_job_input_rejects_unknown_and_missing_fields():
    with pytest.raises(ValidationError):
        JobIn.model_validate({**DEMO_JOB, "surprise": 1})
    with pytest.raises(ValidationError):
        JobIn.model_validate({k: v for k, v in DEMO_JOB.items() if k != "product"})
    with pytest.raises(ValidationError):
        JobIn.model_validate({**DEMO_JOB, "planned_rate_l_ha": 0})


# --- creating a record -------------------------------------------------------

def test_record_contains_every_required_fact(processed):
    p = json.loads(processed.record.payload)
    assert p["schema"] == RECORD_SCHEMA and p["version"] == 1
    assert p["sample_data"] is True
    assert p["farmer"]["name"] == "Namuna Fermer (sample)"
    assert p["field"]["name"] == "Sample field 4.2 ha"
    assert p["field"]["boundary"]["type"] == "Polygon"
    job = p["job"]
    assert (job["crop"], job["product"], job["batch"]) == (
        "Cotton", "Sample product 25 EC", "SAMPLE-0001")
    assert job["planned_rate_l_ha"] == 15.0
    assert job["pilot"] == "Sample Pilot" and job["drone_id"] == "K30-SIM-001"
    assert job["started_utc"].endswith("Z") and job["ended_utc"] > job["started_utc"]
    assert p["weather"]["wind_ms"] == 2.5
    assert p["results"]["coverage_pct"] == processed.coverage.numbers()["coverage_pct"]
    assert p["results"]["litres_basis"] == "estimated"
    assert p["geometry"]["missed"]["type"] in ("Polygon", "MultiPolygon")
    assert p["signer"] == {"key_id": processed.record.key_id, "algorithm": "Ed25519"}


def test_record_carries_sha256_of_the_input_log(processed, faulty_ndjson, settings):
    p = json.loads(processed.record.payload)
    digest = sha256_hex(faulty_ndjson.read_bytes())
    assert p["input"]["log_sha256"] == digest
    assert p["input"]["kind"] == "ndjson"
    # the exact input is kept on the laptop, named by its hash
    kept = settings.logs_dir / f"{digest[:16]}.ndjson"
    assert sha256_hex(kept.read_bytes()) == digest


def test_farmer_phone_is_not_in_the_shareable_record(processed):
    payload = json.loads(processed.record.payload)
    assert set(payload["farmer"]) == {"id", "name"}
    digits = "".join(ch for ch in processed.record.payload if ch.isdigit())
    assert "998900000001" not in digits and "phone" not in processed.record.payload


def test_record_is_stored_as_canonical_json_with_matching_hash(processed):
    rec = processed.record
    assert canonical_json(json.loads(rec.payload)) == rec.payload
    assert sha256_hex(rec.payload.encode("utf-8")) == rec.payload_sha256


def test_new_record_verifies(session, processed):
    v = verify_record(session, processed.record)
    assert v.status == VERIFIED and v.ok and v.problems == []
    assert v.recomputed_sha256 == processed.record.payload_sha256


def test_creating_a_record_queues_delivery_and_sync(session, processed):
    kinds = sorted(o.kind for o in session.scalars(select(Outbox)))
    assert kinds == ["sync_record", "telegram_report"]
    assert all(o.status == "pending" for o in session.scalars(select(Outbox)))


def test_same_farmer_and_field_are_reused(session, settings, signer, job_in,
                                          demo_boundary, faulty_ndjson, processed):
    process_log(session, settings, signer, faulty_ndjson, job_in, demo_boundary)
    session.commit()
    assert len(session.scalars(select(Farmer)).all()) == 1
    assert len(session.scalars(select(Field)).all()) == 1
    assert len(session.scalars(select(Job)).all()) == 2


def test_dataflash_log_produces_a_record_too(session, settings, signer, job_in,
                                             demo_boundary, faulty_samples, tmp_path):
    from atlas.ingest.dataflash_writer import write_dataflash
    path = write_dataflash(faulty_samples, tmp_path / "flight.bin")
    p = process_log(session, settings, signer, path, job_in, demo_boundary)
    payload = json.loads(p.record.payload)
    assert payload["input"]["kind"] == "dataflash"
    assert payload["job"]["started_utc"] == "2026-05-14T05:30:00Z"
    assert verify_record(session, p.record).ok


def test_unknown_log_type_is_refused(tmp_path):
    path = tmp_path / "flight.tlog"
    path.write_bytes(b"x")
    with pytest.raises(IngestError, match="unsupported log type"):
        read_track(path)


# --- tampering ---------------------------------------------------------------

def _raw_update(settings, sql, args):
    """Tamper behind Atlas's back, the way someone with file access would."""
    con = sqlite3.connect(settings.data_dir / "atlas.db")
    con.execute(sql, args)
    con.commit()
    con.close()


def _reload(session, record_id):
    session.expire_all()
    return session.get(RecordVersion, record_id)


def test_changing_one_byte_of_the_record_shows_modified(session, settings, processed):
    rec = processed.record
    i = rec.payload.index('"coverage_pct":') + len('"coverage_pct":')
    old = rec.payload[i]
    new = "8" if old != "8" else "7"
    tampered = rec.payload[:i] + new + rec.payload[i + 1:]
    assert sum(a != b for a, b in zip(rec.payload, tampered)) == 1
    _raw_update(settings, "UPDATE record_version SET payload=? WHERE id=?",
                (tampered, rec.id))
    v = verify_record(session, _reload(session, rec.id))
    assert v.status == MODIFIED
    assert "record hash does not match its content" in v.problems
    assert "signature does not match the record content" in v.problems


def test_tamper_with_matching_hash_still_fails_on_signature(session, settings, processed):
    """An attacker who also recomputes the hash still cannot forge the signature."""
    rec = processed.record
    payload = json.loads(rec.payload)
    payload["results"]["coverage_pct"] = 100.0
    text = canonical_json(payload)
    _raw_update(settings, "UPDATE record_version SET payload=?, payload_sha256=? WHERE id=?",
                (text, sha256_hex(text.encode("utf-8")), rec.id))
    v = verify_record(session, _reload(session, rec.id))
    assert v.status == MODIFIED
    assert v.problems == ["signature does not match the record content"]


def test_changed_hash_or_signature_shows_modified(session, settings, processed):
    rec = processed.record
    _raw_update(settings, "UPDATE record_version SET payload_sha256=? WHERE id=?",
                ("0" * 64, rec.id))
    assert verify_record(session, _reload(session, rec.id)).status == MODIFIED


def test_whitespace_only_change_shows_modified(session, settings, processed):
    rec = processed.record
    _raw_update(settings, "UPDATE record_version SET payload=? WHERE id=?",
                (rec.payload + " ", rec.id))
    assert verify_record(session, _reload(session, rec.id)).status == MODIFIED


def test_corrupt_record_shows_modified(session, settings, processed):
    rec = processed.record
    _raw_update(settings, "UPDATE record_version SET payload=? WHERE id=?",
                (rec.payload[:-1], rec.id))
    v = verify_record(session, _reload(session, rec.id))
    assert v.status == MODIFIED and v.payload is None


def test_untrusted_signing_key_shows_modified(session, settings, processed):
    """Re-signing with a key the verifier does not trust is not accepted."""
    rec = processed.record
    forger = Signer.generate()
    payload = json.loads(rec.payload)
    payload["results"]["coverage_pct"] = 100.0
    payload["signer"]["key_id"] = forger.key_id
    text = canonical_json(payload)
    _raw_update(
        settings,
        "UPDATE record_version SET payload=?, payload_sha256=?, signature=?, key_id=? WHERE id=?",
        (text, sha256_hex(text.encode()), forger.sign(text.encode()), forger.key_id, rec.id))
    v = verify_record(session, _reload(session, rec.id))
    assert v.status == MODIFIED
    assert any("not trusted" in p for p in v.problems)
    assert session.get(SigningKey, forger.key_id) is None


# --- immutability and versions ----------------------------------------------

def test_signed_record_cannot_be_edited_through_atlas(session, processed):
    processed.record.payload = processed.record.payload.replace("Cotton", "Wheat")
    with pytest.raises(ImmutableRecordError):
        session.commit()
    session.rollback()


def test_signed_record_cannot_be_deleted_through_atlas(session, processed):
    session.delete(processed.record)
    with pytest.raises(ImmutableRecordError):
        session.commit()
    session.rollback()


def test_amendment_creates_a_new_version_and_keeps_the_old_one(session, signer, processed):
    v1 = processed.record
    v1_payload, v1_hash = v1.payload, v1.payload_sha256
    v2 = amend_record(session, signer, v1, {"batch": "SAMPLE-0002"},
                      reason="Batch number mistyped")
    session.commit()

    assert v2.id != v1.id and v2.version == 2 and v2.job_id == v1.job_id
    p2 = json.loads(v2.payload)
    assert p2["job"]["batch"] == "SAMPLE-0002"
    assert p2["supersedes_sha256"] == v1_hash == v2.prev_sha256
    assert p2["amendment_reason"] == "Batch number mistyped"
    assert p2["results"] == json.loads(v1_payload)["results"]

    session.expire_all()
    old = session.get(RecordVersion, v1.id)
    assert old.payload == v1_payload                      # untouched
    assert verify_record(session, old).ok and verify_record(session, v2).ok
    assert latest_version(session, v1.job_id).id == v2.id


def test_amendment_rules(session, signer, processed):
    v1 = processed.record
    with pytest.raises(ValueError, match="reason"):
        amend_record(session, signer, v1, {"batch": "X"}, reason=" ")
    with pytest.raises(ValueError, match="cannot amend"):
        amend_record(session, signer, v1, {"coverage_pct": 100}, reason="nope")
    amend_record(session, signer, v1, {"batch": "X"}, reason="fix")
    with pytest.raises(ValueError, match="latest version"):
        amend_record(session, signer, v1, {"batch": "Y"}, reason="fix again")
