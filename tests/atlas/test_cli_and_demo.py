"""The command line and the one-command demo (the definition of done)."""

from __future__ import annotations

import io
import json
import socket
from pathlib import Path

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from atlas.cli import main
from atlas.config import Settings, load_dotenv
from atlas.db import Confirmation, RecordVersion, Share, ShareEvent, init_db
from atlas.demo import DEMO_JOB, free_port, run_demo
from atlas.records import verify_record

REPO = Path(__file__).resolve().parents[2]


# --- the demo ----------------------------------------------------------------

@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """Run the full demo once: simulator -> gateway -> ... -> verify page."""
    lines: list[str] = []
    data_dir = tmp_path_factory.mktemp("demo") / "data"
    result = run_demo(data_dir, speedup=100.0, port=free_port(),
                      udp_port=free_port(socket.SOCK_DGRAM), say=lines.append)
    return result, lines, data_dir


def test_demo_passes_every_check(demo):
    result, lines, _ = demo
    failed = [name for name, ok, _ in result.checks if not ok]
    assert result.ok and not failed, failed
    assert "RESULT: ALL CHECKS PASSED" in lines


def test_demo_shows_verified_then_modified_then_revoked(demo):
    result, _, _ = demo
    by_name = {name: detail for name, _, detail in result.checks}
    assert by_name['share link shows "Verified"'] == "verified"
    assert by_name['changing one byte of the record shows "Modified"'] == "modified"
    assert by_name['restoring that byte shows "Verified" again'] == "verified"
    assert by_name['revoking the share shows "Revoked"'] == "revoked"
    assert by_name["the farmer's own link is unaffected"] == "verified"


def test_demo_went_through_the_real_gateway(demo):
    _, lines, data_dir = demo
    ndjson = data_dir / "logs" / "demo-flight.ndjson"
    kinds = {json.loads(line)["type"] for line in ndjson.read_text().splitlines()[:400]}
    assert {"GLOBAL_POSITION_INT", "VFR_HUD", "SERVO_OUTPUT_RAW", "SYS_STATUS"} <= kinds
    gw_log = (data_dir / "logs" / "demo-flight.gateway.log").read_text()
    assert "Heartbeat OK" in gw_log


def test_demo_record_is_signed_sample_data_with_sane_numbers(demo):
    _, _, data_dir = demo
    Session = init_db(Settings(data_dir=data_dir).database_url)
    with Session() as session:
        record = session.scalar(select(RecordVersion))
        assert verify_record(session, record).ok
        payload = json.loads(record.payload)
        assert payload["sample_data"] is True
        res = payload["results"]
        assert 96.0 < res["coverage_pct"] < 98.5
        assert res["missed_strips"] >= 2 and res["overlap_area_ha"] > 0.05
        assert res["litres_basis"] == "estimated"
        assert payload["input"]["kind"] == "ndjson"
        # farmer confirmed; the bank share was created, opened and revoked - all logged
        assert session.get(Confirmation, record.id) is not None
        bank = session.scalar(select(Share).where(Share.recipient_type == "bank"))
        kinds = [e.kind for e in session.scalars(
            select(ShareEvent).where(ShareEvent.share_id == bank.id).order_by(ShareEvent.id))]
        assert kinds[0] == "created" and "opened" in kinds and "revoked" in kinds
        assert kinds[-1] == "refused"


def test_demo_writes_one_page_pdfs_in_three_languages(demo):
    result, _, _ = demo
    assert sorted(p.name[-6:-4] for p in result.pdfs) == ["en", "ru", "uz"]
    for path in result.pdfs:
        reader = PdfReader(io.BytesIO(path.read_bytes()))
        assert len(reader.pages) == 1
        text = reader.pages[0].extract_text()
        assert "Sample data – not a customer record" in text
        assert result.owner_url in text


def test_demo_refuses_to_overwrite_a_non_demo_folder(tmp_path):
    (tmp_path / "customer.db").write_text("real data")
    with pytest.raises(RuntimeError, match="not a demo folder"):
        run_demo(tmp_path, say=lambda _: None)
    assert (tmp_path / "customer.db").read_text() == "real data"


