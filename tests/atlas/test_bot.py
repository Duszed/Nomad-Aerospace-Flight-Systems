"""Steps 5-6 - the real aiogram bot, driven through a local fake Bot API.

No internet and no Telegram account: FakeTelegram serves the HTTP Bot API on
localhost and plays the farmer's phone.
"""

from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from atlas.bot import deliver_pending, make_bot, running_bot
from atlas.bot.fake_telegram import FAKE_TOKEN, FakeTelegram
from atlas.db import Confirmation, Farmer, Outbox, Share, ShareEvent, utcnow
from atlas.i18n import t
from atlas.pipeline import process_log
from atlas.records import JobIn

from conftest import DEMO_JOB

CHAT = 555001
PHONE = "+998900000001"          # the sample farmer in DEMO_JOB


@asynccontextmanager
async def rig(settings, Session):
    tg = FakeTelegram()
    await tg.start()
    cfg = replace(settings, bot_token=FAKE_TOKEN, telegram_api=tg.base_url)
    try:
        async with running_bot(cfg, Session) as bot:
            yield tg, bot, cfg
    finally:
        await tg.stop()


async def register(tg, chat=CHAT, phone=PHONE, language="uz"):
    """/start, share the phone number, and return the report the bot sends."""
    tg.user_text(chat, "/start", language)
    await tg.wait_for("sendMessage")
    n = len(tg.sent)
    tg.user_contact(chat, phone, language)
    return await tg.wait_for("sendDocument", since=n)


def button(item, prefix):
    return next(b for b in FakeTelegram.buttons(item) if b["callback_data"].startswith(prefix))


def run(coro):
    return asyncio.run(coro)


# --- registration ------------------------------------------------------------

def test_start_asks_for_the_phone_number(settings, Session, processed):
    async def scenario():
        async with rig(settings, Session) as (tg, _, _):
            tg.user_text(CHAT, "/start")
            msg = await tg.wait_for("sendMessage")
            assert msg["params"]["text"] == t("uz", "bot_welcome")
            keyboard = json.loads(msg["params"]["reply_markup"])["keyboard"]
            assert keyboard[0][0]["request_contact"] is True
    run(scenario())


def test_unknown_phone_is_not_registered(settings, Session, session, processed):
    async def scenario():
        async with rig(settings, Session) as (tg, _, _):
            tg.user_contact(CHAT, "+998 91 111 22 33")
            msg = await tg.wait_for("sendMessage")
            assert msg["params"]["text"] == t("uz", "bot_unknown_phone")
    run(scenario())
    assert session.scalar(select(Farmer)).telegram_chat_id is None


def test_someone_elses_contact_card_is_refused(settings, Session, session, processed):
    """Forwarding the farmer's contact card must not hijack their reports."""
    async def scenario():
        async with rig(settings, Session) as (tg, _, _):
            tg.user_contact(999, PHONE, contact_user_id=CHAT)
            msg = await tg.wait_for("sendMessage")
            assert msg["params"]["text"] == t("uz", "bot_not_own_contact")
    run(scenario())
    assert session.scalar(select(Farmer)).telegram_chat_id is None


def test_registration_delivers_the_waiting_report(settings, Session, session, processed):
    async def scenario():
        async with rig(settings, Session) as (tg, _, cfg):
            report = await register(tg)
            return report, cfg
    report, cfg = run(scenario())

    assert int(report["params"]["chat_id"]) == CHAT
    doc = report["files"]["document"]
    assert doc["data"].startswith(b"%PDF") and doc["filename"].endswith("-uz.pdf")
    caption = report["params"]["caption"]
    assert "Qamrov: 97,2%" in caption and "taxminiy" in caption
    assert t("uz", "sample_notice") in caption
    labels = [b["text"] for b in FakeTelegram.buttons(report)]
    assert labels == [t("uz", "btn_confirm"), t("uz", "btn_share")]

    session.expire_all()
    farmer = session.scalar(select(Farmer))
    assert farmer.telegram_chat_id == CHAT and farmer.registered_at is not None
    row = session.scalar(select(Outbox).where(Outbox.kind == "telegram_report"))
    assert row.status == "sent" and row.sent_at is not None


# --- queueing ----------------------------------------------------------------

def test_report_waits_until_the_farmer_registers(settings, Session, session, processed):
    async def scenario():
        async with rig(settings, Session) as (tg, bot, cfg):
            assert await deliver_pending(bot, Session, cfg) == 0
            assert not [i for i in tg.sent if i["method"] == "sendDocument"]
    run(scenario())
    row = session.scalar(select(Outbox).where(Outbox.kind == "telegram_report"))
    assert row.status == "pending" and row.attempts == 0


