"""Write a minimal, valid ArduPilot DataFlash .bin from simulated samples.

Development / demo tool: it lets the .bin ingest path be exercised with no
aircraft. Files it writes are SAMPLE DATA, not a customer record.

Only the messages Atlas reads are written (FMT, GPS, POS, RCOU, BAT and an
optional FLOW), using ArduCopter 4.5 field layouts.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path

HEAD = b"\xa3\x95"
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
GPS_UTC_LEAP_S = 18

# name -> (type id, DataFlash format, labels, struct format)
_MSGS = {
    "GPS": (130, "QBBIHBcLLeffffB",
            "TimeUS,I,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt,Spd,GCrs,VZ,Yaw,U",
            "<QBBIHBhiiiffffB"),
    "POS": (131, "QLLfff", "TimeUS,Lat,Lng,Alt,RelHomeAlt,RelOriginAlt", "<Qiifff"),
    "RCOU": (132, "QHHHHHHHHHHHHHH",
             "TimeUS,C1,C2,C3,C4,C5,C6,C7,C8,C9,C10,C11,C12,C13,C14", "<Q14H"),
    "BAT": (133, "QBfffffcf",
            "TimeUS,Inst,Volt,VoltR,Curr,CurrTot,EnrgTot,Temp,Res", "<QBfffffhf"),
    "FLOW": (134, "Qf", "TimeUS,Rate", "<Qf"),
}
_FMT_TYPE = 128
_FMT_STRUCT = "<BB4s16s64s"


def _fmt_record(type_id: int, length: int, name: str, fmt: str, labels: str) -> bytes:
    return HEAD + bytes([_FMT_TYPE]) + struct.pack(
        _FMT_STRUCT, type_id, length, name.encode(), fmt.encode(), labels.encode())


def write_dataflash(samples: list[dict], path: Path,
                    start_utc: datetime | None = None,
                    pump_channel: int = 9, flow_meter: bool = False) -> Path:
    """Write `samples` (from simulate_telemetry.mission_samples) as a .bin log."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    start_utc = start_utc or datetime(2026, 5, 14, 5, 30, tzinfo=timezone.utc)
    gps_start_s = (start_utc - GPS_EPOCH).total_seconds() + GPS_UTC_LEAP_S
    boot_offset_us = 60_000_000  # log starts a minute after boot

    out = bytearray()
    out += _fmt_record(_FMT_TYPE, 89, "FMT", "BBnNZ", "Type,Length,Name,Format,Columns")
    names = ["GPS", "POS", "RCOU", "BAT"] + (["FLOW"] if flow_meter else [])
    for name in names:
        type_id, fmt, labels, st = _MSGS[name]
        out += _fmt_record(type_id, 3 + struct.calcsize(st), name, fmt, labels)

    def rec(name: str, *values) -> None:
        type_id, _, _, st = _MSGS[name]
        out.extend(HEAD + bytes([type_id]) + struct.pack(st, *values))

    for s in samples:
        t_us = boot_offset_us + int(round(s["t"] * 1e6))
        gps_s = gps_start_s + s["t"]
        week = int(gps_s // 604800)
        ms = int(round((gps_s - week * 604800) * 1000))
        lat = int(round(s["lat"] * 1e7))
        lon = int(round(s["lon"] * 1e7))

        servos = [1500] * 14
        servos[pump_channel - 1] = int(s["pump_pwm"])
        rec("RCOU", t_us, *servos)
        if flow_meter:
            rec("FLOW", t_us, float(s["flow_lpm"]))
        rec("POS", t_us, lat, lon, 280.0 + s["alt_rel_m"], s["alt_rel_m"], s["alt_rel_m"])
        rec("BAT", t_us, 0, s["battery_v"], s["battery_v"], 36.0, 0.0, 0.0, 2500, 0.0)
        rec("GPS", t_us, 0, 6, ms, week, 18, 70, lat, lon,
            int(round((280.0 + s["alt_rel_m"]) * 100)), s["groundspeed_ms"],
            s["heading_deg"], 0.0, s["heading_deg"], 1)

    path.write_bytes(bytes(out))
    return path
