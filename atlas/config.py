"""Settings, read from environment variables (see .env.example).

Data stays where ATLAS_DATA_DIR points: the laptop in the field, and the
Atlas server in Uzbekistan. Atlas talks to no other service except the
Telegram Bot API, and only to deliver a farmer's own reports to that farmer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines from .env into the environment (real env vars win)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    db_url: str = ""                       # default: sqlite file in data_dir
    base_url: str = "http://127.0.0.1:8000"  # public address of the verify server
    bot_token: str | None = None
    telegram_api: str | None = None        # custom Bot API base URL (tests / demo)
    share_ttl_days: int = 30
    sync_token: str | None = None          # shared secret laptop -> server
    server_url: str | None = None          # where the laptop syncs to

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        load_dotenv()
        env = os.environ
        s = cls(
            data_dir=Path(env.get("ATLAS_DATA_DIR", "data")),
            db_url=env.get("ATLAS_DB_URL", ""),
            base_url=env.get("ATLAS_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            bot_token=env.get("ATLAS_BOT_TOKEN") or None,
            telegram_api=env.get("ATLAS_TELEGRAM_API") or None,
            share_ttl_days=int(env.get("ATLAS_SHARE_TTL_DAYS", "30")),
            sync_token=env.get("ATLAS_SYNC_TOKEN") or None,
            server_url=(env.get("ATLAS_SERVER_URL") or "").rstrip("/") or None,
        )
        return replace(s, **overrides) if overrides else s

    @property
    def database_url(self) -> str:
        if self.db_url:
            return self.db_url
        return f"sqlite:///{(self.data_dir / 'atlas.db').as_posix()}"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def key_path(self) -> Path:
        return self.data_dir / "keys" / "atlas_ed25519.pem"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.logs_dir, self.reports_dir, self.key_path.parent):
            d.mkdir(parents=True, exist_ok=True)
