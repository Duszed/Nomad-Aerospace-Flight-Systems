"""Step 5 - SEND TO FARMER, and step 6 - SHARE: the Telegram bot (aiogram 3).

Flow
----
/start            -> farmer shares their phone number -> matched to the
                     farmer the operator entered on the job -> registered
(new record)      -> PDF + summary with [Confirm] [Share] buttons
[Confirm]         -> confirmation stored next to the record
[Share]           -> bank / insurer / buyer / cluster -> expiring link + PDF
/shares           -> list of active shares, each with a [Revoke] button
/lang             -> Uzbek / Russian / English

Deliveries wait in the outbox table while there is no internet (or while
the farmer has not registered yet) and are sent when `deliver_pending`
next succeeds.

The bot only ever talks to a farmer about that farmer's own records.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ..config import Settings
from ..db import Confirmation, Farmer, Job, Outbox, RecordVersion, Share, utcnow
from ..i18n import LANGUAGE_NAMES, LANGUAGES, fmt_dt, norm_lang, t
from ..records import normalize_phone, verify_record
from ..report import record_summary
from ..shares import (
    RECIPIENTS, active_shares, create_share, open_count, owner_share, report_filename,
    report_pdf, revoke_share, share_url,
)

log = logging.getLogger("atlas.bot")
MAX_DELIVERY_ATTEMPTS = 5


def make_bot(settings: Settings) -> Bot:
    if not settings.bot_token:
        raise RuntimeError("ATLAS_BOT_TOKEN is not set")
    if settings.telegram_api:  # local fake Bot API (tests / demo)
        session = AiohttpSession(api=TelegramAPIServer.from_base(settings.telegram_api))
        return Bot(settings.bot_token, session=session)
    return Bot(settings.bot_token)


def report_keyboard(lang: str, record_id: str, confirmed: bool) -> InlineKeyboardMarkup:
    row = []
    if not confirmed:
        row.append(InlineKeyboardButton(text=t(lang, "btn_confirm"),
                                        callback_data=f"confirm:{record_id}"))
    row.append(InlineKeyboardButton(text=t(lang, "btn_share"),
                                    callback_data=f"share:{record_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def deliver_pending(bot: Bot, Session: sessionmaker, settings: Settings,
                          farmer_id: str | None = None) -> int:
    """Send queued reports to registered farmers. Returns how many were sent.

    Safe to call at any time: with no internet nothing is lost, rows stay
    pending and the next call tries again.
    """
    sent = 0
    with Session() as session:
        query = select(Outbox).where(Outbox.kind == "telegram_report",
                                     Outbox.status == "pending").order_by(Outbox.id)
        if farmer_id:
            query = query.where(Outbox.farmer_id == farmer_id)
        for row in session.scalars(query).all():
            farmer = session.get(Farmer, row.farmer_id)
            if farmer is None or farmer.telegram_chat_id is None:
                continue  # not registered yet: keep waiting
            record = session.get(RecordVersion, row.record_id)
            check = verify_record(session, record)
            if not check.ok:
                row.status, row.last_error = "failed", "record failed verification"
                session.commit()
                continue

            lang = farmer.language
            share = owner_share(session, record)
            pdf = report_pdf(settings, record, share, lang)
            confirmed = session.get(Confirmation, record.id) is not None
            session.commit()
            try:
                await bot.send_document(
                    farmer.telegram_chat_id,
                    BufferedInputFile(pdf, filename=report_filename(record, lang)),
                    caption=record_summary(check.payload, lang),
                    reply_markup=report_keyboard(lang, record.id, confirmed))
            except TelegramNetworkError as exc:
                # offline: stop here, everything stays queued for the next run
                row.attempts += 1
                row.last_error = f"network: {exc}"[:500]
                session.commit()
                log.info("offline - reports stay queued until the next attempt")
                break
            except TelegramAPIError as exc:
                row.attempts += 1
                row.last_error = str(exc)[:500]
                if row.attempts >= MAX_DELIVERY_ATTEMPTS:
                    row.status = "failed"
                session.commit()
                continue
            row.status, row.sent_at, row.last_error = "sent", utcnow(), ""
            row.attempts += 1
            session.commit()
            sent += 1
    return sent


def build_dispatcher(Session: sessionmaker, settings: Settings) -> Dispatcher:
    router = Router()

    def farmer_for(session, chat_id: int) -> Farmer | None:
        return session.scalar(select(Farmer).where(Farmer.telegram_chat_id == chat_id))

    def user_lang(farmer: Farmer | None, tg_user) -> str:
        if farmer is not None:
            return farmer.language
        return norm_lang(getattr(tg_user, "language_code", None))

    def owned_record(session, farmer: Farmer, record_id: str) -> RecordVersion | None:
        record = session.get(RecordVersion, record_id)
        if record is None or session.get(Job, record.job_id).farmer_id != farmer.id:
            return None
        return record

    # --- registration --------------------------------------------------------

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        with Session() as session:
            farmer = farmer_for(session, message.chat.id)
            lang = user_lang(farmer, message.from_user)
            if farmer is not None:
                await message.answer(t(lang, "bot_registered", name=farmer.name),
                                     reply_markup=ReplyKeyboardRemove())
                return
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=t(lang, "btn_share_phone"), request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True)
        await message.answer(t(lang, "bot_welcome"), reply_markup=keyboard)

    @router.message(F.contact)
    async def contact(message: Message, bot: Bot) -> None:
        lang = norm_lang(message.from_user.language_code)
        # only accept the sender's own number, as attested by Telegram
        if message.contact.user_id != message.from_user.id:
            await message.answer(t(lang, "bot_not_own_contact"))
            return
        try:
            phone = normalize_phone(message.contact.phone_number)
        except ValueError:
            await message.answer(t(lang, "bot_unknown_phone"))
            return
        with Session() as session:
            farmer = session.scalar(select(Farmer).where(Farmer.phone == phone))
            if farmer is None:
                await message.answer(t(lang, "bot_unknown_phone"),
                                     reply_markup=ReplyKeyboardRemove())
                return
            previous = farmer_for(session, message.chat.id)
            if previous is not None and previous.id != farmer.id:
                previous.telegram_chat_id = None
                session.flush()
            farmer.telegram_chat_id = message.chat.id
            farmer.registered_at = utcnow()
            session.commit()
            farmer_id, name, lang = farmer.id, farmer.name, farmer.language
        await message.answer(t(lang, "bot_registered", name=name),
                             reply_markup=ReplyKeyboardRemove())
        await deliver_pending(bot, Session, settings, farmer_id=farmer_id)

    # --- language ------------------------------------------------------------

    @router.message(Command("lang"))
    async def choose_language(message: Message) -> None:
        with Session() as session:
            lang = user_lang(farmer_for(session, message.chat.id), message.from_user)
        buttons = [[InlineKeyboardButton(text=LANGUAGE_NAMES[code], callback_data=f"lang:{code}")
                    for code in LANGUAGES]]
        await message.answer(t(lang, "bot_choose_language"),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @router.callback_query(F.data.startswith("lang:"))
    async def set_language(query: CallbackQuery) -> None:
        lang = norm_lang(query.data.split(":", 1)[1])
        with Session() as session:
            farmer = farmer_for(session, query.message.chat.id)
            if farmer is None:
                await query.answer(t(lang, "bot_not_registered"), show_alert=True)
                return
            farmer.language = lang
            session.commit()
        await query.answer()
        await query.message.answer(t(lang, "bot_language_set"))

    # --- confirm -------------------------------------------------------------

    @router.callback_query(F.data.startswith("confirm:"))
    async def confirm(query: CallbackQuery) -> None:
        record_id = query.data.split(":", 1)[1]
        with Session() as session:
            farmer = farmer_for(session, query.message.chat.id)
            if farmer is None:
                await query.answer(t(norm_lang(query.from_user.language_code),
                                     "bot_not_registered"), show_alert=True)
                return
            lang = farmer.language
            record = owned_record(session, farmer, record_id)
            if record is None:
                await query.answer(t(lang, "bot_not_yours"), show_alert=True)
                return
            if session.get(Confirmation, record.id) is not None:
                await query.answer(t(lang, "bot_already_confirmed"))
                return
            session.add(Confirmation(record_id=record.id, farmer_id=farmer.id))
            session.commit()
        await query.answer(t(lang, "bot_confirmed"))
        await query.message.edit_reply_markup(
            reply_markup=report_keyboard(lang, record_id, confirmed=True))
        await query.message.answer(t(lang, "bot_confirmed"))

    # --- share ---------------------------------------------------------------

    @router.callback_query(F.data.startswith("share:"))
    async def share_menu(query: CallbackQuery) -> None:
        record_id = query.data.split(":", 1)[1]
        with Session() as session:
            farmer = farmer_for(session, query.message.chat.id)
            if farmer is None:
                await query.answer(t(norm_lang(query.from_user.language_code),
                                     "bot_not_registered"), show_alert=True)
                return
            lang = farmer.language
            if owned_record(session, farmer, record_id) is None:
                await query.answer(t(lang, "bot_not_yours"), show_alert=True)
                return
        buttons = [[InlineKeyboardButton(text=t(lang, f"rcp_{kind}"),
                                         callback_data=f"sw:{record_id}:{kind}")]
                   for kind in RECIPIENTS]
        await query.answer()
        await query.message.answer(t(lang, "bot_choose_recipient"),
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @router.callback_query(F.data.startswith("sw:"))
    async def share_with(query: CallbackQuery) -> None:
        _, record_id, kind = query.data.split(":", 2)
        with Session() as session:
            farmer = farmer_for(session, query.message.chat.id)
            if farmer is None or kind not in RECIPIENTS:
                await query.answer(t(norm_lang(query.from_user.language_code),
                                     "bot_not_registered"), show_alert=True)
                return
            lang = farmer.language
            record = owned_record(session, farmer, record_id)
            if record is None:
                await query.answer(t(lang, "bot_not_yours"), show_alert=True)
                return
            share = create_share(session, record, kind, settings.share_ttl_days)
            session.commit()
            pdf = report_pdf(settings, record, share, lang)
            filename = f"{kind}-{report_filename(record, lang)}"
            text = t(lang, "bot_share_created", recipient=t(lang, f"rcp_{kind}"),
                     until=fmt_dt(share.expires_at, with_time=False),
                     url=share_url(settings, share))
        await query.answer()
        await query.message.answer_document(BufferedInputFile(pdf, filename=filename),
                                            caption=text)

    @router.message(Command("shares"))
    async def list_shares(message: Message) -> None:
        with Session() as session:
            farmer = farmer_for(session, message.chat.id)
            if farmer is None:
                await message.answer(t(norm_lang(message.from_user.language_code),
                                       "bot_not_registered"))
                return
            lang = farmer.language
            shares = active_shares(session, farmer.id)
            if not shares:
                await message.answer(t(lang, "bot_no_shares"))
                return
            lines, buttons = [t(lang, "bot_shares_header")], []
            for i, share in enumerate(shares, 1):
                payload = json.loads(session.get(RecordVersion, share.record_id).payload)
                recipient = t(lang, f"rcp_{share.recipient_type}")
                until = fmt_dt(share.expires_at, with_time=False) if share.expires_at else "∞"
                lines.append(f"{i}. " + t(lang, "bot_share_line", recipient=recipient,
                                          field=payload["field"]["name"], until=until,
                                          opens=open_count(session, share)))
                buttons.append([InlineKeyboardButton(
                    text=f"{i}. " + t(lang, "btn_revoke", recipient=recipient),
                    callback_data=f"revoke:{share.id}")])
        await message.answer("\n".join(lines),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @router.callback_query(F.data.startswith("revoke:"))
    async def revoke(query: CallbackQuery) -> None:
        share_id = query.data.split(":", 1)[1]
        with Session() as session:
            farmer = farmer_for(session, query.message.chat.id)
            if farmer is None:
                await query.answer(t(norm_lang(query.from_user.language_code),
                                     "bot_not_registered"), show_alert=True)
                return
            lang = farmer.language
            share = session.get(Share, share_id)
            if share is None or share.farmer_id != farmer.id:
                await query.answer(t(lang, "bot_not_yours"), show_alert=True)
                return
            revoke_share(session, share)
            session.commit()
        await query.answer(t(lang, "bot_revoked"))
        await query.message.answer(t(lang, "bot_revoked"))

    dp = Dispatcher()
    dp.include_router(router)
    return dp


@asynccontextmanager
async def running_bot(settings: Settings, Session: sessionmaker):
    """Run the bot in the background of the current event loop (tests, demo)."""
    bot = make_bot(settings)
    dp = build_dispatcher(Session, settings)
    task = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False, polling_timeout=1))
    try:
        yield bot
    finally:
        try:
            await dp.stop_polling()
        except RuntimeError:  # polling had not started yet
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await bot.session.close()


async def run_bot(settings: Settings, Session: sessionmaker, poll_outbox_s: float = 15.0) -> None:
    """Run the bot: long-poll Telegram and keep retrying queued deliveries."""
    bot = make_bot(settings)
    dp = build_dispatcher(Session, settings)

    async def outbox_loop() -> None:
        while True:
            try:
                await deliver_pending(bot, Session, settings)
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("delivery pass failed")
            await asyncio.sleep(poll_outbox_s)

    worker = asyncio.create_task(outbox_loop())
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        worker.cancel()
        await bot.session.close()
