"""The one-command demo: no drone, no internet, no Telegram account.

    python -m atlas demo

    simulator -> gateway -> Atlas -> signed record -> PDF -> Telegram bot
    -> share link -> verify page

and then proves the three verify states:

    Verified   the fresh record
    Modified   after one byte of the stored record is changed
    Revoked    after the farmer revokes the share

Everything it creates is SAMPLE DATA, not a customer record, and is kept
under data/demo/.

Telegram: by default the real bot code runs against a local stand-in for
the Bot API and a scripted farmer (atlas/bot/fake_telegram.py). With
`--live` and a real ATLAS_BOT_TOKEN the same bot talks to Telegram and
waits for you to play the farmer on your own phone.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx
import uvicorn
from shapely.geometry import Polygon
from sqlalchemy import select

from . import SAMPLE_DATA_NOTICE
from ._telemetry import spray_planner
from .bot import running_bot
from .bot.fake_telegram import FAKE_TOKEN, FakeTelegram
from .compute import compute_coverage
from .config import Settings
from .db import Confirmation, Farmer, Share, init_db
from .ingest import track_from_dataflash
from .ingest.dataflash_writer import write_dataflash
from .live import capture_simulated_flight
from .pipeline import process_log
from .records import JobIn, Signer
from .shares import owner_share, report_filename, report_pdf, share_state, share_url
from .web import create_app

MARKER = ".atlas-demo"
DEMO_AREA_HA, DEMO_WIDTH_M = 4.2, 140.0
FARMER_CHAT = 700_000_001

DEMO_JOB = {
    "farmer": {"name": "Namuna Fermer (sample)", "phone": "+998 90 000 00 01",
               "language": "uz"},
    "field_name": "Sample field 4.2 ha",
    "crop": "Cotton (sample)",
    "product": "Sample product 25 EC",
    "batch": "SAMPLE-0001",
    "planned_rate_l_ha": 15.0,
    "swath_m": 7.0,
    "pilot": "Sample Pilot",
    "drone_id": "K30-SIM-001",
    "weather": {"temperature_c": 24.0, "wind_ms": 2.5, "wind_direction": "NE",
                "humidity_pct": 41.0},
    "sample_data": True,
}


@dataclass
class DemoResult:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    owner_url: str = ""
    share_url: str = ""
    pdfs: list[Path] = field(default_factory=list)
    data_dir: Path | None = None

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(passed for _, passed, _ in self.checks)


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fresh_dir(data_dir: Path) -> None:
    """Start from an empty demo folder - but only ever delete our own."""
    if data_dir.exists():
        if any(data_dir.iterdir()) and not (data_dir / MARKER).exists():
            raise RuntimeError(
                f"{data_dir} exists and is not a demo folder; refusing to overwrite it")
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    (data_dir / MARKER).write_text(SAMPLE_DATA_NOTICE + "\n", encoding="utf-8")


class _Server:
    """The verify server on a background thread."""

    def __init__(self, app, port: int) -> None:
        self.server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("verify server did not start (is the port in use?)")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _flip_one_byte(db_path: Path, record_id: str) -> tuple[str, str]:
    """Change one character of the stored record, behind Atlas's back."""
    con = sqlite3.connect(db_path)
    (original,) = con.execute("SELECT payload FROM record_version WHERE id=?",
                              (record_id,)).fetchone()
    i = original.index('"coverage_pct":') + len('"coverage_pct":')
    tampered = original[:i] + ("8" if original[i] != "8" else "7") + original[i + 1:]
    con.execute("UPDATE record_version SET payload=? WHERE id=?", (tampered, record_id))
    con.commit()
    con.close()
    return original, tampered


def _restore(db_path: Path, record_id: str, original: str) -> None:
    con = sqlite3.connect(db_path)
    con.execute("UPDATE record_version SET payload=? WHERE id=?", (original, record_id))
    con.commit()
    con.close()


async def _status(http: httpx.AsyncClient, url: str) -> str:
    token = url.rsplit("/", 1)[1]
    base = url.rsplit("/v/", 1)[0]
    return (await http.get(f"{base}/api/verify/{token}")).json()["status"]


