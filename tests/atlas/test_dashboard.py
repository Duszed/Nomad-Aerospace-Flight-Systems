"""The laptop dashboard: every screen and route, demo mode, and its safety rules."""

from __future__ import annotations

import json
import re
import socket
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine, func, inspect, select, text

from atlas import SAMPLE_DATA_NOTICE
from atlas.dashboard import Runtime, launcher
from atlas.dashboard import jobs as J
from atlas.dashboard.geo import (
    area_ha, boundary_from_kml, boundary_from_points, boundary_from_upload, svg_map,
)
from atlas.dashboard.services import NOT_SET_UP, can_reach
from atlas.dashboard.text import D
from atlas.db import (
    Confirmation, Draft, Farmer, Field, Job, Outbox, RecordVersion, Share, init_db,
)
from atlas.i18n import T
from atlas.ingest import IngestError
from atlas.ingest.dataflash_writer import write_dataflash
from atlas.records import verify_record
from atlas.web import create_app
from atlas.web.app import DASHBOARD_CSP, VERIFY_CSP

REPO = Path(__file__).resolve().parents[2]
LOCAL = dict(base_url="http://localhost:8000", client=("127.0.0.1", 50000))
PHONE = "+998 90 111 22 33"


def make_client(rt: Runtime, **kw) -> TestClient:
    return TestClient(create_app(runtime=rt), **{**LOCAL, **kw})


@pytest.fixture
def rt(tmp_path, monkeypatch):
    for name in ("ATLAS_DB_URL", "ATLAS_BOT_TOKEN", "ATLAS_SERVER_URL", "ATLAS_SYNC_TOKEN",
                 "ATLAS_TELEGRAM_API"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)                      # no stray .env
    runtime = Runtime(tmp_path / "data", background=False)
    runtime.tile_fetcher = lambda z, x, y: None      # tests never touch the internet
    yield runtime
    runtime.close()


@pytest.fixture
def client(rt):
    with make_client(rt) as c:
        c.get("/lang/en")
        yield c


@pytest.fixture
def geojson_file(demo_boundary):
    ring = [list(p) for p in demo_boundary.exterior.coords]
    return json.dumps({"type": "Feature", "properties": {},
                       "geometry": {"type": "Polygon", "coordinates": [ring]}}).encode()


@pytest.fixture
def farmer_and_field(client, rt, geojson_file):
    """A farmer with the demo field, created through the screens."""
    client.post("/farmers", data={"name": "Test Farmer", "phone": PHONE, "language": "en"})
    with rt.Session() as s:
        farmer = s.scalar(select(Farmer))
    r = client.post(f"/farmers/{farmer.id}/fields", data={"name": "North field", "crop": "Cotton"},
                    files={"boundary_file": ("field.geojson", geojson_file, "application/json")})
    assert r.status_code == 200
    with rt.Session() as s:
        return farmer.id, s.scalar(select(Field.id))


@pytest.fixture
def bin_log(tmp_path, faulty_samples):
    return write_dataflash(faulty_samples, tmp_path / "flight.bin").read_bytes()


DETAILS = {"crop": "Cotton", "product": "Product 25 EC", "batch": "B-1",
           "planned_rate_l_ha": "15", "swath_m": "7,0", "pilot": "Pilot One",
           "drone_id": "K30-001", "temperature_c": "24", "humidity_pct": "41",
           "wind_ms": "2,5", "wind_direction": "NE", "tank_litres_used": ""}


def start_draft(client, rt, farmer_id, field_id) -> str:
    r = client.post("/jobs/new", data={"farmer_id": farmer_id, "field_id": field_id})
    assert "/details" in str(r.url)
    with rt.Session() as s:
        return s.scalar(select(Draft.id))


def draft_with_log(client, rt, ids, bin_log) -> str:
    draft_id = start_draft(client, rt, *ids)
    assert "/flight" in str(client.post(f"/drafts/{draft_id}/details", data=DETAILS).url)
    r = client.post(f"/drafts/{draft_id}/upload",
                    files={"log": ("flight.bin", bin_log, "application/octet-stream")})
    assert "/results" in str(r.url), r.text[:400]
    return draft_id


def signed_job(client, rt, ids, bin_log) -> str:
    draft_id = draft_with_log(client, rt, ids, bin_log)
    r = client.post(f"/drafts/{draft_id}/sign")
    assert "/jobs/" in str(r.url)
    return str(r.url).rsplit("/", 1)[1]


# --- home, languages, safety ----------------------------------------------------

def test_home_renders_empty_in_three_languages(client):
    for lang, word in (("uz", "Bosh sahifa"), ("ru", "Главная"), ("en", "Home")):
        client.get(f"/lang/{lang}")
        page = client.get("/")
        assert page.status_code == 200 and word in page.text
        assert f'<html lang="{lang}">' in page.text
    assert "Load demo data" in page.text and "No jobs today yet." in page.text


