"""Start and stop the laptop app (what "Start Atlas" / "Stop Atlas" run).

Messages are for someone who is not a developer: plain sentences on the
screen, the technical detail in <data>/atlas.log.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import threading
import time
import webbrowser
from pathlib import Path

import httpx

RUN_FILE = "atlas-running.json"
LOG_FILE = "atlas.log"


def _port_in_use(port: int) -> bool:
    """Is something answering on this port?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _port_taken(port: int) -> bool:
    """Would Atlas fail to open this port? (asked before starting)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _is_atlas(port: int) -> bool:
    try:
        body = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2.0).json()
    except (httpx.HTTPError, ValueError):
        return False
    return isinstance(body, dict) and body.get("service") == "nomad-atlas"


def _open_when_ready(url: str, port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _port_in_use(port):
            webbrowser.open(url)
            return
        time.sleep(0.2)


def _say(message: str = "") -> None:
    print(message, flush=True)


def start(data_dir: Path, port: int = 8000, open_browser: bool = True, say=_say) -> int:
    data_dir = Path(data_dir)
    url = f"http://localhost:{port}"
    if _is_atlas(port):
        say("Atlas is already running. Opening it in your browser.")
        if open_browser:
            webbrowser.open(url)
        return 0
    if _port_taken(port):
        say(f"Atlas cannot start: another program on this computer is using port {port}.")
        say("Close that program (or restart the computer) and start Atlas again.")
        return 1

    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=data_dir / LOG_FILE, level=logging.WARNING, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        import uvicorn

        from ..web import create_app
        from .runtime import Runtime

        token = secrets.token_urlsafe(24)
        runtime = Runtime(data_dir, port=port, shutdown_token=token)
        server = uvicorn.Server(uvicorn.Config(
            create_app(runtime=runtime), host="127.0.0.1", port=port, log_config=None,
            access_log=False))
        runtime.on_shutdown = lambda: setattr(server, "should_exit", True)
    except Exception as exc:  # noqa: BLE001 - explain, do not dump a stack trace
        logging.exception("Atlas could not start")
        say("Atlas could not start.")
        say(f"Reason: {exc}")
        say(f"The technical details were saved to {data_dir / LOG_FILE}")
        return 1

    run_file = data_dir / RUN_FILE
    run_file.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": token}),
                        encoding="utf-8")
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url, port), daemon=True).start()
    say("Atlas is running.")
    say(f"  Dashboard:  {url}")
    say("  Keep this window open while you work.")
    say('  To stop: double-click "Stop Atlas", or just close this window.')
    failed = False
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except SystemExit:               # uvicorn could not open the port
        failed = True
    finally:
        runtime.close()
        run_file.unlink(missing_ok=True)
    if failed:
        say(f"Atlas could not start: port {port} is not available on this computer.")
        say("Restart the computer and start Atlas again.")
        return 1
    say("Atlas has stopped. Your data is saved.")
    return 0


def stop(data_dir: Path, say=_say) -> int:
    run_file = Path(data_dir) / RUN_FILE
    try:
        info = json.loads(run_file.read_text(encoding="utf-8"))
        port, token = int(info["port"]), str(info["token"])
    except (OSError, ValueError, KeyError):
        say("Atlas is not running.")
        return 0
    if not _is_atlas(port):
        run_file.unlink(missing_ok=True)
        say("Atlas is not running.")
        return 0
    try:
        httpx.post(f"http://127.0.0.1:{port}/api/shutdown", timeout=5.0,
                   headers={"X-Atlas-Token": token})
    except httpx.HTTPError:
        pass
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not _port_in_use(port):
            say("Atlas has stopped. Your data is saved.")
            return 0
        time.sleep(0.3)
    say("Atlas did not stop by itself. Close the Atlas window to stop it.")
    return 1
