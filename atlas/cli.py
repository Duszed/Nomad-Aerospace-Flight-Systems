"""Command line: `python -m atlas <command>`.

  demo      run the whole pipeline with no drone and no internet
  ingest    log file (.bin / .ndjson, or - for a live gateway stream) -> signed record + PDF
  serve     run the verify server
  bot       run the Telegram bot
  sync      push records created offline to the Atlas server
  verify    check a stored record's hash and signature
  amend     correct job details by creating a new signed version
  keys      show this installation's public key / trust a laptop's key
  sample-log  write a sample DataFlash .bin (simulated flight) to try `ingest` with
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from . import __version__
from .config import Settings
from .db import RecordVersion, SigningKey, init_db
from .ingest import IngestError, capture_stream, load_boundary
from .pipeline import process_log
from .records import (
    AMENDABLE, JobIn, Signer, amend_record, ensure_key_registered, verify_record,
)
from .shares import owner_share, report_filename, report_pdf, share_url


def _settings(args) -> Settings:
    """Settings from the environment; --data-dir overrides ATLAS_DATA_DIR."""
    data_dir = getattr(args, "data_dir", None)
    return Settings.from_env(data_dir=Path(data_dir)) if data_dir else Settings.from_env()


def _open(settings: Settings):
    settings.ensure_dirs()
    return init_db(settings.database_url)


def cmd_demo(args) -> int:
    from .demo import free_port, run_demo
    port = args.port or 8000
    result = run_demo(Path(args.data_dir), speedup=args.speedup, port=port,
                      udp_port=args.udp_port or free_port(2), serve=args.serve,
                      live=args.live, phone=args.phone)
    return 0 if result.ok else 1


def cmd_ingest(args) -> int:
    settings = _settings(args)
    Session = _open(settings)
    signer = Signer.load_or_create(settings.key_path)
    job = JobIn.model_validate(json.loads(Path(args.job).read_text(encoding="utf-8")))
    boundary = load_boundary(Path(args.boundary))

    log_path = Path(args.log)
    if args.log == "-":  # live: gateway | atlas ingest -
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = settings.logs_dir / f"live-{stamp}.ndjson"
        print(f"capturing the gateway stream to {log_path} (end it with Ctrl+C in the gateway)")
        rows = capture_stream(sys.stdin, log_path)
        print(f"captured {rows} rows")

    with Session() as session:
        done = process_log(session, settings, signer, log_path, job, boundary)
        owner = owner_share(session, done.record)
        session.commit()
        res = done.coverage.numbers()
        print(f"record     {done.record.id}  (version 1, key {done.record.key_id})")
        print(f"sha256     {done.record.payload_sha256}")
        print(f"field      {res['field_area_ha']:.2f} ha   coverage {res['coverage_pct']:.1f}%")
        print(f"missed     {res['missed_area_ha']:.3f} ha in {res['missed_strips']} strip(s)   "
              f"double-sprayed {res['overlap_area_ha']:.3f} ha")
        print(f"applied    {res['litres_applied']:.1f} L ({res['litres_basis']})   "
              f"{res['actual_rate_l_ha']:.1f} L/ha vs {res['planned_rate_l_ha']:.1f} planned")
        langs = ("uz", "ru", "en") if args.lang == "all" else (args.lang or job.farmer.language,)
        for lang in langs:
            path = settings.reports_dir / report_filename(done.record, lang)
            path.write_bytes(report_pdf(settings, done.record, owner, lang))
            print(f"pdf        {path}")
        print(f"verify     {share_url(settings, owner)}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    from .web import create_app
    settings = _settings(args)
    uvicorn.run(create_app(settings, _open(settings)), host=args.host, port=args.port)
    return 0


def cmd_bot(args) -> int:
    from .bot import run_bot
    settings = _settings(args)
    if not settings.bot_token:
        print("ATLAS_BOT_TOKEN is not set (see .env.example)", file=sys.stderr)
        return 2
    try:
        asyncio.run(run_bot(settings, _open(settings)))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_sync(args) -> int:
    from .sync import push_pending
    settings = _settings(args)
    sent, pending = push_pending(_open(settings), settings)
    print(f"sent {sent} record(s); {pending} still waiting")
    return 0


def cmd_verify(args) -> int:
    settings = _settings(args)
    with _open(settings)() as session:
        record = session.get(RecordVersion, args.record_id)
        if record is None:
            print("no such record", file=sys.stderr)
            return 2
        check = verify_record(session, record)
        print(check.status.upper())
        for problem in check.problems:
            print(f"  - {problem}")
        return 0 if check.ok else 1


def cmd_amend(args) -> int:
    settings = _settings(args)
    changes = dict(item.split("=", 1) for item in args.set)
    signer = Signer.load_or_create(settings.key_path)
    with _open(settings)() as session:
        record = session.get(RecordVersion, args.record_id)
        if record is None:
            print("no such record", file=sys.stderr)
            return 2
        new = amend_record(session, signer, record, changes, args.reason)
        session.commit()
        print(f"version {new.version} created: record {new.id} (supersedes {record.id})")
    return 0


def cmd_keys(args) -> int:
    settings = _settings(args)
    Session = _open(settings)
    if args.action == "show":
        signer = Signer.load_or_create(settings.key_path)
        with Session() as session:
            ensure_key_registered(session, signer)
            session.commit()
        print(f"key_id      {signer.key_id}")
        print(f"public_key  {signer.public_b64}")
        print(f"\nOn the server:  python -m atlas keys trust {signer.key_id} {signer.public_b64}")
    elif args.action == "trust":
        from .sync import trust_key
        if not (args.key_id and args.public_key):
            print("usage: keys trust KEY_ID PUBLIC_KEY [--label TEXT]", file=sys.stderr)
            return 2
        with Session() as session:
            trust_key(session, args.key_id, args.public_key, args.label)
            session.commit()
        print(f"trusted {args.key_id}")
    else:
        with Session() as session:
            for key in session.scalars(select(SigningKey)):
                print(f"{key.key_id}  {key.label}")
    return 0


def cmd_sample_log(args) -> int:
    import simulate_telemetry as sim
    from .ingest.dataflash_writer import write_dataflash
    samples = sim.mission_samples(faults=None if args.perfect else sim.DEFAULT_FAULTS)
    path = write_dataflash(samples, Path(args.out), flow_meter=args.flow_meter)
    print(f"wrote {path} ({path.stat().st_size // 1024} kB) - sample data, not a customer record")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles: never crash on a character
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(prog="atlas", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"Nomad Atlas {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", help="data folder (default: ATLAS_DATA_DIR or ./data)")

    p = sub.add_parser("demo", help="full pipeline, no drone needed")
    p.add_argument("--data-dir", default="data/demo")
    p.add_argument("--speedup", type=float, default=60.0, help="simulator speed, x real time")
    p.add_argument("--port", type=int, default=8000, help="verify server port")
    p.add_argument("--udp-port", type=int, default=0, help="simulator -> gateway UDP port")
    p.add_argument("--serve", action="store_true", help="keep the verify server running")
    p.add_argument("--live", action="store_true",
                   help="use real Telegram (ATLAS_BOT_TOKEN) and wait for you to act")
    p.add_argument("--phone", help="with --live: your phone number, as the sample farmer's")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("ingest", parents=[common], help="log -> signed record + PDF")
    p.add_argument("log", help="DataFlash .bin, gateway .ndjson, or - for stdin")
    p.add_argument("--job", required=True, help="job details JSON (see examples/job.json)")
    p.add_argument("--boundary", required=True, help="field boundary: GeoJSON or QGC .plan")
    p.add_argument("--lang", choices=["uz", "ru", "en", "all"], help="PDF language")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("serve", parents=[common], help="verify server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    sub.add_parser("bot", parents=[common], help="Telegram bot").set_defaults(func=cmd_bot)
    sub.add_parser("sync", parents=[common],
                   help="push offline records to the server").set_defaults(func=cmd_sync)

    p = sub.add_parser("verify", parents=[common], help="check a record")
    p.add_argument("record_id")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("amend", parents=[common], help="new signed version with corrected job details")
    p.add_argument("record_id")
    p.add_argument("--set", action="append", required=True, metavar="FIELD=VALUE",
                   help=f"one of: {', '.join(AMENDABLE)}")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_amend)

    p = sub.add_parser("keys", parents=[common], help="signing keys")
    p.add_argument("action", choices=["show", "trust", "list"])
    p.add_argument("key_id", nargs="?")
    p.add_argument("public_key", nargs="?")
    p.add_argument("--label", default="")
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser("sample-log", help="write a sample DataFlash .bin")
    p.add_argument("out", help="output path, e.g. data/sample-flight.bin")
    p.add_argument("--perfect", action="store_true", help="no missed or double-sprayed areas")
    p.add_argument("--flow-meter", action="store_true", help="include a FLOW message (L/min)")
    p.set_defaults(func=cmd_sample_log)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (IngestError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