def test_language_switch_only_redirects_inside_the_dashboard(client):
    r = client.get("/lang/ru", params={"next": "//evil.example/x"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    r = client.get("/lang/ru", params={"next": "/jobs"}, follow_redirects=False)
    assert r.headers["location"] == "/jobs" and "atlas_lang=ru" in r.headers["set-cookie"]


def test_pages_load_nothing_from_other_sites(client):
    """Fonts, scripts and styles all come from this laptop."""
    for path in ("/", "/farmers", "/jobs", "/jobs/new", "/settings"):
        page = client.get(path)
        assert page.headers["content-security-policy"] == DASHBOARD_CSP
        refs = re.findall(r'(?:src|href)="([^"]+)"', page.text)
        assert refs and all(ref.startswith("/") for ref in refs), refs
    css = client.get("/static/app.css").text
    assert "http://" not in css and "https://" not in css
    assert client.get("/static/vendor/htmx.min.js").status_code == 200
    assert client.get("/static/vendor/fonts/inter-cyrillic.woff2").status_code == 200


def test_dashboard_answers_only_to_this_computer(rt):
    with make_client(rt, client=("192.168.1.50", 40000)) as other:
        assert other.get("/").status_code == 403
        assert other.post("/demo/load").status_code == 403
        assert other.get("/healthz").status_code == 200          # verify side stays public
    with make_client(rt, base_url="http://atlas.evil.example") as rebound:
        assert rebound.get("/").status_code == 403


def test_other_websites_cannot_change_anything(client, rt):
    data = {"name": "X", "phone": PHONE, "language": "en"}
    assert client.post("/farmers", data=data, headers={"Sec-Fetch-Site": "cross-site"}
                       ).status_code == 403
    assert client.post("/farmers", data=data, headers={"Origin": "https://evil.example"}
                       ).status_code == 403
    assert client.post("/farmers", data=data, headers={"Origin": "null"}).status_code == 403
    with rt.Session() as s:
        assert s.scalar(select(func.count()).select_from(Farmer)) == 0
    ok = client.post("/farmers", data=data, headers={
        "Sec-Fetch-Site": "same-origin", "Origin": "null"})   # what a browser sends
    assert ok.status_code == 200
    with rt.Session() as s:
        assert s.scalar(select(func.count()).select_from(Farmer)) == 1


def test_the_public_server_has_no_dashboard(settings, Session):
    with TestClient(create_app(settings, Session)) as public:
        assert public.get("/farmers").status_code == 404
        assert public.get("/settings").status_code == 404
        assert public.post("/demo/load").status_code in (404, 405)
        home = public.get("/")
        assert home.status_code == 200 and home.headers["content-security-policy"] == VERIFY_CSP


def test_verify_pages_work_inside_the_laptop_app(client, rt, farmer_and_field, bin_log):
    job_id = signed_job(client, rt, farmer_and_field, bin_log)
    with rt.Session() as s:
        token = s.scalar(select(Share.token).where(Share.recipient_type == "owner"))
    page = client.get(f"/v/{token}?lang=en")
    assert page.status_code == 200 and "Verified" in page.text
    assert page.headers["content-security-policy"] == VERIFY_CSP
    assert client.get(f"/api/verify/{token}").json()["status"] == "verified"
    assert job_id


# --- farmers & fields -----------------------------------------------------------

def test_add_edit_and_search_farmers(client, rt):
    page = client.post("/farmers", data={"name": "Karimov A.", "phone": "901234567",
                                         "language": "uz"})
    assert "Karimov A." in page.text and "+998901234567" in page.text
    assert "Telegram not linked yet" in page.text
    with rt.Session() as s:
        farmer = s.scalar(select(Farmer))
    assert farmer.phone == "+998901234567" and farmer.language == "uz"

    assert "not a valid phone" in client.post(
        "/farmers", data={"name": "Bad", "phone": "12", "language": "uz"}).text
    assert "already has this phone" in client.post(
        "/farmers", data={"name": "Twin", "phone": "+998 90 123 45 67", "language": "uz"}).text

    page = client.post(f"/farmers/{farmer.id}", data={
        "name": "Karimov Anvar", "phone": "+998 90 123 45 68", "language": "ru"})
    assert "Karimov Anvar" in page.text
    with rt.Session() as s:
        assert s.scalar(select(func.count()).select_from(Farmer)) == 1
        assert s.get(Farmer, farmer.id).language == "ru"

    assert "Karimov Anvar" in client.get("/farmers/list", params={"q": "karim"}).text
    assert "Karimov Anvar" in client.get("/farmers/list", params={"q": "4568"}).text
    assert "Karimov Anvar" not in client.get("/farmers/list", params={"q": "zzz"}).text


def test_field_from_geojson_kml_and_drawn_corners(client, rt, farmer_and_field, demo_boundary):
    farmer_id, _ = farmer_and_field
    ring = list(demo_boundary.exterior.coords)
    kml = ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Placemark>'
           "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
           + " ".join(f"{lon},{lat},0" for lon, lat in ring)
           + "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>")
    client.post(f"/farmers/{farmer_id}/fields", data={"name": "KML field", "crop": "Wheat"},
                files={"boundary_file": ("f.kml", kml.encode(), "application/xml")})
    drawn = json.dumps([[lat, lon] for lon, lat in ring[:-1]])
    page = client.post(f"/farmers/{farmer_id}/fields",
                       data={"name": "Drawn field", "crop": "", "drawn": drawn})
    assert "KML field" in page.text and "Drawn field" in page.text and "<svg" in page.text
    with rt.Session() as s:
        fields = s.scalars(select(Field).order_by(Field.name)).all()
    assert [f.name for f in fields] == ["Drawn field", "KML field", "North field"]
    assert all(abs(f.area_ha - 4.2) < 0.05 for f in fields)
    assert fields[2].crop == "Cotton"


def test_field_errors_are_plain_messages(client, rt, farmer_and_field, geojson_file):
    farmer_id, _ = farmer_and_field
    url = f"/farmers/{farmer_id}/fields"
    assert "Upload a boundary file or draw" in client.post(
        url, data={"name": "No outline", "crop": ""}).text
    assert "Could not read a field outline" in client.post(
        url, data={"name": "Bad file", "crop": ""},
        files={"boundary_file": ("f.geojson", b"not json", "application/json")}).text
    assert "at least 3 corners" in client.post(
        url, data={"name": "Two points", "drawn": "[[40.4, 68.8], [40.5, 68.9]]"}).text
    assert "already has a field with that name" in client.post(
        url, data={"name": "North field", "crop": ""},
        files={"boundary_file": ("f.geojson", geojson_file, "application/json")}).text
    with rt.Session() as s:
        assert s.scalar(select(func.count()).select_from(Field)) == 1


def test_editing_a_boundary_does_not_touch_signed_records(client, rt, farmer_and_field, bin_log):
    farmer_id, field_id = farmer_and_field
    job_id = signed_job(client, rt, farmer_and_field, bin_log)
    draft_id = draft_with_log(client, rt, farmer_and_field, bin_log)
    with rt.Session() as s:
        before = s.scalar(select(RecordVersion.payload))
        assert s.get(Draft, draft_id).preview
    smaller = json.dumps([[40.4110, 68.8413], [40.4110, 68.8420], [40.4120, 68.8420],
                          [40.4120, 68.8413]])
    client.post(f"/fields/{field_id}", data={"name": "North field", "crop": "Wheat",
                                             "drawn": smaller})
    with rt.Session() as s:
        fld = s.get(Field, field_id)
        assert fld.crop == "Wheat" and fld.area_ha < 1.0
        assert s.scalar(select(RecordVersion.payload)) == before
        assert s.get(Draft, draft_id).preview == ""        # the draft is recomputed
    assert "Verified" in client.get(f"/jobs/{job_id}").text


def test_map_tiles_are_fetched_once_then_served_from_disk(client, rt):
    calls = []
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    def fetch(z, x, y):
        calls.append((z, x, y))
        return png

    rt.tile_fetcher = fetch
    first = client.get("/tiles/15/22651/12354.png")
    again = client.get("/tiles/15/22651/12354.png")
    assert first.content == again.content == png and calls == [(15, 22651, 12354)]
    assert (rt.root / "tiles" / "15" / "22651" / "12354.png").read_bytes() == png

    rt.tile_fetcher = lambda z, x, y: None                     # offline
    assert client.get("/tiles/15/22651/12354.png").status_code == 200    # seen before
    assert client.get("/tiles/15/1/1.png").status_code == 204            # never seen
    assert client.get("/tiles/3/99/0.png").status_code == 404


def test_boundary_readers():
    square = [[40.0, 68.0], [40.0, 68.002], [40.002, 68.002], [40.002, 68.0]]
    poly = boundary_from_points(square)
    assert 3.0 < area_ha(poly) < 4.5
    with pytest.raises(IngestError):
        boundary_from_points([[40.0, 68.0], [40.0, 68.00001], [40.00001, 68.0]])   # 1 m2
    with pytest.raises(IngestError):
        boundary_from_points([[400.0, 68.0], [400.0, 68.002], [400.002, 68.0]])
    with pytest.raises(IngestError):
        boundary_from_kml("<!DOCTYPE x [<!ENTITY a 'b'>]><kml/>")
    with pytest.raises(IngestError):
        boundary_from_upload("x.geojson", b"[1, 2]")
    svg = svg_map({"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in square]]})
    assert svg.startswith("<svg") and "m-outline" in svg


# --- new job wizard -------------------------------------------------------------

def test_wizard_start_needs_a_farmer_with_a_field(client, rt, farmer_and_field):
    farmer_id, field_id = farmer_and_field
    page = client.get("/jobs/new")
    assert "Test Farmer" in page.text
    options = client.get("/jobs/new/fields", params={"farmer_id": farmer_id}).text
    assert field_id in options and "North field" in options and "selected" in options
    assert "Please fill in" in client.post("/jobs/new", data={
        "farmer_id": farmer_id, "field_id": "nope"}).text


def test_wizard_details_keep_what_was_typed_and_explain_mistakes(client, rt, farmer_and_field):
    draft_id = start_draft(client, rt, *farmer_and_field)
    page = client.get(f"/drafts/{draft_id}/details")
    assert 'value="Cotton"' in page.text and 'value="7.0"' in page.text   # field crop, swath
    page = client.post(f"/drafts/{draft_id}/details",
                       data={**DETAILS, "planned_rate_l_ha": "fifteen"})
    assert "Not a valid number: fifteen" in page.text and 'value="Product 25 EC"' in page.text
    page = client.post(f"/drafts/{draft_id}/details", data={**DETAILS, "product": ""})
    assert "Please fill in: product" in page.text
    page = client.post(f"/drafts/{draft_id}/details", data=DETAILS)
    assert "/flight" in str(page.url) and "Drop the flight log" in page.text
    assert "never sends commands to the drone" in page.text


def test_wizard_upload_shows_results_but_signs_nothing(client, rt, farmer_and_field, bin_log):
    draft_id = draft_with_log(client, rt, farmer_and_field, bin_log)
    page = client.get(f"/drafts/{draft_id}/results")
    assert "DRAFT – not signed yet" in page.text
    assert "97.2 %" in page.text and "2 strip(s)" in page.text
    assert page.text.count("ESTIMATED") == 2                    # litres and L/ha
    assert "Estimated: no flow meter" in page.text
    for css in ("m-sprayed", "m-missed", "m-overlap", "m-outline"):
        assert f'class="{css}"' in page.text
    with rt.Session() as s:                                     # nothing signed, nothing queued
        assert s.scalar(select(func.count()).select_from(RecordVersion)) == 0
        assert s.scalar(select(func.count()).select_from(Outbox)) == 0
        assert s.scalar(select(func.count()).select_from(Job)) == 0

    pdf = client.get(f"/drafts/{draft_id}/preview.pdf", params={"lang": "en"})
    assert pdf.headers["content-type"] == "application/pdf"
    reader = PdfReader(__import__("io").BytesIO(pdf.content))
    assert len(reader.pages) == 1 and "DRAFT - NOT SIGNED" in reader.pages[0].extract_text()
    assert "Draft – not signed" in client.get("/").text         # listed on Home


def test_tank_litres_are_labelled_operator_reported(client, rt, farmer_and_field, bin_log):
    draft_id = start_draft(client, rt, *farmer_and_field)
    client.post(f"/drafts/{draft_id}/details", data={**DETAILS, "tank_litres_used": "60"})
    page = client.post(f"/drafts/{draft_id}/upload",
                       files={"log": ("flight.bin", bin_log, "application/octet-stream")})
    assert "60.0 L" in page.text and "operator-reported" in page.text
    assert "ESTIMATED" not in page.text


def test_wizard_rejects_bad_logs_in_plain_words(client, rt, farmer_and_field):
    draft_id = start_draft(client, rt, *farmer_and_field)
    client.post(f"/drafts/{draft_id}/details", data=DETAILS)
    page = client.post(f"/drafts/{draft_id}/upload",
                       files={"log": ("notes.txt", b"hello", "text/plain")})
    assert "Please choose an ArduPilot .bin flight log." in page.text
    page = client.post(f"/drafts/{draft_id}/upload",
                       files={"log": ("flight.bin", b"\x00" * 4096, "application/octet-stream")})
    assert "Could not use that flight log" in page.text and "Traceback" not in page.text
    with rt.Session() as s:
        assert s.get(Draft, draft_id).log_path == ""
    assert not J.upload_dir(rt.settings, draft_id).exists()
    assert "/flight" in str(client.get(f"/drafts/{draft_id}/results").url)


def test_sign_and_send_creates_a_verified_record_and_queues_it(client, rt, farmer_and_field,
                                                               bin_log):
    draft_id = draft_with_log(client, rt, farmer_and_field, bin_log)
    page = client.post(f"/drafts/{draft_id}/sign")
    assert "Record signed. The report is queued for the farmer." in page.text
    assert "Verified · version 1" in page.text and "Queued" in page.text
    assert "The farmer has not linked Telegram yet." in page.text
    with rt.Session() as s:
        record = s.scalar(select(RecordVersion))
        assert verify_record(s, record).ok
        payload = json.loads(record.payload)
        assert payload["job"]["product"] == "Product 25 EC" and payload["sample_data"] is False
        assert payload["weather"]["wind_ms"] == 2.5 and payload["input"]["kind"] == "dataflash"
        assert PHONE.replace(" ", "") not in record.payload     # phone never in the record
        assert sorted(o.kind for o in s.scalars(select(Outbox))) == [
            "sync_record", "telegram_report"]
        assert s.get(Draft, draft_id) is None
        assert s.scalar(select(Share.recipient_type)) == "owner"   # only the farmer's own link
    assert not J.upload_dir(rt.settings, draft_id).exists()
    assert list(rt.settings.logs_dir.glob("*.bin"))             # the input log is kept


def test_drafts_can_be_deleted(client, rt, farmer_and_field, bin_log):
    draft_id = draft_with_log(client, rt, farmer_and_field, bin_log)
    client.post(f"/drafts/{draft_id}/delete")
    with rt.Session() as s:
        assert s.scalar(select(func.count()).select_from(Draft)) == 0
    assert not J.upload_dir(rt.settings, draft_id).exists()
    assert "does not exist any more" in client.get(f"/drafts/{draft_id}/results").text


def test_live_receive_through_the_real_gateway(client, rt, farmer_and_field):
    """Simulator -> MAVLink/UDP -> gateway -> dashboard -> results."""
    rt.sim_speedup = 150.0
    draft_id = start_draft(client, rt, *farmer_and_field)
    client.post(f"/drafts/{draft_id}/details", data=DETAILS)
    page = client.post(f"/drafts/{draft_id}/live/start", data={"mode": "sim"})
    assert "Finish flight" in page.text
    deadline = time.monotonic() + 90
    while not rt.capture.status()["sim_done"]:
        assert time.monotonic() < deadline, rt.capture.status()
        time.sleep(0.5)
    status = client.get(f"/drafts/{draft_id}/live/status").text
    assert "Receiving:" in status and "Simulated flight complete" in status
    page = client.post(f"/drafts/{draft_id}/live/finish")
    assert "/results" in str(page.url), page.text[:500]
    assert SAMPLE_DATA_NOTICE in page.text                      # a simulated flight is sample data
    with rt.Session() as s:
        draft = s.get(Draft, draft_id)
        res = json.loads(draft.preview)["results"]
        assert draft.sample_data and 94.0 < res["coverage_pct"] < 99.5
    assert not rt.capture.active


def test_live_receive_with_no_data_is_explained(client, rt, farmer_and_field):
    draft_id = start_draft(client, rt, *farmer_and_field)
    client.post(f"/drafts/{draft_id}/details", data=DETAILS)
    rt.store.update(drone_connection=f"udpin:127.0.0.1:{launcher_free_udp()}")
    client.post(f"/drafts/{draft_id}/live/start", data={"mode": "drone"})
    assert rt.capture.active
    assert "waiting for the drone" in client.get(f"/drafts/{draft_id}/live/status").text
    page = client.post(f"/drafts/{draft_id}/live/finish")
    assert "No position data was received" in page.text and not rt.capture.active


def launcher_free_udp() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_dashboard_never_commands_the_drone():
    """The dashboard has no MAVLink code at all: it only reads the gateway's output."""
    for path in (REPO / "atlas" / "dashboard").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("pymavlink", "mavutil", "command_long", "_send(", "mission_item",
                          "set_mode", "arducopter_arm"):
            assert forbidden not in source, f"{path.name} contains {forbidden}"


# --- jobs -----------------------------------------------------------------------

def test_job_list_search_and_filters(client, rt, farmer_and_field, bin_log):
    signed_job(client, rt, farmer_and_field, bin_log)
    page = client.get("/jobs")
    assert "Test Farmer" in page.text and "97.2 %" in page.text
    table = lambda **p: client.get("/jobs/table", params=p).text     # noqa: E731
    assert "Test Farmer" in table(q="product 25")
    assert "No jobs match." in table(q="zzz")
    assert "Test Farmer" in table(state="queued") and "No jobs match." in table(state="sent")
    assert "Test Farmer" in table(crop="Cotton") and "No jobs match." in table(crop="Rice")
    assert "No jobs match." in table(date_to="2020-01-01")
    assert "Test Farmer" in table(date_from="2020-01-01", date_to="2099-01-01")


def test_job_pdf_amend_and_totals(client, rt, farmer_and_field, bin_log):
    job_id = signed_job(client, rt, farmer_and_field, bin_log)
    pdf = client.get(f"/jobs/{job_id}/report.pdf", params={"lang": "ru"})
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert "DRAFT" not in PdfReader(__import__("io").BytesIO(pdf.content)).pages[0].extract_text()

    page = client.post(f"/jobs/{job_id}/amend", data={
        "crop": "Cotton", "product": "Product 25 EC", "batch": "B-2", "pilot": "Pilot One",
        "drone_id": "K30-001", "reason": "Batch mistyped"})
    assert "Version 2 created" in page.text and "Verified · version 2" in page.text
    with rt.Session() as s:
        versions = s.scalars(select(RecordVersion).order_by(RecordVersion.version)).all()
        assert [v.version for v in versions] == [1, 2]
        assert json.loads(versions[0].payload)["job"]["batch"] == "B-1"       # untouched
        assert json.loads(versions[1].payload)["job"]["batch"] == "B-2"
        assert all(verify_record(s, v).ok for v in versions)
    assert "Please fill in" in client.post(f"/jobs/{job_id}/amend", data={
        "crop": "Cotton", "product": "Product 25 EC", "batch": "B-2", "pilot": "Pilot One",
        "drone_id": "K30-001", "reason": ""}).text

    with rt.Session() as s:
        totals = J.totals(J.list_jobs(rt, s))
    assert totals["season_jobs"] == 1 and 4.0 < totals["season_ha"] < 4.2


def test_a_tampered_record_shows_modified_and_gives_no_report(client, rt, farmer_and_field,
                                                              bin_log):
    job_id = signed_job(client, rt, farmer_and_field, bin_log)
    with rt.Session() as s:
        s.execute(text("UPDATE record_version SET payload = replace(payload, :old, :new)"),
                  {"old": '"coverage_pct":97', "new": '"coverage_pct":99'})
        s.commit()
    page = client.get(f"/jobs/{job_id}")
    assert "MODIFIED – this record no longer matches its signature" in page.text
    assert "<svg" not in page.text                              # altered numbers are not shown
    assert client.get(f"/jobs/{job_id}/report.pdf").status_code == 409


def test_unknown_pages_lead_home_with_a_message(client):
    for path in ("/jobs/nope", "/drafts/nope/details", "/farmers?id=nope"):
        assert client.get(path).status_code == 200
    assert "does not exist any more" in client.get("/jobs/nope").text


# --- settings and sending -------------------------------------------------------

def test_settings_are_saved_and_secrets_never_shown(client, rt):
    token = "123456789:AAExampleExampleExampleExample_abc"
    page = client.post("/settings", data={
        "bot_token": token, "server_url": "", "sync_token": "", "swath_m": "8,5",
        "kind": "relay", "channel": "10", "pwm_on": "1200", "language": "ru",
        "drone_connection": "COM5"})
    assert "Сохранено." in page.text and token not in page.text
    assert rt.store["swath_m"] == 8.5 and rt.store["sprayer"]["kind"] == "relay"
    assert rt.store["drone_connection"] == "COM5" and rt.settings.bot_token == token
    assert rt.services.target() == ("api.telegram.org", 443) and not rt.server_mode
    saved = json.loads((rt.root / "settings.json").read_text(encoding="utf-8"))
    assert saved["bot_token"] == token and saved["language"] == "ru"

    keep = {"swath_m": "8.5", "kind": "relay", "channel": "10", "pwm_on": "1200",
            "language": "en", "drone_connection": "COM5"}
    client.post("/settings", data={**keep, "bot_token": "", "server_url": ""})
    assert rt.settings.bot_token == token                       # empty box keeps the token
    client.post("/settings", data={**keep, "bot_token": "", "remove_token": "1"})
    assert rt.settings.bot_token is None and rt.services.target() is None

    assert "does not look like a Telegram bot token" in client.post(
        "/settings", data={**keep, "bot_token": "hello"}).text
    assert "must start with https://" in client.post(
        "/settings", data={**keep, "server_url": "atlas.example.uz"}).text
    assert "Not a valid number" in client.post("/settings", data={**keep, "swath_m": "wide"}).text


def test_server_mode_points_links_and_sync_at_the_server(client, rt, farmer_and_field, bin_log):
    client.post("/settings", data={
        "server_url": "https://atlas.example.uz/", "sync_token": "secret-sync", "swath_m": "7",
        "kind": "servo", "channel": "9", "pwm_on": "1150", "language": "en",
        "drone_connection": "udpin:0.0.0.0:14550"})
    assert rt.server_mode and rt.settings.base_url == "https://atlas.example.uz"
    assert rt.services.target() == ("atlas.example.uz", 443)
    assert "secret-sync" not in client.get("/settings").text
    job_id = signed_job(client, rt, farmer_and_field, bin_log)
    page = client.get(f"/jobs/{job_id}")
    assert "Waiting to sync" in page.text and "https://atlas.example.uz/v/" in page.text
    assert rt.services.waiting() == 1
    assert "1 item(s) waiting to send" in client.get("/ui/sync").text


def test_qr_printed_on_the_laptop_verifies_on_the_server(processed, session, settings, tmp_path):
    """The farmer's own link travels with the record, so one QR works in both places."""
    from atlas.config import Settings
    from atlas.shares import owner_share
    from atlas.sync import build_bundle, receive_bundle, trust_key

    token = owner_share(session, processed.record).token
    bundle = build_bundle(session, processed.record)
    session.commit()
    assert bundle["owner_share"]["token"] == token

    server = Settings(data_dir=tmp_path / "server", base_url="https://atlas.example.uz")
    server.ensure_dirs()
    ServerSession = init_db(server.database_url)
    with ServerSession() as s:
        key = session.get(__import__("atlas.db", fromlist=["SigningKey"]).SigningKey,
                          processed.record.key_id)
        trust_key(s, key.key_id, key.public_key, "laptop")
        assert receive_bundle(s, bundle) is True
        s.commit()
        assert owner_share(s, s.get(RecordVersion, processed.record.id)).token == token
    with TestClient(create_app(server, ServerSession)) as public:
        assert public.get(f"/api/verify/{token}").json()["status"] == "verified"
    ServerSession.kw["bind"].dispose()


def test_sync_card_and_services_when_nothing_is_set_up(client, rt):
    assert rt.services.target() is None and rt.services.state == NOT_SET_UP
    assert "Not set up" in client.get("/ui/sync").text
    rt.services.start()                                         # nothing to start
    assert rt.services._thread is None
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert can_reach("127.0.0.1", port, timeout=0.5) is False


def test_laptop_app_refuses_incoming_sync(client):
    r = client.post("/api/sync/record", json={}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 403


# --- demo mode ------------------------------------------------------------------

@pytest.fixture(scope="module")
def demo_rt(tmp_path_factory):
    runtime = Runtime(tmp_path_factory.mktemp("demo-dash") / "data", background=False)
    with make_client(runtime) as c:
        c.get("/lang/en")
        c.post("/farmers", data={"name": "Real Customer", "phone": PHONE, "language": "uz"})
        runtime.store.update(bot_token="123456789:AAExampleExampleExampleExample_abc")
        runtime.reload()
        page = c.post("/demo/load")
        yield runtime, c, page
    runtime.close()


def test_demo_creates_three_finished_sample_jobs(demo_rt):
    rt, client, page = demo_rt
    assert "Demo data loaded. Your own data is untouched." in page.text
    assert rt.demo and (rt.demo_dir / ".atlas-demo").is_file()
    with rt.Session() as s:
        views = J.list_jobs(rt, s)
        assert len(views) == 3 and all(v["sample"] for v in views)
        assert all(verify_record(s, v["record"]).ok for v in views)
        assert all(v["payload"]["sample_data"] is True for v in views)
        states = {v["field"].name: v["delivery"]["state"] for v in views}
        basis = {v["field"].name: v["res"]["litres_basis"] for v in views}
        assert s.scalar(select(func.count()).select_from(Confirmation)) == 1
        kinds = {sh.recipient_type: sh.revoked_at is not None
                 for sh in s.scalars(select(Share).where(Share.recipient_type != "owner"))}
    assert states == {"Sample field 4.2 ha": "confirmed", "Sample field 3.1 ha": "sent",
                      "Sample field 5.6 ha": "queued"}
    assert basis["Sample field 3.1 ha"] == "measured" and basis["Sample field 4.2 ha"] == "estimated"
    assert kinds == {"bank": False, "insurer": True}


def test_demo_is_labelled_on_every_screen(demo_rt):
    rt, client, _ = demo_rt
    with rt.Session() as s:
        job_id = s.scalar(select(Job.id))
        farmer_id = s.scalar(select(Farmer.id))
    for path in ("/", "/farmers", f"/farmers?id={farmer_id}", "/jobs", f"/jobs/{job_id}",
                 "/jobs/new", "/settings"):
        for lang in ("uz", "ru", "en"):
            client.get(f"/lang/{lang}")
            page = client.get(path)
            assert page.status_code == 200 and SAMPLE_DATA_NOTICE in page.text, (path, lang)
    client.get("/lang/en")
    pdf = client.get(f"/jobs/{job_id}/report.pdf", params={"lang": "en"})
    assert SAMPLE_DATA_NOTICE in PdfReader(__import__("io").BytesIO(pdf.content)).pages[0].extract_text()


def test_demo_job_page_shows_delivery_and_shares(demo_rt):
    rt, client, _ = demo_rt
    with rt.Session() as s:
        job_id = s.scalar(select(Job.id).join(Field, Job.field_id == Field.id)
                          .where(Field.name == "Sample field 4.2 ha"))
        bank = s.scalar(select(Share).where(Share.recipient_type == "bank"))
    page = client.get(f"/jobs/{job_id}")
    assert "Confirmed by farmer" in page.text and "opened 2×" in page.text
    assert "Bank" in page.text and "Insurer" in page.text and "revoked" in page.text
    assert client.get(f"/api/verify/{bank.token}").json()["status"] == "verified"

    page = client.post(f"/shares/{bank.id}/revoke")
    assert "Share revoked." in page.text
    assert client.get(f"/api/verify/{bank.token}").status_code == 410
    with rt.Session() as s:
        events = [e.detail for e in s.get(Share, bank.id).events if e.kind == "revoked"]
    assert events == ["by operator"]


def test_demo_never_sends_and_never_mixes_with_real_data(demo_rt):
    rt, client, _ = demo_rt
    assert rt.settings.bot_token is None and rt.settings.server_url is None
    assert rt.services.target() is None and rt.services.waiting() == 0
    assert "nothing is sent to Telegram" in client.get("/ui/sync").text
    assert "Real Customer" not in client.get("/farmers").text
    real = init_db(f"sqlite:///{(rt.root / 'atlas.db').as_posix()}")
    with real() as s:
        assert [f.name for f in s.scalars(select(Farmer))] == ["Real Customer"]
        assert s.scalar(select(func.count()).select_from(RecordVersion)) == 0
    real.kw["bind"].dispose()


def test_clear_demo_returns_to_untouched_real_data(demo_rt):
    rt, client, _ = demo_rt
    page = client.post("/demo/clear")
    assert "Demo data cleared." in page.text and SAMPLE_DATA_NOTICE not in page.text
    assert not rt.demo and not rt.demo_dir.exists()
    assert "Real Customer" in client.get("/farmers").text
    assert rt.settings.bot_token                                 # sending settings are back
    with rt.Session() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 0


def test_clear_demo_never_deletes_a_folder_that_is_not_demo(rt):
    rt.demo_dir.mkdir()
    (rt.demo_dir / "customer.db").write_text("real")
    with pytest.raises(RuntimeError, match="not a demo folder"):
        rt.clear_demo()
    assert (rt.demo_dir / "customer.db").read_text() == "real"


# --- start / stop ---------------------------------------------------------------

def test_stop_needs_the_token_from_the_data_folder(rt):
    rt.shutdown_token = "tok"
    stopped = []
    rt.on_shutdown = lambda: stopped.append(True)
    with make_client(rt) as c:
        assert c.post("/api/shutdown").status_code == 403
        assert c.post("/api/shutdown", headers={"X-Atlas-Token": "wrong"}).status_code == 403
        assert not stopped
        assert c.post("/api/shutdown", headers={"X-Atlas-Token": "tok"}).json() == {"ok": True}
    assert stopped == [True]


def test_start_explains_a_busy_port_and_stop_when_not_running(tmp_path):
    said: list[str] = []
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        code = launcher.start(tmp_path / "data", port=port, open_browser=False, say=said.append)
    assert code == 1 and "another program on this computer is using port" in said[0]
    assert "Traceback" not in " ".join(said)
    said.clear()
    assert launcher.stop(tmp_path / "data", say=said.append) == 0
    assert said == ["Atlas is not running."]


def test_one_click_files_exist_with_the_right_line_endings():
    for name in ("Start Atlas.bat", "Stop Atlas.bat"):
        data = (REPO / name).read_bytes()
        assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
        assert b"-m atlas" in data
    for name in ("start-atlas.sh", "stop-atlas.sh"):
        data = (REPO / name).read_bytes()
        assert data.startswith(b"#!/usr/bin/env bash\n") and b"\r" not in data
    assert "localhost:8000" in (REPO / "HOW TO START.md").read_text(encoding="utf-8")
    attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in attrs


# --- wording and database -------------------------------------------------------

def test_every_dashboard_string_exists_in_three_languages():
    for key, entry in D.items():
        assert set(entry) == {"en", "ru", "uz"}, key
        assert all(entry.values()), key
        for char in ("'", "`", "‘", "’"):          # oʻ gʻ use U+02BB, never a quote
            assert char not in entry["uz"], (key, entry["uz"])
        assert re.search("[а-яА-Я]", entry["ru"]) or key in ("d_points",), key


def test_templates_only_use_wording_that_exists():
    used = set()
    for path in (REPO / "atlas" / "web" / "templates" / "dash").glob("*.html"):
        used |= set(re.findall(r"""\bt\('([a-z_0-9]+)'(?!\s*~)""",
                               path.read_text(encoding="utf-8")))
    assert len(used) > 100 and not sorted(k for k in used if k not in T)
    for prefix, names in (("d_st_", J.STATES), ("d_step", "1234"), ("d_src_", (
            "servo", "relay", "flow", "speed")), ("d_why_", ("unreg", "nobot", "offline")),
            ("basis_", ("estimated", "measured", "operator")),
            ("rcp_", ("bank", "insurer", "buyer", "cluster"))):
        assert all(f"{prefix}{n}" in T for n in names)


def test_older_databases_gain_the_new_columns(tmp_path):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as con:
        con.execute(text("CREATE TABLE field (id VARCHAR(32) PRIMARY KEY, farmer_id VARCHAR(32), "
                         "name VARCHAR(200), boundary_geojson TEXT, area_ha FLOAT, "
                         "created_at DATETIME)"))
        con.execute(text("INSERT INTO field VALUES ('f1', 'x', 'Old field', '{}', 1.0, NULL)"))
    engine.dispose()
    Session = init_db(url)
    with Session() as s:
        columns = {c["name"] for c in inspect(s.get_bind()).get_columns("field")}
        assert "crop" in columns
        assert s.execute(text("SELECT crop FROM field")).scalar() == ""
        assert "draft" in inspect(s.get_bind()).get_table_names()
    Session.kw["bind"].dispose()
