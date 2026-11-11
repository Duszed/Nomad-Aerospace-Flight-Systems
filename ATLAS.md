# Nomad Atlas — spray-record platform (prototype)

After a drone sprays a field, Atlas takes the flight data, works out what was
actually sprayed, creates a signed record, sends the report to the farmer on
Telegram, and lets the farmer share it with a bank, insurer, export buyer or
cluster, who can verify it from a QR code.

```
RECEIVE ─► COMPUTE ─► RECORD ─► REPORT ─► SEND ─► SHARE ─► VERIFY
.bin /     shapely    SQLite    one-page   Telegram  expiring,  FastAPI page:
NDJSON     + pyproj   Ed25519   PDF + QR   bot       revocable  Verified / Modified / Revoked
└────────── offline, on the field laptop ─────┘ └──── needs internet: Atlas server ────┘
```

## Setup (under 5 minutes)

Python 3.11 or newer. No drone, Docker, Telegram account or internet needed
after the install.

```bash
git clone https://github.com/Duszed/Nomad-Aerospace-Flight-Systems.git
cd Nomad-Aerospace-Flight-Systems
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python -m atlas demo
```

## The one-command demo

```bash
python -m atlas demo
```

It runs the whole pipeline in about 30 seconds and ends with
`RESULT: ALL CHECKS PASSED`:

| Step | What happens |
|---|---|
| 1 Receive | `simulate_telemetry.py --mission` flies the demo field (4.2 ha, 7 m swath, 20 passes) and sends MAVLink over local UDP to the real `mavlink_ground_gateway.py`; Atlas captures its NDJSON |
| 2 Compute | Coverage, missed strips, double-sprayed area, litres and L/ha in the field's UTM zone |
| 3 Record | Stored in SQLite with the SHA-256 of the input log; canonical JSON signed with Ed25519 |
| 4 Report | One-page PDF with map and QR code, in Uzbek, Russian and English → `data/demo/reports/` |
| 5 Send | The aiogram bot registers the farmer by phone, sends the PDF and summary; farmer taps Confirm |
| 6 Share | Farmer taps Share → Bank and gets an expiring link |
| 7 Verify | The link shows **Verified**; one byte of the record is changed → **Modified**; the share is revoked → **Revoked** |

The simulated flight is deliberately imperfect (one pump dropout, one pass
flown 2.5 m off-line) so the record has missed and double-sprayed areas to
show: about 97.2 % coverage.

To look at the result in a browser, keep the server running:

```bash
python -m atlas demo --serve
```

and open the two links it prints. Everything the demo creates is labelled
**Sample data – not a customer record** and lives in `data/demo/`.

### Telegram in the demo

By default the demo needs no Telegram account: the real bot code talks to a
local stand-in for the Bot API (`atlas/bot/fake_telegram.py`) and a scripted
farmer taps the buttons. To use real Telegram instead, create a bot with
@BotFather and play the farmer yourself:

```bash
set ATLAS_BOT_TOKEN=123456:your-token        # Linux/macOS: export ATLAS_BOT_TOKEN=...
python -m atlas demo --live --phone "+998 90 123 45 67" --serve
```

`--phone` must be the number of the Telegram account you will use. The demo
waits while you press Start, share your number, tap Confirm, Share → Bank,
and later revoke the share with `/shares`. Share links point at
`127.0.0.1`, so open them on the same computer. (This live mode has not been
run against real Telegram yet; the bot logic itself is covered by the tests.)

## Tests

```bash
pytest
```

Runs the original 50 telemetry tests plus the Atlas tests from the repo
root. The original command (`cd telemetry && pytest`) still works.

## Using it on a real job

Steps 1–4 work offline on the field laptop.

```bash
# from an ArduPilot DataFlash log
python -m atlas ingest flight.bin --job job.json --boundary field.geojson

# or live, through the gateway (stop the gateway with Ctrl+C when the job ends)
python telemetry/mavlink_ground_gateway.py | python -m atlas ingest - --job job.json --boundary field.geojson
```

* `job.json` — farmer, field, crop, product, batch, planned dose, pilot,
  drone ID, weather. See [`examples/job.json`](examples/job.json).
* `field.geojson` — the field boundary as a GeoJSON polygon, or a
  QGroundControl `.plan` (geofence or survey polygon). See
  [`examples/field.geojson`](examples/field.geojson).

To try it without a real log: `python -m atlas sample-log data/sample-flight.bin`
writes a simulated one, then ingest it with the two files in `examples/`.

The command prints the numbers, writes the PDF to `data/reports/` and prints
the verify link. Other commands:

```bash
python -m atlas verify RECORD_ID                       # recompute hash, check signature
python -m atlas amend RECORD_ID --set batch=B-17 --reason "Batch mistyped"   # new signed version
python -m atlas serve                                   # verify server on :8000
python -m atlas bot                                     # Telegram bot (needs ATLAS_BOT_TOKEN)
python -m atlas sync                                    # push offline records to the server
python -m atlas keys show                               # this laptop's public signing key
```

