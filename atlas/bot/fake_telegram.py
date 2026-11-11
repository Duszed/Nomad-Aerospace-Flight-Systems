"""A local stand-in for the Telegram Bot API, for tests and the offline demo.

It speaks enough of the HTTP Bot API for the real aiogram bot code to run
against it unchanged: the bot long-polls `getUpdates` and calls
`sendMessage` / `sendDocument` / ... exactly as it would against Telegram.
Nothing leaves the machine and no real Telegram account is involved.

The other half is a scripted farmer: `user_text`, `user_contact` and
`user_press` queue the updates a real person's phone would produce.

The token used with it is a dummy, not a Telegram credential.
"""

from __future__ import annotations

import asyncio
import json
import time

from aiohttp import web

FAKE_TOKEN = "1234567890:ATLAS-LOCAL-FAKE-TOKEN-not-a-real-credential"
BOT_USER = {"id": 1234567890, "is_bot": True, "first_name": "Nomad Atlas (local test)",
            "username": "nomad_atlas_local_test_bot"}


class FakeTelegram:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host, self.port = host, port
        self.sent: list[dict] = []          # everything the bot sent, in order
        self._updates: list[dict] = []
        self._next_update = 1
        self._next_message = 100
        self._new = asyncio.Event()
        self._runner: web.AppRunner | None = None

    # --- server lifecycle ----------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def start(self) -> str:
        app = web.Application(client_max_size=32 * 1024 * 1024)
        app.router.add_route("*", "/bot{token}/{method}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        self.port = self._runner.addresses[0][1]
        return self.base_url

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # --- the Bot API ---------------------------------------------------------

    async def _handle(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        params: dict = {}
        files: dict = {}
        if request.can_read_body:
            for key, value in (await request.post()).items():
                if isinstance(value, web.FileField):
                    files[key] = {"filename": value.filename, "data": value.file.read()}
                else:
                    params[key] = value
        params.update(request.query)
        # aiogram uploads a file under a generated field name and points the
        # real parameter at it ("document": "attach://<name>")
        for key, value in list(params.items()):
            if isinstance(value, str) and value.startswith("attach://"):
                attached = files.pop(value[len("attach://"):], None)
                if attached is not None:
                    files[key] = attached

        if method == "getUpdates":
            return web.json_response({"ok": True, "result": await self._get_updates(params)})
        if method == "getMe":
            return web.json_response({"ok": True, "result": BOT_USER})

        result: object = True
        if method in ("sendMessage", "sendDocument", "editMessageReplyMarkup"):
            result = self._message(method, params, files)
        self.sent.append({"method": method, "params": params, "files": files,
                          "result": result})
        self._new.set()
        return web.json_response({"ok": True, "result": result})

    async def _get_updates(self, params: dict) -> list[dict]:
        offset = int(params.get("offset") or 0)
        self._updates = [u for u in self._updates if u["update_id"] >= offset]
        deadline = time.monotonic() + min(float(params.get("timeout") or 0), 1.0)
        while not self._updates and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        return list(self._updates)

    def _message(self, method: str, params: dict, files: dict) -> dict:
        if method == "editMessageReplyMarkup":
            message_id = int(params["message_id"])
        else:
            message_id = self._next_message
            self._next_message += 1
        msg = {"message_id": message_id, "date": int(time.time()),
               "chat": {"id": int(params["chat_id"]), "type": "private"},
               "from": BOT_USER}
        if method == "sendMessage":
            msg["text"] = params.get("text", "")
        elif method == "sendDocument":
            doc = files.get("document") or next(iter(files.values()), {"filename": "file"})
            msg["document"] = {"file_id": f"doc{message_id}", "file_unique_id": f"u{message_id}",
                               "file_name": doc["filename"]}
            if params.get("caption"):
                msg["caption"] = params["caption"]
        return msg

    # --- the scripted farmer -------------------------------------------------

    def _user(self, chat_id: int, language: str) -> dict:
        return {"id": chat_id, "is_bot": False, "first_name": "Farmer",
                "language_code": language}

    def _push(self, body: dict) -> None:
        body["update_id"] = self._next_update
        self._next_update += 1
        self._updates.append(body)

    def _incoming(self, chat_id: int, language: str, **content) -> dict:
        self._next_message += 1
        return {"message_id": self._next_message, "date": int(time.time()),
                "chat": {"id": chat_id, "type": "private"},
                "from": self._user(chat_id, language), **content}

    def user_text(self, chat_id: int, text: str, language: str = "uz") -> None:
        self._push({"message": self._incoming(chat_id, language, text=text)})

    def user_contact(self, chat_id: int, phone: str, language: str = "uz",
                     contact_user_id: int | None = None) -> None:
        """The farmer taps "share my phone number"."""
        contact = {"phone_number": phone, "first_name": "Farmer",
                   "user_id": chat_id if contact_user_id is None else contact_user_id}
        self._push({"message": self._incoming(chat_id, language, contact=contact)})

    def user_press(self, chat_id: int, button: dict, message: dict,
                   language: str = "uz") -> None:
        """The farmer taps an inline button under `message`."""
        self._push({"callback_query": {
            "id": f"cb{self._next_update}", "from": self._user(chat_id, language),
            "chat_instance": "local", "data": button["callback_data"],
            "message": message}})

    # --- reading what the bot did --------------------------------------------

    @staticmethod
    def buttons(item: dict) -> list[dict]:
        """Inline buttons attached to something the bot sent."""
        markup = item["params"].get("reply_markup")
        if not markup:
            return []
        rows = json.loads(markup).get("inline_keyboard") or []
        return [button for row in rows for button in row]

    @classmethod
    def has_button(cls, prefix: str):
        """Predicate for wait_for: the bot sent something with such a button."""
        return lambda item: any(b.get("callback_data", "").startswith(prefix)
                                for b in cls.buttons(item))

    async def wait_for(self, method: str, since: int = 0, timeout: float = 15.0,
                       where=None) -> dict:
        """Wait until the bot has made a matching call (after index `since`)."""
        deadline = time.monotonic() + timeout
        while True:
            for item in self.sent[since:]:
                if item["method"] == method and (where is None or where(item)):
                    return item
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                seen = [i["method"] for i in self.sent[since:]]
                raise TimeoutError(f"bot did not call {method} within {timeout}s; saw {seen}")
            self._new.clear()
            try:
                await asyncio.wait_for(self._new.wait(), min(remaining, 0.5))
            except asyncio.TimeoutError:
                pass
