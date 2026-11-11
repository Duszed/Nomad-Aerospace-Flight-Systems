"""Run the existing simulator -> gateway chain and capture its NDJSON stream.

No aircraft is involved: telemetry/simulate_telemetry.py emits MAVLink over
local UDP, telemetry/mavlink_ground_gateway.py turns it into NDJSON exactly
as it would for a real Cube, and Atlas saves that stream to a file.

Everything captured this way is SAMPLE DATA, not a customer record.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from ._telemetry import TELEMETRY_DIR
from .ingest.ndjson import capture_stream


def capture_simulated_flight(out_path: Path, speedup: float = 40.0, port: int = 14601,
                             perfect: bool = False, flow_meter: bool = False,
                             timeout_s: float = 600.0) -> int:
    """Fly the demo mission through the real gateway. Returns NDJSON rows captured."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gw_log = open(out_path.with_suffix(".gateway.log"), "w", encoding="utf-8")

    gateway = subprocess.Popen(
        [sys.executable, str(TELEMETRY_DIR / "mavlink_ground_gateway.py"),
         "--connection", f"udpin:127.0.0.1:{port}"],
        stdout=subprocess.PIPE, stderr=gw_log, text=True, encoding="utf-8",
        cwd=str(TELEMETRY_DIR))
    rows = {"n": 0}

    def pump() -> None:
        rows["n"] = capture_stream(gateway.stdout, out_path)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        time.sleep(1.0)  # let the gateway bind its UDP port
        if gateway.poll() is not None:
            raise RuntimeError(
                f"gateway exited early (is UDP port {port} in use?) - see {gw_log.name}")
        sim_cmd = [sys.executable, str(TELEMETRY_DIR / "simulate_telemetry.py"),
                   "--mission", "--speedup", str(speedup),
                   "--connection", f"udpout:127.0.0.1:{port}"]
        if perfect:
            sim_cmd.append("--perfect")
        if flow_meter:
            sim_cmd.append("--flow-meter")
        subprocess.run(sim_cmd, check=True, timeout=timeout_s, cwd=str(TELEMETRY_DIR),
                       stdout=subprocess.DEVNULL)
        time.sleep(1.0)  # let the gateway drain what is still in the socket
    finally:
        gateway.terminate()  # only ever the gateway process started above
        try:
            gateway.wait(timeout=10)
        except subprocess.TimeoutExpired:
            gateway.kill()
        reader.join(timeout=10)
        gw_log.close()
    return rows["n"]