Settings are environment variables (or a `.env` file): see
[`.env.example`](.env.example).

### Where the sprayer on/off signal comes from

Set `"sprayer"` in `job.json`. The K30 default is the pump on SERVO9.

| `kind` | DataFlash `.bin` | Live MAVLink (gateway NDJSON) |
|---|---|---|
| `servo` (default) | `RCOU.C<channel>` ≥ `pwm_on` | `SERVO_OUTPUT_RAW` |
| `relay` | `RELY.State` bit `relay` | `RELAY_STATUS` |
| `flow` | `<flow_name>.<flow_field>` ≥ `flow_on_lpm` | `NAMED_VALUE_FLOAT <flow_name>` |
| `speed` | ground speed ≥ 1 m/s | ground speed ≥ 1 m/s |

If the chosen signal is not in the log, Atlas refuses to make a record
rather than guess. `speed` is an explicit, weaker fallback.

### How litres are labelled

| Source | Label on the report |
|---|---|
| Flow meter in the log | measured by flow meter |
| `"tank_litres_used"` in `job.json` | operator-reported |
| Neither | **estimated** = sprayed distance × swath × planned rate |

### How coverage is computed

Every stretch flown with the sprayer on is buffered by half the swath and
unioned (sprayed area). Ground covered by two passes is double-sprayed.
Field minus sprayed is missed. Gaps and overlaps narrower than 0.5 m are
ignored as below positioning accuracy. The UTM zone is picked from the field
centroid.

## Server (Uzbekistan) and sync

The bot and the verify page need the internet, so they run on the Atlas
server. Records made offline are queued on the laptop and pushed when the
connection returns.

```bash
# on the server
cp .env.example .env          # set ATLAS_BASE_URL, ATLAS_BOT_TOKEN, ATLAS_SYNC_TOKEN
docker compose up -d --build

# once per laptop: make the server trust the laptop's signing key
python -m atlas keys show                                   # on the laptop
docker compose exec web python -m atlas keys trust KEY_ID PUBLIC_KEY --label "laptop 1"

# on the laptop, whenever there is internet (ATLAS_SERVER_URL + ATLAS_SYNC_TOKEN set)
python -m atlas sync
```

The server stores a synced record only if its signature verifies against a
key it already trusts, then queues the Telegram delivery. Reports for a
farmer who has not registered yet, or sent while Telegram is unreachable,
wait in the queue and are retried. (The Docker files have not been built on
the development machine, which has no Docker.)

## Rules the code keeps

* **Offline first.** Steps 1–4 use no network. Maps are drawn from the
  record's own geometry; there are no map tiles.
* **Data stays on the laptop and the server.** The only outside service is
  the Telegram Bot API, used to deliver a farmer's own report to that
  farmer. Verify pages load nothing from other sites: no fonts, scripts,
  tiles or analytics.
* **Nothing leaves a record without a share.** A record is reachable only
  through a share token. There is no listing or search. The QR on the
  farmer's own PDF is the farmer's own revocable link; every third-party
  share has its own link and PDF. Every creation, opening and revocation is
  logged. The farmer's phone number is never part of the shared record.
* **Signed records are never edited.** Atlas refuses updates and deletes; a
  correction is a new version that carries the hash of the one it replaces.
* **Ground software never controls the drone.** The gateway only reads
  telemetry. The one message it sends is the existing stream-rate request,
  and a test fails if anything else is added.
* **Sample data is labelled** on the PDF, the Telegram summary and the
  verify page.

## Layout

```
atlas/
  ingest/      DataFlash .bin and NDJSON readers, sprayer source, field boundary
  compute/     coverage geometry
  records/     canonical JSON, Ed25519 signing, create / amend / verify
  report/      coverage map and one-page PDF
  bot/         Telegram bot, local Bot API stand-in for tests and the demo
  web/         verify server and page
  shares.py  sync.py  pipeline.py  demo.py  cli.py  db.py  i18n.py  config.py
telemetry/     existing gateway, coverage logger, planner, simulator (extended, not rewritten)
tests/atlas/   Atlas tests
examples/      sample job.json and field.geojson
```

Changes to the existing `telemetry/` files are additive:

* `mavlink_ground_gateway.py` also forwards `SERVO_OUTPUT_RAW`,
  `RELAY_STATUS`, `NAMED_VALUE_FLOAT` and the aircraft clock, and takes
  `--connection`. It now selects the MAVLink 2 dialect so servo channels
  9–16 are visible.
* `coverage_logger.py` gains an optional `spray_source` and `keep_track`.
  Default behaviour is unchanged.
* `simulate_telemetry.py` gains `--mission`. Running it with no arguments
  behaves as before.
* `spray_planner.py` gains `field_boundary_lonlat()`.

## Known limits of the prototype

* The verifier trusts the public keys in its own database. For third
  parties to check independently, the public key should also be published
  somewhere they already trust.
* The signing key is stored unencrypted in `data/keys/`.
* Height is height above the take-off point, not above the crop.
* "Problems" on the Modified page are in English only.
* One job is one log file. A job flown over several batteries needs the
  logs joined first.
