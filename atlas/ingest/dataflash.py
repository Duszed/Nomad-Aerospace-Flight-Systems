"""Ingest path (a): ArduPilot DataFlash .bin log, read with pymavlink DFReader.

Messages used
-------------
GPS   TimeUS, Status, Lat, Lng, Alt, Spd      position, ground speed, UTC
POS   RelHomeAlt                              height above home (preferred)
RCOU  C1..C14                                 servo outputs (pump PWM)
RELY  State                                   relay bitmask
<flow message>.<flow field>                   optional flow meter, L/min
BAT   Volt                                    minimum pack voltage
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pymavlink import DFReader

from .._telemetry import coverage_logger
from .sprayer import SprayerConfig
from .track import IngestError, Sample, Track, sha256_file

GPS_FIX_3D = 3
_MIN_VALID_EPOCH = 946684800.0  # 2000-01-01: below this the log clock is not UTC


def track_from_dataflash(path: Path, sprayer: SprayerConfig | None = None) -> Track:
    path = Path(path)
    sprayer = sprayer or SprayerConfig()

    wanted = {"GPS", "POS", "BAT"}
    if sprayer.kind == "servo":
        wanted.add("RCOU")
    elif sprayer.kind == "relay":
        wanted.add("RELY")
    wanted.add(sprayer.flow_name)  # read a flow meter whenever one is logged

    try:
        mlog = DFReader.DFReader_binary(str(path))
    except Exception as exc:  # noqa: BLE001 - pymavlink raises bare exceptions
        raise IngestError(f"{path.name}: not a readable DataFlash log ({exc})") from exc

    samples: list[Sample] = []
    spray_on = False
    saw_signal = False
    flow: float | None = None
    saw_flow = False
    rel_alt: float | None = None
    first_gps_alt: float | None = None
    last_pos: tuple[float, float] | None = None
    rejected = 0
    min_batt: float | None = None
    first_utc = last_utc = None

    try:
        while True:
            m = mlog.recv_match(type=list(wanted))
            if m is None:
                break
            kind = m.get_type()

            if kind == "RCOU" and sprayer.kind == "servo":
                pwm = getattr(m, f"C{sprayer.channel}", None)
                if pwm is not None:
                    saw_signal = True
                    spray_on = pwm >= sprayer.pwm_on

            elif kind == "RELY" and sprayer.kind == "relay":
                saw_signal = True
                spray_on = bool((int(m.State) >> sprayer.relay) & 1)

            elif kind == sprayer.flow_name:
                value = getattr(m, sprayer.flow_field, None)
                if value is not None:
                    flow = float(value)
                    saw_flow = True
                    if sprayer.kind == "flow":
                        saw_signal = True
                        spray_on = flow >= sprayer.flow_on_lpm

            elif kind == "POS":
                rel_alt = float(m.RelHomeAlt)

            elif kind == "BAT":
                if getattr(m, "Inst", getattr(m, "Instance", 0)) == 0:
                    v = float(m.Volt)
                    if v > 0 and (min_batt is None or v < min_batt):
                        min_batt = v

            elif kind == "GPS":
                if getattr(m, "I", 0) != 0 or m.Status < GPS_FIX_3D:
                    continue
                lat, lon = float(m.Lat), float(m.Lng)
                if lat == 0.0 and lon == 0.0:
                    continue
                if last_pos is not None:
                    step = coverage_logger.haversine_m(last_pos[0], last_pos[1], lat, lon)
                    if step > coverage_logger.MAX_PLAUSIBLE_STEP_M:
                        rejected += 1
                        last_pos = (lat, lon)
                        continue
                last_pos = (lat, lon)

                if first_gps_alt is None:
                    first_gps_alt = float(m.Alt)
                height = rel_alt if rel_alt is not None else float(m.Alt) - first_gps_alt
                speed = float(m.Spd)
                on = (speed >= coverage_logger.SPRAY_MIN_SPEED_MS
                      if sprayer.kind == "speed" else spray_on)
                samples.append(Sample(
                    t=m.TimeUS / 1e6, lat=lat, lon=lon, height_m=round(height, 2),
                    speed_ms=speed, spray_on=on, flow_lpm=flow))

                stamp = getattr(m, "_timestamp", 0.0) or 0.0
                if stamp > _MIN_VALID_EPOCH:
                    if first_utc is None:
                        first_utc = stamp
                    last_utc = stamp
    finally:
        # release the mmap + handle (Windows will not delete an open file)
        for attr in ("data_map", "filehandle"):
            obj = getattr(mlog, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:  # noqa: BLE001
                    pass

    if len(samples) < 2:
        raise IngestError(f"{path.name}: fewer than two GPS fixes with a 3D lock")
    if sprayer.kind != "speed" and not saw_signal:
        raise IngestError(
            f"{path.name}: no sprayer signal found for source '{sprayer.describe()}'. "
            "Check the sprayer source configuration (or use kind 'speed' to infer "
            "spraying from ground speed).")

    def utc(stamp):
        return datetime.fromtimestamp(stamp, tz=timezone.utc) if stamp else None

    return Track(
        samples=samples, source="dataflash", sprayer=sprayer,
        log_name=path.name, log_sha256=sha256_file(path), has_flow=saw_flow,
        started_utc=utc(first_utc), ended_utc=utc(last_utc),
        rejected_fixes=rejected, min_battery_v=min_batt,
    )