async def _wait_db(Session, predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while True:
        with Session() as session:
            value = predicate(session)
        if value:
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {what}")
        await asyncio.sleep(0.5)


async def _scripted_farmer(tg: FakeTelegram, phone: str, say) -> None:
    """Play the farmer's phone: register, receive, confirm, share with the bank."""
    tg.user_text(FARMER_CHAT, "/start")
    await tg.wait_for("sendMessage")
    n = len(tg.sent)
    tg.user_contact(FARMER_CHAT, phone)
    report = await tg.wait_for("sendDocument", since=n)
    say(f"farmer registered by phone and received "
        f"{report['files']['document']['filename']} "
        f"({len(report['files']['document']['data']) // 1024} kB) with a summary")

    buttons = {b["callback_data"].split(":")[0]: b for b in FakeTelegram.buttons(report)}
    n = len(tg.sent)
    tg.user_press(FARMER_CHAT, buttons["confirm"], report["result"])
    await tg.wait_for("editMessageReplyMarkup", since=n)
    await tg.wait_for("sendMessage", since=n)
    say("farmer tapped Confirm")

    n = len(tg.sent)
    tg.user_press(FARMER_CHAT, buttons["share"], report["result"])
    menu = await tg.wait_for("sendMessage", since=n, where=FakeTelegram.has_button("sw:"))
    bank = next(b for b in FakeTelegram.buttons(menu) if b["callback_data"].endswith(":bank"))
    n = len(tg.sent)
    tg.user_press(FARMER_CHAT, bank, menu["result"])
    await tg.wait_for("sendDocument", since=n)
    say("farmer tapped Share -> Bank and received an expiring link")


async def _scripted_revoke(tg: FakeTelegram, say) -> None:
    n = len(tg.sent)
    tg.user_text(FARMER_CHAT, "/shares")
    listing = await tg.wait_for("sendMessage", since=n,
                                where=FakeTelegram.has_button("revoke:"))
    revoke = next(b for b in FakeTelegram.buttons(listing) if "Bank" in b["text"])
    n = len(tg.sent)
    tg.user_press(FARMER_CHAT, revoke, listing["result"])
    await tg.wait_for("answerCallbackQuery", since=n)
    say("farmer opened /shares and revoked the bank's link")


async def _telegram_and_verify(settings: Settings, Session, record_id: str, result: DemoResult,
                               say, live: bool) -> None:
    def check(name: str, passed: bool, detail: str = "") -> None:
        result.checks.append((name, passed, detail))
        say(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))

    tg = None
    if not live:
        tg = FakeTelegram()
        await tg.start()
        settings = replace(settings, bot_token=FAKE_TOKEN, telegram_api=tg.base_url)
    db_path = settings.data_dir / "atlas.db"

    try:
        async with running_bot(settings, Session), httpx.AsyncClient(timeout=30) as http:
            # --- step 5: send to farmer; step 6: share -------------------------
            if live:
                say("LIVE TELEGRAM: open your bot, press Start and share your phone number,")
                say("then tap Confirm, then Share -> Bank. Waiting up to 10 minutes ...")
            else:
                phone = JobIn.model_validate(DEMO_JOB).farmer.phone
                await _scripted_farmer(tg, phone, say)

            wait = 600 if live else 20
            await _wait_db(Session, lambda s: s.scalar(select(Farmer).where(
                Farmer.telegram_chat_id.is_not(None))), wait, "the farmer to register")
            check("farmer registered by phone and received the PDF", True)
            await _wait_db(Session, lambda s: s.get(Confirmation, record_id), wait,
                           "the farmer to tap Confirm")
            check("farmer confirmed the record", True)
            bank = await _wait_db(Session, lambda s: s.scalar(select(Share).where(
                Share.recipient_type == "bank")), wait, "the farmer to share with the bank")
            result.share_url = share_url(settings, bank)
            check("share link created for the bank", True, result.share_url)

            # --- step 7: verify -------------------------------------------------
            status = await _status(http, result.share_url)
            check('share link shows "Verified"', status == "verified", status)
            status = await _status(http, result.owner_url)
            check('QR link on the farmer\'s PDF shows "Verified"', status == "verified", status)
            page = await http.get(result.share_url + "?lang=en")
            check("verify page shows the record and the map",
                  page.status_code == 200 and "Sample field 4.2 ha" in page.text
                  and "/map.png" in page.text)

            original, _ = _flip_one_byte(db_path, record_id)
            status = await _status(http, result.share_url)
            check('changing one byte of the record shows "Modified"', status == "modified",
                  status)
            _restore(db_path, record_id, original)
            status = await _status(http, result.share_url)
            check('restoring that byte shows "Verified" again', status == "verified", status)

            if live:
                say("LIVE TELEGRAM: now send /shares to the bot and revoke the Bank share ...")
                await _wait_db(Session, lambda s: s.scalar(select(Share).where(
                    Share.recipient_type == "bank", Share.revoked_at.is_not(None))),
                    wait, "the bank share to be revoked")
            else:
                await _scripted_revoke(tg, say)
            status = await _status(http, result.share_url)
            check('revoking the share shows "Revoked"', status == "revoked", status)
            status = await _status(http, result.owner_url)
            check("the farmer's own link is unaffected", status == "verified", status)
    finally:
        if tg is not None:
            await tg.stop()


