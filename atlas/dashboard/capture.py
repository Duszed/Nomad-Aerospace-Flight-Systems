"""Receive live from the drone, through the existing gateway.

Starts `telemetry/mavlink_ground_gateway.py` as a child process and writes
its NDJSON stream to a file. The gateway is observe-only: it reads
telemetry and never commands the aircraft. Atlas adds nothing to that - it
only reads the gateway's output.

"Simulated flight" additionally runs `simulate_telemetry.py --mission`
against a local UDP port, for trying the screen with no drone. Its output
is sample data.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from .._telemetry import TELEMETRY_DIR

IDLE, WAITING, RECEIVING, ERROR = "idle", "waiting", "receiving", "error"


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveCapture:
    """One capture at a time, tied to one draft job."""

    def __init__(self) -> None:
        self.draft_id: str | None = None
        self.out_path: Path | None = None
        self.simulated = False
        self.rows = 0
        self.fixes = 0
        self.started = 0.0
        self.error = ""
        self._gateway: subprocess.Popen | None = None
        self._sim: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._log = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._gateway is not None

    def start(self, draft_id: str, out_path: Path, connection: str,
              simulate: bool = False, speedup: float = 40.0,
              sim_args: list[str] | None = None) -> None:
        with self._lock:
            self._stop_processes()
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            self.draft_id, self.out_path, self.simulated = draft_id, out_path, simulate
            self.rows = self.fixes = 0
            self.error = ""
            self.started = time.monotonic()
            port = None
            if simulate:
                port = _free_udp_port()
                connection = f"udpin:127.0.0.1:{port}"
            self._log = open(out_path.with_suffix(".gateway.log"), "w", encoding="utf-8")
            self._gateway = subprocess.Popen(
                [sys.executable, "-u", str(TELEMETRY_DIR / "mavlink_ground_gateway.py"),
                 "--connection", connection],
                stdout=subprocess.PIPE, stderr=self._log, text=True, encoding="utf-8",
                errors="replace", cwd=str(TELEMETRY_DIR))
            self._reader = threading.Thread(target=self._pump, args=(self._gateway, out_path),
                                            daemon=True)
            self._reader.start()
            if simulate:
                time.sleep(1.0)  # let the gateway bind its port
                self._sim = subprocess.Popen(
                    [sys.executable, str(TELEMETRY_DIR / "simulate_telemetry.py"), "--mission",
                     "--speedup", str(speedup), "--connection", f"udpout:127.0.0.1:{port}",
                     *(sim_args or [])],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd=str(TELEMETRY_DIR))

    def _pump(self, gateway: subprocess.Popen, out_path: Path) -> None:
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            for line in gateway.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                fh.write(line + "\n")
                fh.flush()
                self.rows += 1
                if '"GLOBAL_POSITION_INT"' in line:
                    self.fixes += 1

    def status(self) -> dict:
        gw = self._gateway
        state = IDLE
        if gw is not None:
            if gw.poll() is not None and self.fixes == 0:
                state = ERROR
                self.error = self._last_log_line() or "the gateway stopped"
            else:
                state = RECEIVING if self.fixes else WAITING
        sim_done = self._sim is not None and self._sim.poll() is not None
        return {"state": state, "rows": self.rows, "fixes": self.fixes,
                "seconds": int(time.monotonic() - self.started) if gw else 0,
                "simulated": self.simulated, "sim_done": sim_done, "error": self.error,
                "draft_id": self.draft_id}

    def _last_log_line(self) -> str:
        try:
            self._log.flush()
            lines = Path(self._log.name).read_text(encoding="utf-8").strip().splitlines()
            return lines[-1][:200] if lines else ""
        except (OSError, AttributeError, ValueError):
            return ""

    def _stop_processes(self) -> None:
        """Stop only the child processes this object started."""
        for proc in (self._sim, self._gateway):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self._reader is not None:
            self._reader.join(timeout=10)
        if self._log is not None:
            self._log.close()
        self._gateway = self._sim = self._reader = self._log = None

    def finish(self) -> tuple[Path | None, int]:
        """Stop listening. Returns (captured file, position fixes)."""
        with self._lock:
            if self._sim is not None and self._sim.poll() is not None:
                time.sleep(1.0)  # let the gateway drain what is still in the socket
            self._stop_processes()
            path, fixes = self.out_path, self.fixes
            self.draft_id = self.out_path = None
            return path, fixes

    def cancel(self) -> None:
        with self._lock:
            self._stop_processes()
            self.draft_id = self.out_path = None
            self.rows = self.fixes = 0