def test_offline_reports_are_queued_and_sent_when_back_online(settings, Session, session,
                                                             processed):
    farmer = session.scalar(select(Farmer))
    farmer.telegram_chat_id = CHAT
    session.commit()
    with socket.socket() as s:                      # a port with nothing listening
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = replace(settings, bot_token=FAKE_TOKEN, telegram_api=f"http://127.0.0.1:{port}")

    async def scenario():
        bot = make_bot(cfg)
        try:
            # 1. no connection: nothing sent, nothing lost
            assert await deliver_pending(bot, Session, cfg) == 0
            with Session() as s2:
                row = s2.scalar(select(Outbox).where(Outbox.kind == "telegram_report"))
                assert row.status == "pending" and row.attempts == 1
                assert row.last_error.startswith("network")
            # 2. connection is back
            tg = FakeTelegram(port=port)
            await tg.start()
            try:
                assert await deliver_pending(bot, Session, cfg) == 1
                assert tg.sent[-1]["method"] == "sendDocument"
                assert await deliver_pending(bot, Session, cfg) == 0   # not sent twice
            finally:
                await tg.stop()
        finally:
            await bot.session.close()
    run(scenario())
    session.expire_all()
    assert session.scalar(select(Outbox).where(
        Outbox.kind == "telegram_report")).status == "sent"


def test_modified_record_is_never_sent(settings, Session, session, processed):
    import sqlite3
    farmer = session.scalar(select(Farmer))
    farmer.telegram_chat_id = CHAT
    session.commit()
    con = sqlite3.connect(settings.data_dir / "atlas.db")
    con.execute("UPDATE record_version SET payload = replace(payload, 'Cotton', 'Wheat')")
    con.commit()
    con.close()

    async def scenario():
        async with rig(settings, Session) as (tg, bot, cfg):
            assert await deliver_pending(bot, Session, cfg) == 0
            assert not [i for i in tg.sent if i["method"] == "sendDocument"]
    run(scenario())
    session.expire_all()
    row = session.scalar(select(Outbox).where(Outbox.kind == "telegram_report"))
    assert row.status == "failed" and "verification" in row.last_error


# --- confirm -----------------------------------------------------------------

def test_farmer_confirms_the_record(settings, Session, session, processed):
    async def scenario():
        async with rig(settings, Session) as (tg, _, _):
            report = await register(tg)
            n = len(tg.sent)
            tg.user_press(CHAT, button(report, "confirm:"), report["result"])
            edit = await tg.wait_for("editMessageReplyMarkup", since=n)
            answer = await tg.wait_for("answerCallbackQuery", since=n)
            assert answer["params"]["text"] == t("uz", "bot_confirmed")
            # the Confirm button is gone, Share stays
            assert [b["callback_data"].split(":")[0] for b in FakeTelegram.buttons(edit)] == ["share"]

            n = len(tg.sent)
            tg.user_press(CHAT, button(report, "confirm:"), report["result"])
            again = await tg.wait_for("answerCallbackQuery", since=n)
            assert again["params"]["text"] == t("uz", "bot_already_confirmed")
    run(scenario())
    rows = session.scalars(select(Confirmation)).all()
    assert len(rows) == 1 and rows[0].record_id == processed.record.id


# --- share / list / revoke ---------------------------------------------------

async def share_with_bank(tg, report):
    n = len(tg.sent)
    tg.user_press(CHAT, button(report, "share:"), report["result"])
    menu = await tg.wait_for("sendMessage", since=n, where=FakeTelegram.has_button("sw:"))
    n = len(tg.sent)
    bank = next(b for b in FakeTelegram.buttons(menu) if b["callback_data"].endswith(":bank"))
    tg.user_press(CHAT, bank, menu["result"])
    return menu, await tg.wait_for("sendDocument", since=n)


def test_share_creates_an_expiring_link_for_the_chosen_recipient(settings, Session, session,
                                                                 processed):
    async def scenario():
        async with rig(settings, Session) as (tg, _, cfg):
            report = await register(tg)
            menu, shared = await share_with_bank(tg, report)
            return menu, shared, cfg
    menu, shared, cfg = run(scenario())

    assert menu["params"]["text"] == t("uz", "bot_choose_recipient")
    kinds = [b["callback_data"].rsplit(":", 1)[1] for b in FakeTelegram.buttons(menu)]
    assert kinds == ["bank", "insurer", "buyer", "cluster"]

    share = session.scalar(select(Share).where(Share.recipient_type == "bank"))
    assert share is not None and share.revoked_at is None
    assert timedelta(days=29) < share.expires_at - utcnow() <= timedelta(days=30)
    url = f"{cfg.base_url}/v/{share.token}"
    assert url in shared["params"]["caption"]
    assert shared["files"]["document"]["data"].startswith(b"%PDF")
    assert shared["files"]["document"]["filename"].startswith("bank-")
    events = [e.kind for e in session.scalars(
        select(ShareEvent).where(ShareEvent.share_id == share.id))]
    assert events == ["created"]


