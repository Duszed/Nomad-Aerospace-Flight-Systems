"""The laptop app's state: saved settings, real or demo data, background work.

Two data folders, never mixed:

    <data>/                 customer data: database, logs, reports, signing key
    <data>/demo-dashboard/  sample data only, created by "Load demo data"

While demo data is loaded the app works on the demo folder, never talks to
Telegram or the sync server, and every page says "Sample data - not a
customer record". "Clear demo data" deletes that folder and returns to the
customer data, which was never touched.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from ..config import Settings
from ..db import init_db
from ..ingest.sprayer import SprayerConfig
from ..records import Signer, ensure_key_registered

DEMO_DIR = "demo-dashboard"
MARKER = ".atlas-demo"

DEFAULTS: dict = {
    "bot_token": "",
    "server_url": "",
    "sync_token": "",
    "swath_m": 7.0,
    "sprayer": {"kind": "servo", "channel": 9, "pwm_on": 1150, "relay": 0,
                "flow_name": "FLOW", "flow_on_lpm": 0.2},
    "language": "uz",
    "drone_connection": "udpin:0.0.0.0:14550",
    "demo": False,
}


class Store:
    """Settings chosen on the Settings screen, kept in <data>/settings.json.

    The bot token and sync password are stored here unencrypted, on this
    laptop only (the data folder is never committed or uploaded).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.values: dict = json.loads(json.dumps(DEFAULTS))
        if self.path.is_file():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            for key, default in DEFAULTS.items():
                if key not in saved:
                    continue
                if isinstance(default, dict) and isinstance(saved[key], dict):
                    self.values[key].update(saved[key])
                else:
                    self.values[key] = saved[key]

    def __getitem__(self, key: str):
        return self.values[key]

    def update(self, **changes) -> None:
        self.values.update(changes)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def sprayer(self) -> SprayerConfig:
        try:
            return SprayerConfig.from_dict(self.values["sprayer"])
        except (TypeError, ValueError):
            return SprayerConfig()


class Runtime:
    """What the dashboard and the verify pages work on right now."""

    def __init__(self, root: Path, port: int = 8000, background: bool = True,
                 shutdown_token: str = "") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.port = port
        self.local_url = f"http://localhost:{port}"
        self.store = Store(self.root / "settings.json")
        self.background = background
        self.shutdown_token = shutdown_token
        self.on_shutdown = None          # set by the launcher
        self.flash: list[tuple[str, str]] = []
        self.settings: Settings = None   # set by reload()
        self.Session: sessionmaker = None
        self.signer: Signer = None
        self.demo = False
        from .capture import LiveCapture
        from .services import Services
        self.capture = LiveCapture()
        self.services = Services(self)
        self.reload()

    # --- which data are we on -------------------------------------------------

    @property
    def demo_dir(self) -> Path:
        return self.root / DEMO_DIR

    def reload(self) -> None:
        """(Re)open the data folder that the saved settings point at."""
        self.services.stop()
        self._dispose()
        env = Settings.from_env()
        self.demo = bool(self.store["demo"]) and (self.demo_dir / MARKER).is_file()
        if self.demo:
            # sample data never reaches Telegram or the sync server
            self.settings = Settings(data_dir=self.demo_dir, base_url=self.local_url,
                                     share_ttl_days=env.share_ttl_days)
        else:
            server = (self.store["server_url"] or env.server_url or "").rstrip("/") or None
            self.settings = Settings(
                data_dir=self.root, db_url=env.db_url,
                base_url=server or self.local_url,
                bot_token=self.store["bot_token"] or env.bot_token,
                telegram_api=env.telegram_api,
                share_ttl_days=env.share_ttl_days,
                sync_token=self.store["sync_token"] or env.sync_token,
                server_url=server)
        self.settings.ensure_dirs()
        self.Session = init_db(self.settings.database_url)
        self.signer = Signer.load_or_create(self.settings.key_path)
        with self.Session() as session:
            ensure_key_registered(session, self.signer)
            session.commit()
        if self.background:
            self.services.start()

    def _dispose(self) -> None:
        if self.Session is not None:
            self.Session.kw["bind"].dispose()   # release the SQLite file (Windows)
            self.Session = None

    @property
    def server_mode(self) -> bool:
        """True when the Atlas server, not this laptop, talks to Telegram."""
        return bool(self.settings.server_url and self.settings.sync_token)

    # --- demo data --------------------------------------------------------------

    def load_demo(self) -> None:
        from .demo_data import build_demo
        self.capture.cancel()
        self.services.stop()
        if self.demo:
            self._dispose()
        build_demo(self.demo_dir, self.local_url)
        self.store.update(demo=True)
        self.reload()

    def clear_demo(self) -> None:
        self.capture.cancel()
        self.services.stop()
        if self.demo:
            self._dispose()
        self.store.update(demo=False)
        if self.demo_dir.exists():
            if not (self.demo_dir / MARKER).is_file():
                raise RuntimeError(f"{self.demo_dir} is not a demo folder; not deleting it")
            shutil.rmtree(self.demo_dir)
        self.reload()

    def close(self) -> None:
        self.capture.cancel()
        self.services.stop()
        self._dispose()
