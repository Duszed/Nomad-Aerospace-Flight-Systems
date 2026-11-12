""""Load demo data": sample farmers, fields and three finished jobs.

Everything here is SAMPLE DATA, not a customer record. It lives in its own
folder with its own database and signing key, and is built by the real
pipeline: the simulator flies each field, the log is written as an
ArduPilot DataFlash .bin, Atlas reads it, computes coverage and signs the
record. The real bot code then runs against a local stand-in for Telegram
with scripted farmers, so the three jobs end up in three different states:

    job 1   delivered, confirmed by the farmer, shared with a bank
            (and with an insurer, later revoked)
    job 2   delivered, not confirmed yet
    job 3   queued - this farmer has not linked Telegram

Nothing is sent to the real Telegram or to any server.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from shapely.geometry import Polygon
from sqlalchemy import select

from .._telemetry import spray_planner
from ..bot import running_bot
from ..bot.fake_telegram import FAKE_TOKEN, FakeTelegram
from ..config import Settings
from ..db import Farmer, Field, Share, init_db, utcnow
from ..demo import _fresh_dir
from ..ingest.dataflash_writer import write_dataflash
from ..pipeline import process_log
from ..records import JobIn, Signer
from ..shares import owner_share
from ..web.app import resolve

DEMO_JOBS = [
    {   # the repository's 4.2 ha demo field, flown imperfectly
        "farmer": {"name": "Namuna Fermer (sample)", "phone": "+998 90 000 00 01",
                   "language": "uz"},
        "field_name": "Sample field 4.2 ha", "crop": "Cotton (sample)",
        "product": "Sample product 25 EC", "batch": "SAMPLE-0001",
        "planned_rate_l_ha": 15.0, "pilot": "Sample Pilot", "drone_id": "K30-SIM-001",
        "weather": {"temperature_c": 24.0, "wind_ms": 2.5, "wind_direction": "NE",
                    "humidity_pct": 41.0},
        "area_ha": 4.2, "width_m": 140.0, "origin": (40.410983, 68.841300),
        "faults": "default", "flow_meter": False, "hours_ago": 2, "chat": 700_000_001,
    },
    {   # a clean flight with a flow meter: litres are measured, not estimated
        "farmer": {"name": "Namuna Dehqon (sample)", "phone": "+998 90 000 00 02",
                   "language": "ru"},
        "field_name": "Sample field 3.1 ha", "crop": "Wheat (sample)",
        "product": "Sample fungicide 250 SC", "batch": "SAMPLE-0002",
        "planned_rate_l_ha": 12.0, "pilot": "Sample Pilot", "drone_id": "K30-SIM-001",
        "weather": {"temperature_c": 19.0, "wind_ms": 1.2, "wind_direction": "W",
                    "humidity_pct": 55.0},
        "area_ha": 3.1, "width_m": 125.0, "origin": (40.4252, 68.8630),
        "faults": None, "flow_meter": True, "hours_ago": 26, "chat": 700_000_002,
    },
    {   # a rougher flight; this farmer is not on Telegram yet
        "farmer": {"name": "Namuna Xoʻjalik (sample)", "phone": "+998 90 000 00 03",
                   "language": "uz"},
        "field_name": "Sample field 5.6 ha", "crop": "Cotton (sample)",
        "product": "Sample product 25 EC", "batch": "SAMPLE-0003",
        "planned_rate_l_ha": 15.0, "pilot": "Sample Pilot 2", "drone_id": "K30-SIM-002",
        "weather": {"temperature_c": 28.0, "wind_ms": 4.0, "wind_direction": "S",
                    "humidity_pct": 33.0},
        "area_ha": 5.6, "width_m": 160.0, "origin": (40.3961, 68.8195),
        "faults": {"dropout": {"pass": 5, "from_m": 60.0, "to_m": 210.0},
                   "offset": {"pass": 11, "metres": -3.0}},
        "flow_meter": False, "hours_ago": 74, "chat": None,
    },
]

JOB_KEYS = ("farmer", "field_name", "crop", "product", "batch", "planned_rate_l_ha",
            "pilot", "drone_id", "weather")


async def _farmers_on_telegram(settings: Settings, Session) -> None:
    """Scripted farmers on the local Telegram stand-in."""
    tg = FakeTelegram()
    await tg.start()
    settings = replace(settings, bot_token=FAKE_TOKEN, telegram_api=tg.base_url)
    try:
        async with running_bot(settings, Session):
            reports = {}
            for spec in DEMO_JOBS:
                chat = spec["chat"]
                if chat is None:
                    continue
                lang = spec["farmer"]["language"]
                n = len(tg.sent)
                tg.user_text(chat, "/start", language=lang)
                await tg.wait_for("sendMessage", since=n)
                n = len(tg.sent)
                tg.user_contact(chat, spec["farmer"]["phone"], language=lang)
                reports[chat] = await tg.wait_for("sendDocument", since=n)

            # farmer 1: confirm, share with the bank and the insurer, revoke the insurer
            chat = DEMO_JOBS[0]["chat"]
            report = reports[chat]
            buttons = {b["callback_data"].split(":")[0]: b for b in FakeTelegram.buttons(report)}
            n = len(tg.sent)
            tg.user_press(chat, buttons["confirm"], report["result"])
            await tg.wait_for("editMessageReplyMarkup", since=n)
            await tg.wait_for("sendMessage", since=n)
            for kind in ("bank", "insurer"):
                n = len(tg.sent)
                tg.user_press(chat, buttons["share"], report["result"])
                menu = await tg.wait_for("sendMessage", since=n,
                                         where=FakeTelegram.has_button("sw:"))
                pick = next(b for b in FakeTelegram.buttons(menu)
                            if b["callback_data"].endswith(f":{kind}"))
                n = len(tg.sent)
                tg.user_press(chat, pick, menu["result"])
                await tg.wait_for("sendDocument", since=n)

            with Session() as session:
                insurer_id = session.scalar(select(Share.id).where(
                    Share.recipient_type == "insurer"))
            n = len(tg.sent)
            tg.user_text(chat, "/shares")
            listing = await tg.wait_for("sendMessage", since=n,
                                        where=FakeTelegram.has_button("revoke:"))
            revoke = next(b for b in FakeTelegram.buttons(listing)
                          if b["callback_data"] == f"revoke:{insurer_id}")
            n = len(tg.sent)
            tg.user_press(chat, revoke, listing["result"])
            await tg.wait_for("answerCallbackQuery", since=n)
    finally:
        await tg.stop()


def build_demo(demo_dir: Path, base_url: str) -> None:
    """Create the demo folder from scratch (only ever replaces a demo folder)."""
    import simulate_telemetry as sim

    demo_dir = Path(demo_dir)
    _fresh_dir(demo_dir)
    settings = Settings(data_dir=demo_dir, base_url=base_url)
    settings.ensure_dirs()
    Session = init_db(settings.database_url)
    signer = Signer.load_or_create(settings.key_path)
    now = utcnow().replace(microsecond=0)
    try:
        with Session() as session:
            for i, spec in enumerate(DEMO_JOBS, start=1):
                lat, lon = spec["origin"]
                faults = sim.DEFAULT_FAULTS if spec["faults"] == "default" else spec["faults"]
                samples = sim.mission_samples(
                    area_ha=spec["area_ha"], field_width_m=spec["width_m"], faults=faults,
                    origin_lat=lat, origin_lon=lon)
                log = write_dataflash(
                    samples, settings.logs_dir / f"sample-flight-{i}.bin",
                    start_utc=now - timedelta(hours=spec["hours_ago"]),
                    flow_meter=spec["flow_meter"])
                boundary = Polygon(spray_planner.field_boundary_lonlat(
                    spec["area_ha"], spec["width_m"], lat, lon))
                job = JobIn.model_validate(
                    {**{k: spec[k] for k in JOB_KEYS}, "sample_data": True})
                done = process_log(session, settings, signer, log, job, boundary)
                owner_share(session, done.record)
                fld = session.scalar(select(Field).join(Farmer, Field.farmer_id == Farmer.id)
                                     .where(Farmer.phone == job.farmer.phone,
                                            Field.name == job.field_name))
                fld.crop = spec["crop"]
            session.commit()

        asyncio.run(_farmers_on_telegram(settings, Session))

        with Session() as session:     # the bank looks at its link twice
            bank = session.scalar(select(Share).where(Share.recipient_type == "bank"))
            for _ in range(2):
                resolve(session, bank.token, log=True)
    finally:
        Session.kw["bind"].dispose()