def run_demo(data_dir: Path = Path("data/demo"), speedup: float = 60.0, port: int = 8000,
             udp_port: int = 14601, serve: bool = False, live: bool = False,
             phone: str | None = None, say=print) -> DemoResult:
    """Run the whole pipeline end to end. Returns the checks that were made."""
    data_dir = Path(data_dir)
    _fresh_dir(data_dir)
    env = Settings.from_env()
    settings = Settings(data_dir=data_dir, base_url=f"http://127.0.0.1:{port}",
                        share_ttl_days=env.share_ttl_days,
                        bot_token=env.bot_token if live else None)
    if live and not settings.bot_token:
        raise RuntimeError("--live needs a real bot token in ATLAS_BOT_TOKEN")
    settings.ensure_dirs()
    Session = init_db(settings.database_url)
    signer = Signer.load_or_create(settings.key_path)
    result = DemoResult(data_dir=data_dir)
    job_data = json.loads(json.dumps(DEMO_JOB))
    if phone:
        job_data["farmer"]["phone"] = phone
    job = JobIn.model_validate(job_data)
    boundary = Polygon(spray_planner.field_boundary_lonlat(DEMO_AREA_HA, DEMO_WIDTH_M))

    say(f"NOMAD ATLAS DEMO - {SAMPLE_DATA_NOTICE}")
    say("")
    say("[1] RECEIVE  simulator -> MAVLink/UDP -> gateway -> NDJSON")
    ndjson = settings.logs_dir / "demo-flight.ndjson"
    rows = capture_simulated_flight(ndjson, speedup=speedup, port=udp_port)
    say(f"    captured {rows} telemetry rows through the real gateway ({speedup:g}x real time)")

    say("[2] COMPUTE  coverage geometry (shapely + pyproj, UTM zone chosen automatically)")
    say("[3] RECORD   SQLite + SHA-256 of the input log + Ed25519 signature")
    with Session() as session:
        done = process_log(session, settings, signer, ndjson, job, boundary)
        owner = owner_share(session, done.record)
        session.commit()
        record_id = done.record.id
        res = done.coverage.numbers()
        say(f"    field {res['field_area_ha']:.2f} ha (EPSG:{res['utm_epsg']}), "
            f"coverage {res['coverage_pct']:.1f}%, missed {res['missed_area_ha']:.3f} ha in "
            f"{res['missed_strips']} strips, double-sprayed {res['overlap_area_ha']:.3f} ha")
        say(f"    applied {res['litres_applied']:.1f} L ({res['litres_basis']}), "
            f"{res['actual_rate_l_ha']:.1f} L/ha vs {res['planned_rate_l_ha']:.1f} planned")
        say(f"    record {record_id} signed with key {done.record.key_id}")
        result.owner_url = share_url(settings, owner)

        # the other ingest path: the same flight as an ArduPilot DataFlash .bin
        import simulate_telemetry as sim
        bin_path = write_dataflash(sim.mission_samples(faults=sim.DEFAULT_FAULTS),
                                   settings.logs_dir / "demo-flight.bin")
        bin_cov = compute_coverage(track_from_dataflash(bin_path), boundary, job.swath_m,
                                   job.planned_rate_l_ha)
        same = abs(bin_cov.coverage_pct - done.coverage.coverage_pct) < 0.3
        result.checks.append(("DataFlash .bin of the same flight gives the same coverage",
                              same, f"{bin_cov.coverage_pct:.1f}%"))
        say(f"    DataFlash .bin of the same flight (pymavlink DFReader): "
            f"coverage {bin_cov.coverage_pct:.1f}%")

        say("[4] REPORT   one-page PDF with map and QR, in Uzbek, Russian and English")
        for lang in ("uz", "ru", "en"):
            path = settings.reports_dir / report_filename(done.record, lang)
            path.write_bytes(report_pdf(settings, done.record, owner, lang))
            result.pdfs.append(path)
            say(f"    {path}")

    server = _Server(create_app(settings, Session), port)
    server.start()
    try:
        say("[5] SEND     Telegram bot (aiogram 3)" +
            ("" if live else " against a local stand-in for the Bot API"))
        say("[6] SHARE    farmer shares with the bank")
        say("[7] VERIFY   FastAPI verify page")
        asyncio.run(_telegram_and_verify(settings, Session, record_id, result,
                                         lambda m: say("    " + m), live))
        say("")
        say("RESULT: " + ("ALL CHECKS PASSED" if result.ok else "SOME CHECKS FAILED"))
        if serve:
            with Session() as session:
                bank = session.scalar(select(Share).where(Share.recipient_type == "bank"))
                bank_state = share_state(bank) if bank else "-"
            say("")
            say("Verify server is running. Open in a browser:")
            say(f"  {result.owner_url}   (farmer's QR link: Verified)")
            say(f"  {result.share_url}   (bank's link: {bank_state})")
            say("Press Ctrl+C to stop.")
            try:
                while server.thread.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    finally:
        server.stop()
    return result