def test_farmer_lists_and_revokes_shares(settings, Session, session, processed):
    async def scenario():
        async with rig(settings, Session) as (tg, _, _):
            report = await register(tg)
            await share_with_bank(tg, report)

            n = len(tg.sent)
            tg.user_text(CHAT, "/shares")
            listing = await tg.wait_for("sendMessage", since=n)
            text = listing["params"]["text"]
            assert t("uz", "bot_shares_header") in text
            assert "Bank" in text and t("uz", "rcp_owner") in text
            revoke_bank = next(b for b in FakeTelegram.buttons(listing) if "Bank" in b["text"])

            n = len(tg.sent)
            tg.user_press(CHAT, revoke_bank, listing["result"])
            done = await tg.wait_for("sendMessage", since=n)
            assert done["params"]["text"] == t("uz", "bot_revoked")

            n = len(tg.sent)
            tg.user_text(CHAT, "/shares")
            after = await tg.wait_for("sendMessage", since=n)
            assert "Bank" not in after["params"]["text"]
    run(scenario())

    share = session.scalar(select(Share).where(Share.recipient_type == "bank"))
    assert share.revoked_at is not None
    events = [e.kind for e in session.scalars(
        select(ShareEvent).where(ShareEvent.share_id == share.id))]
    assert events == ["created", "revoked"]


def test_another_farmer_cannot_touch_this_record(settings, Session, session, signer,
                                                 demo_boundary, faulty_ndjson, processed):
    """Forged button data from a different registered farmer is refused."""
    other = JobIn.model_validate({**DEMO_JOB, "farmer": {
        "name": "Other Farmer (sample)", "phone": "+998900000002", "language": "en"}})
    process_log(session, settings, signer, faulty_ndjson, other, demo_boundary)
    session.commit()
    intruder = 777002

    async def scenario():
        async with rig(settings, Session) as (tg, _, _):
            report = await register(tg)                              # farmer 1
            _, shared = await share_with_bank(tg, report)
            await register(tg, chat=intruder, phone="+998900000002", language="en")
            with Session() as s2:
                share_id = s2.scalar(select(Share.id).where(Share.recipient_type == "bank"))
            for data in (f"confirm:{processed.record.id}", f"share:{processed.record.id}",
                         f"sw:{processed.record.id}:insurer", f"revoke:{share_id}"):
                n = len(tg.sent)
                tg.user_press(intruder, {"callback_data": data}, report["result"] | {
                    "chat": {"id": intruder, "type": "private"}}, language="en")
                answer = await tg.wait_for("answerCallbackQuery", since=n)
                assert answer["params"]["text"] == t("en", "bot_not_yours"), data
    run(scenario())

    session.expire_all()
    assert session.scalars(select(Confirmation)).all() == []
    assert session.scalar(select(Share).where(Share.recipient_type == "insurer")) is None
    assert session.scalar(select(Share).where(Share.recipient_type == "bank")).revoked_at is None


def test_unregistered_user_cannot_use_buttons(settings, Session, session, processed):
    async def scenario():
        async with rig(settings, Session) as (tg, _, _):
            fake_message = {"message_id": 1, "date": 1, "chat": {"id": 42, "type": "private"}}
            tg.user_press(42, {"callback_data": f"confirm:{processed.record.id}"},
                          fake_message, language="ru")
            answer = await tg.wait_for("answerCallbackQuery")
            assert answer["params"]["text"] == t("ru", "bot_not_registered")
            n = len(tg.sent)
            tg.user_text(42, "/shares", "en")
            msg = await tg.wait_for("sendMessage", since=n)
            assert msg["params"]["text"] == t("en", "bot_not_registered")
    run(scenario())
    assert session.scalars(select(Confirmation)).all() == []


# --- language ----------------------------------------------------------------

def test_farmer_can_switch_language(settings, Session, session, processed):
    async def scenario():
        async with rig(settings, Session) as (tg, _, _):
            await register(tg)
            n = len(tg.sent)
            tg.user_text(CHAT, "/lang")
            menu = await tg.wait_for("sendMessage", since=n)
            assert [b["text"] for b in FakeTelegram.buttons(menu)] == [
                "Oʻzbekcha", "Русский", "English"]
            n = len(tg.sent)
            tg.user_press(CHAT, button(menu, "lang:ru"), menu["result"])
            done = await tg.wait_for("sendMessage", since=n)
            assert done["params"]["text"] == t("ru", "bot_language_set")
            n = len(tg.sent)
            tg.user_text(CHAT, "/shares")
            listing = await tg.wait_for("sendMessage", since=n)
            assert t("ru", "bot_shares_header") in listing["params"]["text"]
    run(scenario())
    session.expire_all()
    assert session.scalar(select(Farmer)).language == "ru"