def test_demo_job_is_labelled_as_sample():
    assert DEMO_JOB["sample_data"] is True and "sample" in DEMO_JOB["farmer"]["name"].lower()


# --- the command line --------------------------------------------------------

@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                 # no stray .env
    for name in ("ATLAS_DB_URL", "ATLAS_BOT_TOKEN", "ATLAS_SERVER_URL", "ATLAS_SYNC_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ATLAS_BASE_URL", "http://atlas.test")
    return tmp_path


def test_cli_ingest_a_dataflash_log_end_to_end(cli_env, capsys):
    """Steps 1-4 from the command line, using the files in examples/."""
    log = cli_env / "sample-flight.bin"
    assert main(["sample-log", str(log)]) == 0
    code = main(["ingest", str(log), "--job", str(REPO / "examples" / "job.json"),
                 "--boundary", str(REPO / "examples" / "field.geojson"), "--lang", "all"])
    out = capsys.readouterr().out
    assert code == 0
    assert "coverage 97.2%" in out and "(estimated)" in out
    assert "verify     http://atlas.test/v/" in out
    pdfs = sorted((cli_env / "data" / "reports").glob("*.pdf"))
    assert len(pdfs) == 3 and all(p.read_bytes().startswith(b"%PDF") for p in pdfs)

    record_id = next(line.split()[1] for line in out.splitlines() if line.startswith("record"))
    assert main(["verify", record_id]) == 0
    assert "VERIFIED" in capsys.readouterr().out


def test_cli_amend_creates_version_two(cli_env, capsys):
    log = cli_env / "f.bin"
    main(["sample-log", str(log)])
    main(["ingest", str(log), "--job", str(REPO / "examples" / "job.json"),
          "--boundary", str(REPO / "examples" / "field.geojson")])
    out = capsys.readouterr().out
    record_id = next(line.split()[1] for line in out.splitlines() if line.startswith("record"))
    assert main(["amend", record_id, "--set", "batch=SAMPLE-0002",
                 "--reason", "Batch number mistyped"]) == 0
    assert "version 2 created" in capsys.readouterr().out
    assert main(["amend", record_id, "--set", "coverage_pct=100", "--reason", "x"]) == 2


def test_cli_reports_bad_input_clearly(cli_env, capsys):
    bad = cli_env / "flight.tlog"
    bad.write_bytes(b"x")
    code = main(["ingest", str(bad), "--job", str(REPO / "examples" / "job.json"),
                 "--boundary", str(REPO / "examples" / "field.geojson")])
    assert code == 2 and "unsupported log type" in capsys.readouterr().err
    assert main(["verify", "no-such-record"]) == 2


def test_cli_keys_show_and_trust(cli_env, capsys):
    assert main(["keys", "show"]) == 0
    out = capsys.readouterr().out
    key_id = out.split("key_id")[1].split()[0]
    public = out.split("public_key")[1].split()[0]
    assert main(["keys", "trust", key_id, public, "--label", "laptop"]) == 0
    assert main(["keys", "trust", "0" * 16, public]) == 2


def test_cli_bot_needs_a_token(cli_env, capsys):
    assert main(["bot"]) == 2
    assert "ATLAS_BOT_TOKEN" in capsys.readouterr().err


def test_dotenv_is_read_but_real_environment_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nATLAS_SHARE_TTL_DAYS=7\nATLAS_BASE_URL='https://x.uz/'\n")
    monkeypatch.delenv("ATLAS_SHARE_TTL_DAYS", raising=False)
    monkeypatch.setenv("ATLAS_BASE_URL", "http://real.env")
    load_dotenv(env)
    s = Settings.from_env()
    assert s.share_ttl_days == 7 and s.base_url == "http://real.env"
    monkeypatch.delenv("ATLAS_SHARE_TTL_DAYS")


def test_example_files_are_labelled_sample_data():
    job = json.loads((REPO / "examples" / "job.json").read_text(encoding="utf-8"))
    assert job["sample_data"] is True
    assert "Sample data" in (REPO / "examples" / "field.geojson").read_text(encoding="utf-8")
