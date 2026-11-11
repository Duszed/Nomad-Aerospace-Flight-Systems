"""Background work that needs the internet: Telegram delivery or server sync.

Nothing here blocks the dashboard. Offline, everything stays queued in the
outbox and is retried when the connection returns.

    server address + sync password set  ->  push records to the Atlas server
                                            (the server talks to Telegram)
    otherwise, a bot token set          ->  this laptop runs the bot itself
    neither                             ->  nothing is sent anywhere

The "online" check only ever contacts the service that is configured
(the Atlas server or api.telegram.org) - no third-party connectivity pings.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
from urllib.parse import urlsplit

from sqlalchemy import func, select

from ..db import Outbox

log = logging.getLogger("atlas.services")

NOT_SET_UP = "not_set_up"
CHECKING = "checking"
ONLINE = "online"
OFFLINE = "offline"


def can_reach(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class Services:
    def __init__(self, runtime) -> None:
        self.rt = runtime
        self.state = NOT_SET_UP
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None

    # --- what is configured -----------------------------------------------------

    def target(self) -> tuple[str, int] | None:
        """The one host this laptop is set up to talk to, if any."""
        s = self.rt.settings
        if self.rt.demo or s is None:
            return None
        if self.rt.server_mode:
            url = urlsplit(s.server_url)
            return (url.hostname or "", url.port or (443 if url.scheme == "https" else 80))
        if s.bot_token:
            if s.telegram_api:                      # local stand-in (tests)
                url = urlsplit(s.telegram_api)
                return (url.hostname or "", url.port or 80)
            return ("api.telegram.org", 443)
        return None

    def waiting(self) -> int:
        """Outbox items this laptop still has to send."""
        if self.rt.demo:
            return 0
        kind = "sync_record" if self.rt.server_mode else "telegram_report"
        with self.rt.Session() as session:
            return session.scalar(select(func.count()).select_from(Outbox).where(
                Outbox.kind == kind, Outbox.status == "pending")) or 0

    # --- lifecycle --------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        if self.target() is None:
            self.state = NOT_SET_UP
            return
        self.state = CHECKING
        self._thread = threading.Thread(target=self._run, name="atlas-services", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        thread.join(timeout=15)
        self._loop = self._stop = None
        self.state = NOT_SET_UP

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:  # noqa: BLE001 - background work must never crash the app
            log.exception("background services stopped")

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        settings, Session = self.rt.settings, self.rt.Session
        host, port = self.target()
        tasks = [asyncio.create_task(self._probe(host, port))]
        if self.rt.server_mode:
            tasks.append(asyncio.create_task(self._sync_loop(settings, Session)))
        else:
            tasks.append(asyncio.create_task(self._bot_loop(settings, Session)))
        await self._stop.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe(self, host: str, port: int) -> None:
        while True:
            ok = await asyncio.to_thread(can_reach, host, port)
            self.state = ONLINE if ok else OFFLINE
            await asyncio.sleep(20)

    async def _sync_loop(self, settings, Session) -> None:
        from ..sync import push_pending
        while True:
            if self.state == ONLINE:
                try:
                    await asyncio.to_thread(push_pending, Session, settings)
                except Exception:  # noqa: BLE001
                    log.exception("sync pass failed")
            await asyncio.sleep(30)

    async def _bot_loop(self, settings, Session) -> None:
        from ..bot import run_bot
        while True:
            if self.state == ONLINE:
                try:
                    await run_bot(settings, Session)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - offline, bad token, ...
                    log.warning("Telegram bot stopped (%s); retrying", type(exc).__name__)
            await asyncio.sleep(15)
