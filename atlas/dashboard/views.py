"""The dashboard's pages: Home, Farmers & fields, New job, Jobs, Settings.

Server-rendered HTML with HTMX for the small live parts. Everything is
served from this laptop; pages load nothing from the internet. The only
outside request the dashboard can make is for OpenStreetMap map pictures
on the boundary-drawing map, fetched by this app and saved on disk.
"""

from __future__ import annotations

import hmac
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment
from markupsafe import Markup
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import __version__
from ..db import Draft, Farmer, Field, Job, RecordVersion, Share
from ..i18n import LANGUAGE_NAMES, fmt_dt, fmt_num, fmt_signed_pct, norm_lang, t
from ..ingest.sprayer import KINDS
from ..ingest.track import IngestError
from ..records import AMENDABLE, amend_record, latest_version, normalize_phone, verify_record
from ..report import render_pdf
from ..shares import owner_share, report_filename, report_pdf, revoke_share, share_url
from . import jobs as J
from . import text as _text  # noqa: F401 - registers the dashboard wording
from .capture import ERROR, IDLE
from .geo import (
    area_ha, boundary_from_points, boundary_from_upload, boundary_of, boundary_text, field_map,
    payload_map,
)
from .runtime import Runtime
from .services import CHECKING, NOT_SET_UP, OFFLINE, ONLINE

LANG_COOKIE = "atlas_lang"
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_AGENT = f"NomadAtlas/{__version__} (field laptop dashboard)"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_LOG_BYTES = 600 * 1024 * 1024
DEFAULT_CENTER = (41.31, 69.28)      # Tashkent, until there is a field to centre on


_tile_client: httpx.Client | None = None


def fetch_osm_tile(z: int, x: int, y: int) -> bytes | None:
    """One map picture from OpenStreetMap, or None when offline.

    The request carries only the tile numbers and a generic program name:
    nothing about the farmer, the field or the job.
    """
    global _tile_client
    if _tile_client is None:
        _tile_client = httpx.Client(timeout=6.0, headers={"User-Agent": TILE_AGENT})
    try:
        resp = _tile_client.get(TILE_URL.format(z=z, x=x, y=y))
    except httpx.HTTPError:
        return None
    if resp.status_code == 200 and resp.content.startswith(PNG_MAGIC):
        return resp.content
    return None


def _number(raw: str | None) -> float | None:
    """A typed number; a decimal comma is fine. '' -> None."""
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    return float(raw)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "").name)[-80:]
    return name or "flight.bin"


def build_router(rt: Runtime, templates: Environment) -> APIRouter:
    r = APIRouter()

    # --- helpers ----------------------------------------------------------------

    def lang_of(request: Request) -> str:
        return norm_lang(request.cookies.get(LANG_COOKIE) or rt.store["language"])

    def say(kind: str, key: str, **fmt) -> None:
        rt.flash.append((kind, key, fmt))

    def go(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        lang = lang_of(request)
        flash = []
        if not name.startswith("dash/_"):      # partials leave messages for the next page
            flash, rt.flash = rt.flash, []
        html = templates.get_template(name).render(
            lang=lang, t=lambda key, /, **kw: t(lang, key, **kw),
            num=lambda value, digits=1: fmt_num(value, digits, lang),
            pct=lambda value: fmt_signed_pct(value, lang),
            dt=fmt_dt, languages=LANGUAGE_NAMES, demo=rt.demo, version=__version__,
            flash=[(kind, t(lang, key, **fmt)) for kind, key, fmt in flash],
            path=request.url.path, here=quote(
                request.url.path + (f"?{request.url.query}" if request.url.query else "")),
            **ctx)
        return HTMLResponse(html, status_code=status)

    def missing() -> RedirectResponse:
        say("err", "d_err_not_found")
        return go("/")

    def sync_ctx() -> dict:
        svc = rt.services
        state = svc.state
        if rt.demo:
            state = "demo"
        elif svc.target() is None:
            state = NOT_SET_UP
        elif not rt.background and state == NOT_SET_UP:
            state = CHECKING
        return {"sync_state": state, "waiting": svc.waiting(), "server_mode": rt.server_mode,
                "ONLINE": ONLINE, "OFFLINE": OFFLINE, "CHECKING": CHECKING,
                "NOT_SET_UP": NOT_SET_UP}

    def drafts_of(session) -> list[Draft]:
        return session.scalars(select(Draft).order_by(Draft.created_at.desc())).all()

    # --- home -------------------------------------------------------------------

    @r.get("/", response_class=HTMLResponse)
    def home(request: Request):
        with rt.Session() as session:
            views = J.list_jobs(rt, session)
            return render(request, "dash/home.html", nav="home", totals=J.totals(views),
                          drafts=drafts_of(session), empty=not views, **sync_ctx())

    @r.get("/ui/sync", response_class=HTMLResponse)
    def sync_card(request: Request):
        return render(request, "dash/_sync.html", **sync_ctx())

    @r.get("/lang/{code}")
    def set_language(code: str, next: str = "/"):
        if not next.startswith("/") or next.startswith("//"):
            next = "/"
        resp = go(next)
        resp.set_cookie(LANG_COOKIE, norm_lang(code), max_age=365 * 24 * 3600,
                        httponly=True, samesite="lax")
        return resp

    # --- demo data --------------------------------------------------------------

    @r.post("/demo/load")
    def demo_load():
        try:
            rt.load_demo()
            say("ok", "d_demo_loaded")
        except Exception as exc:  # noqa: BLE001 - show the reason, keep the app up
            rt.store.update(demo=False)
            rt.reload()
            say("err", "d_err_generic", msg=str(exc)[:300])
        return go("/")

    @r.post("/demo/clear")
    def demo_clear():
        try:
            rt.clear_demo()
            say("ok", "d_demo_cleared")
        except Exception as exc:  # noqa: BLE001
            say("err", "d_err_generic", msg=str(exc)[:300])
        return go("/")

    # --- farmers & fields -------------------------------------------------------

    def farmer_rows(session, q: str = "") -> list[Farmer]:
        farmers = session.scalars(select(Farmer).order_by(func.lower(Farmer.name))).all()
        q = (q or "").strip().lower()
        if q:
            digits = re.sub(r"\D", "", q)
            farmers = [f for f in farmers if q in f.name.lower()
                       or (digits and digits in f.phone)]
        return farmers

    def map_center(session, farmer: Farmer | None) -> tuple[float, float]:
        query = select(Field).order_by(Field.created_at.desc())
        fld = None
        if farmer is not None:
            fld = session.scalars(query.where(Field.farmer_id == farmer.id)).first()
        fld = fld or session.scalars(query).first()
        if fld is None:
            return DEFAULT_CENTER
        ring = json.loads(fld.boundary_geojson)["coordinates"]
        while isinstance(ring[0][0], list):
            ring = ring[0]
        return (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))

    @r.get("/farmers", response_class=HTMLResponse)
    def farmers_page(request: Request, id: str = "", q: str = ""):
        with rt.Session() as session:
            farmer = session.get(Farmer, id) if id else None
            fields = []
            if farmer is not None:
                for fld in session.scalars(select(Field).where(Field.farmer_id == farmer.id)
                                           .order_by(Field.name)):
                    fields.append({"field": fld, "svg": Markup(field_map(fld)),
                                   "geojson": fld.boundary_geojson})
            return render(request, "dash/farmers.html", nav="farmers",
                          farmers=farmer_rows(session, q), q=q, farmer=farmer, fields=fields,
                          center=map_center(session, farmer),
                          total=session.scalar(select(func.count()).select_from(Farmer)))

    @r.get("/farmers/list", response_class=HTMLResponse)
    def farmers_list(request: Request, q: str = "", id: str = ""):
        with rt.Session() as session:
            return render(request, "dash/_farmer_list.html", farmers=farmer_rows(session, q),
                          farmer=session.get(Farmer, id) if id else None, q=q)

    def _farmer_form(session, farmer: Farmer | None, name: str, phone: str,
                     language: str) -> Farmer | None:
        name = name.strip()
        if not name:
            say("err", "d_err_required", what=t("en", "d_name"))
            return None
        try:
            phone = normalize_phone(phone)
        except ValueError:
            say("err", "d_err_phone")
            return None
        other = session.scalar(select(Farmer).where(Farmer.phone == phone))
        if other is not None and (farmer is None or other.id != farmer.id):
            say("err", "d_err_phone_used")
            return None
        if farmer is None:
            farmer = Farmer(name=name[:200], phone=phone, language=norm_lang(language))
            session.add(farmer)
        else:
            farmer.name, farmer.phone, farmer.language = name[:200], phone, norm_lang(language)
        session.commit()
        say("ok", "d_saved")
        return farmer

    @r.post("/farmers")
    def farmer_create(name: str = Form(""), phone: str = Form(""), language: str = Form("uz")):
        with rt.Session() as session:
            farmer = _farmer_form(session, None, name, phone, language)
            return go(f"/farmers?id={farmer.id}" if farmer else "/farmers")

    @r.post("/farmers/{farmer_id}")
    def farmer_update(farmer_id: str, name: str = Form(""), phone: str = Form(""),
                      language: str = Form("uz")):
        with rt.Session() as session:
            farmer = session.get(Farmer, farmer_id)
            if farmer is None:
                return missing()
            _farmer_form(session, farmer, name, phone, language)
            return go(f"/farmers?id={farmer_id}")

    def _boundary_from_form(boundary_file: UploadFile | None, drawn: str):
        """Polygon from the upload or the drawn corners; None if neither was given."""
        if boundary_file is not None and boundary_file.filename:
            return boundary_from_upload(boundary_file.filename, boundary_file.file.read(
                6 * 1024 * 1024))
        if drawn.strip():
            try:
                points = json.loads(drawn)
            except ValueError:
                raise IngestError("drawn outline is not readable") from None
            if points:
                return boundary_from_points(points)
        return None

    @r.post("/farmers/{farmer_id}/fields")
    def field_create(farmer_id: str, name: str = Form(""), crop: str = Form(""),
                     drawn: str = Form(""), boundary_file: UploadFile | None = File(None)):
        with rt.Session() as session:
            farmer = session.get(Farmer, farmer_id)
            if farmer is None:
                return missing()
            back = go(f"/farmers?id={farmer_id}")
            if not name.strip():
                say("err", "d_err_required", what=t("en", "d_field_name"))
                return back
            try:
                poly = _boundary_from_form(boundary_file, drawn)
            except IngestError as exc:
                say("err", "d_err_boundary", msg=str(exc))
                return back
            if poly is None:
                say("err", "d_err_no_boundary")
                return back
            session.add(Field(farmer_id=farmer.id, name=name.strip()[:200],
                              crop=crop.strip()[:100], boundary_geojson=boundary_text(poly),
                              area_ha=round(area_ha(poly), 4)))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                say("err", "d_err_field_name")
                return back
            say("ok", "d_saved")
            return back

    @r.post("/fields/{field_id}")
    def field_update(field_id: str, name: str = Form(""), crop: str = Form(""),
                     drawn: str = Form(""), boundary_file: UploadFile | None = File(None)):
        with rt.Session() as session:
            fld = session.get(Field, field_id)
            if fld is None:
                return missing()
            back = go(f"/farmers?id={fld.farmer_id}")
            if not name.strip():
                say("err", "d_err_required", what=t("en", "d_field_name"))
                return back
            try:
                poly = _boundary_from_form(boundary_file, drawn)
            except IngestError as exc:
                say("err", "d_err_boundary", msg=str(exc))
                return back
            fld.name, fld.crop = name.strip()[:200], crop.strip()[:100]
            if poly is not None:
                # signed records keep the boundary they were computed with;
                # only drafts and future jobs use the new one
                fld.boundary_geojson = boundary_text(poly)
                fld.area_ha = round(area_ha(poly), 4)
                for draft in session.scalars(select(Draft).where(Draft.field_id == fld.id)):
                    draft.preview = ""
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                say("err", "d_err_field_name")
                return back
            say("ok", "d_saved")
            return back

    @r.get("/tiles/{z}/{x}/{y}.png")
    def tile(z: int, x: int, y: int):
        if not (0 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
            return Response(status_code=404)
        path = rt.root / "tiles" / str(z) / str(x) / f"{y}.png"
        if path.is_file():
            data = path.read_bytes()
        else:
            data = (getattr(rt, "tile_fetcher", None) or fetch_osm_tile)(z, x, y)
            if data is None:
                return Response(status_code=204)      # offline and never viewed: blank
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return Response(data, media_type="image/png",
                        headers={"Cache-Control": "max-age=604800"})

    # --- new job wizard ---------------------------------------------------------

    def fields_of(session, farmer_id: str) -> list[Field]:
        return session.scalars(select(Field).where(Field.farmer_id == farmer_id)
                               .order_by(Field.name)).all()

    @r.get("/jobs/new", response_class=HTMLResponse)
    def wizard_start(request: Request, farmer: str = ""):
        with rt.Session() as session:
            farmers = [f for f in farmer_rows(session) if fields_of(session, f.id)]
            chosen = farmer if any(f.id == farmer for f in farmers) else ""
            return render(request, "dash/wizard1.html", nav="jobs", step=1, farmers=farmers,
                          chosen=chosen, fields=fields_of(session, chosen) if chosen else [])

    @r.get("/jobs/new/fields", response_class=HTMLResponse)
    def wizard_fields(request: Request, farmer_id: str = ""):
        with rt.Session() as session:
            return render(request, "dash/_field_options.html",
                          fields=fields_of(session, farmer_id) if farmer_id else [])

    @r.post("/jobs/new")
    def wizard_create(farmer_id: str = Form(""), field_id: str = Form("")):
        with rt.Session() as session:
            fld = session.get(Field, field_id) if field_id else None
            if fld is None or fld.farmer_id != farmer_id:
                say("err", "d_err_required", what=f"{t('en', 'farmer')}, {t('en', 'field')}")
                return go("/jobs/new")
            last = session.scalars(select(RecordVersion)
                                   .order_by(RecordVersion.created_at.desc())).first()
            prev = json.loads(last.payload)["job"] if last is not None else {}
            details = {"crop": fld.crop, "product": "", "batch": "",
                       "planned_rate_l_ha": prev.get("planned_rate_l_ha"),
                       "swath_m": rt.store["swath_m"], "pilot": prev.get("pilot", ""),
                       "drone_id": prev.get("drone_id", ""), "weather": {},
                       "tank_litres_used": None}
            draft = Draft(farmer_id=farmer_id, field_id=fld.id, sample_data=rt.demo,
                          details=json.dumps(details, ensure_ascii=False))
            session.add(draft)
            session.commit()
            return go(f"/drafts/{draft.id}/details")

    @r.get("/drafts/{draft_id}/details", response_class=HTMLResponse)
    def wizard_details(request: Request, draft_id: str):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            if draft is None:
                return missing()
            return render(request, "dash/wizard2.html", nav="jobs", step=2, draft=draft,
                          d=J.draft_details(draft))

    @r.post("/drafts/{draft_id}/details")
    def wizard_details_save(
            draft_id: str, crop: str = Form(""), product: str = Form(""), batch: str = Form(""),
            planned_rate_l_ha: str = Form(""), swath_m: str = Form(""), pilot: str = Form(""),
            drone_id: str = Form(""), temperature_c: str = Form(""),
            humidity_pct: str = Form(""), wind_ms: str = Form(""),
            wind_direction: str = Form(""), tank_litres_used: str = Form("")):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            if draft is None:
                return missing()
            back = go(f"/drafts/{draft_id}/details")
            raw = {"planned_rate_l_ha": planned_rate_l_ha, "swath_m": swath_m,
                   "temperature_c": temperature_c, "humidity_pct": humidity_pct,
                   "wind_ms": wind_ms, "tank_litres_used": tank_litres_used}
            numbers, bad = {}, None
            for key, value in raw.items():
                try:
                    numbers[key] = _number(value)
                except ValueError:
                    numbers[key], bad = None, bad or value
            draft.details = json.dumps({
                "crop": crop.strip(), "product": product.strip(), "batch": batch.strip(),
                "planned_rate_l_ha": numbers["planned_rate_l_ha"],
                "swath_m": numbers["swath_m"], "pilot": pilot.strip(),
                "drone_id": drone_id.strip(),
                "weather": {"temperature_c": numbers["temperature_c"],
                            "humidity_pct": numbers["humidity_pct"],
                            "wind_ms": numbers["wind_ms"],
                            "wind_direction": wind_direction.strip() or None},
                "tank_litres_used": numbers["tank_litres_used"]}, ensure_ascii=False)
            draft.preview = ""
            session.commit()          # keep what was typed, even if it is not valid yet
            if bad is not None:
                say("err", "d_err_number", what=bad)
                return back
            try:
                J.job_in_for(draft, rt.store["sprayer"])
            except ValidationError as exc:
                first = exc.errors()[0]
                say("err", "d_err_required",
                    what=f"{'.'.join(str(p) for p in first['loc'])} ({first['msg']})")
                return back
            return go(f"/drafts/{draft_id}/" + ("results" if draft.log_path else "flight"))

    def live_ctx(draft_id: str) -> dict:
        status = rt.capture.status()
        mine = status["draft_id"] == draft_id
        return {"live": status if mine else {**status, "state": IDLE},
                "live_busy": rt.capture.active and not mine, "IDLE": IDLE, "ERROR": ERROR}

    @r.get("/drafts/{draft_id}/flight", response_class=HTMLResponse)
    def wizard_flight(request: Request, draft_id: str):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            if draft is None:
                return missing()
            return render(request, "dash/wizard3.html", nav="jobs", step=3, draft=draft,
                          draft_id=draft_id, connection=rt.store["drone_connection"],
                          **live_ctx(draft_id))

    def _attach_log(session, draft: Draft, path: Path, name: str) -> RedirectResponse:
        """Use `path` as the draft's flight data and compute its results."""
        draft.log_path, draft.log_name = str(path), name
        try:
            J.compute_preview(rt, draft)
        except (IngestError, ValidationError, ValueError) as exc:
            draft.log_path = draft.log_name = draft.preview = ""
            session.commit()
            shutil.rmtree(J.upload_dir(rt.settings, draft.id), ignore_errors=True)
            say("err", "d_err_log", msg=str(exc)[:300])
            return go(f"/drafts/{draft.id}/flight")
        session.commit()
        return go(f"/drafts/{draft.id}/results")

    @r.post("/drafts/{draft_id}/upload")
    def wizard_upload(draft_id: str, log: UploadFile | None = File(None)):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            if draft is None:
                return missing()
            if log is None or not log.filename or not log.filename.lower().endswith(".bin"):
                say("err", "d_err_log_type")
                return go(f"/drafts/{draft_id}/flight")
            folder = J.upload_dir(rt.settings, draft.id)
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
            path = folder / _safe_name(log.filename)
            with open(path, "wb") as out:
                shutil.copyfileobj(log.file, out, length=1024 * 1024)
            if path.stat().st_size > MAX_LOG_BYTES:
                shutil.rmtree(folder, ignore_errors=True)
                say("err", "d_err_log", msg="the file is larger than 600 MB")
                return go(f"/drafts/{draft_id}/flight")
            return _attach_log(session, draft, path, Path(log.filename).name[:200])

    @r.post("/drafts/{draft_id}/live/start")
    def live_start(draft_id: str, mode: str = Form("drone")):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            if draft is None:
                return missing()
            if rt.capture.active and rt.capture.draft_id != draft_id:
                say("err", "d_live_busy")
                return go(f"/drafts/{draft_id}/flight")
            folder = J.upload_dir(rt.settings, draft.id)
            shutil.rmtree(folder, ignore_errors=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            sim_args = None
            if mode == "sim":      # fly the simulator over this field's bounding box
                west, south, east, north = boundary_of(draft.field).bounds
                width_m = (east - west) * 111_320.0 * math.cos(math.radians(south))
                length_m = (north - south) * 111_320.0
                swath = J.draft_details(draft).get("swath_m") or rt.store["swath_m"]
                sim_args = ["--area", f"{width_m * length_m / 10_000.0:.4f}",
                            "--width", f"{width_m:.2f}", "--swath", f"{float(swath):.2f}",
                            "--origin-lat", f"{south:.7f}", "--origin-lon", f"{west:.7f}"]
            try:
                rt.capture.start(draft_id, folder / f"live-{stamp}.ndjson",
                                 rt.store["drone_connection"], simulate=(mode == "sim"),
                                 speedup=getattr(rt, "sim_speedup", 40.0), sim_args=sim_args)
            except OSError as exc:
                say("err", "d_live_error", msg=str(exc)[:200])
            return go(f"/drafts/{draft_id}/flight")

    @r.get("/drafts/{draft_id}/live/status", response_class=HTMLResponse)
    def live_status(request: Request, draft_id: str):
        return render(request, "dash/_live.html", draft_id=draft_id, **live_ctx(draft_id))

    @r.post("/drafts/{draft_id}/live/finish")
    def live_finish(draft_id: str):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            if draft is None or rt.capture.draft_id != draft_id:
                return missing() if draft is None else go(f"/drafts/{draft_id}/flight")
            simulated = rt.capture.simulated
            path, fixes = rt.capture.finish()
            if not fixes or path is None:
                say("err", "d_live_none")
                return go(f"/drafts/{draft_id}/flight")
            draft.sample_data = draft.sample_data or simulated
            return _attach_log(session, draft, path, path.name)

    @r.post("/drafts/{draft_id}/live/cancel")
    def live_cancel(draft_id: str):
        if rt.capture.draft_id == draft_id:
            rt.capture.cancel()
        return go(f"/drafts/{draft_id}/flight")

    def _preview(session, draft: Draft) -> dict | None:
        if not draft.log_path:
            return None
        if draft.preview:
            return json.loads(draft.preview)
        try:
            payload = J.compute_preview(rt, draft)
        except (IngestError, ValidationError, ValueError) as exc:
            say("err", "d_err_log", msg=str(exc)[:300])
            return None
        session.commit()
        return payload

    @r.get("/drafts/{draft_id}/results", response_class=HTMLResponse)
    def wizard_results(request: Request, draft_id: str):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            if draft is None:
                return missing()
            payload = _preview(session, draft)
            if payload is None:
                return go(f"/drafts/{draft_id}/flight")
            return render(request, "dash/wizard4.html", nav="jobs", step=4, draft=draft,
                          payload=payload, res=payload["results"],
                          svg=Markup(payload_map(payload)))

    @r.get("/drafts/{draft_id}/preview.pdf")
    def wizard_pdf(request: Request, draft_id: str, lang: str = ""):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            payload = _preview(session, draft) if draft is not None else None
            if payload is None:
                return missing()
            pdf = render_pdf(payload, "DRAFT - NOT SIGNED", rt.local_url,
                             norm_lang(lang or draft.farmer.language), draft=True)
            return Response(pdf, media_type="application/pdf", headers={
                "Content-Disposition": 'inline; filename="nomad-atlas-draft.pdf"'})

    @r.post("/drafts/{draft_id}/sign")
    def wizard_sign(draft_id: str):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            if draft is None:
                return missing()
            if not draft.log_path:
                return go(f"/drafts/{draft_id}/flight")
            try:
                record = J.sign_draft(rt, session, draft)
            except (IngestError, ValidationError, ValueError) as exc:
                session.rollback()
                say("err", "d_err_log", msg=str(exc)[:300])
                return go(f"/drafts/{draft_id}/results")
            session.commit()
            say("ok", "d_signed_ok")
            return go(f"/jobs/{record.job_id}")

    @r.post("/drafts/{draft_id}/delete")
    def draft_delete(draft_id: str):
        with rt.Session() as session:
            draft = session.get(Draft, draft_id)
            if draft is not None:
                if rt.capture.draft_id == draft_id:
                    rt.capture.cancel()
                J.discard_draft(rt, session, draft)
                session.commit()
        return go("/")

    # --- jobs -------------------------------------------------------------------

    def jobs_ctx(session, q, state, crop, date_from, date_to) -> dict:
        views = J.list_jobs(rt, session)
        crops = sorted({v["details"].get("crop") for v in views if v["details"].get("crop")})
        return {"rows": J.filter_jobs(views, q, state, crop, date_from, date_to),
                "crops": crops, "q": q, "state": state, "crop": crop,
                "date_from": date_from, "date_to": date_to, "states": J.STATES,
                "total": len(views)}

    @r.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request, q: str = "", state: str = "", crop: str = "",
                  date_from: str = "", date_to: str = ""):
        with rt.Session() as session:
            return render(request, "dash/jobs.html", nav="jobs", drafts=drafts_of(session),
                          **jobs_ctx(session, q, state, crop, date_from, date_to))

    @r.get("/jobs/table", response_class=HTMLResponse)
    def jobs_table(request: Request, q: str = "", state: str = "", crop: str = "",
                   date_from: str = "", date_to: str = ""):
        with rt.Session() as session:
            return render(request, "dash/_jobs_table.html",
                          **jobs_ctx(session, q, state, crop, date_from, date_to))

    @r.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str):
        with rt.Session() as session:
            job = session.get(Job, job_id)
            if job is None or not job.versions:
                return missing()
            view = J.job_detail(rt, session, job)
            ok = view["check"].ok
            links = [{"url": share_url(rt.settings, row["share"])} for row in view["owner_links"]]
            return render(request, "dash/job.html", nav="jobs", v=view, ok=ok,
                          svg=Markup(payload_map(view["payload"])) if ok else "",
                          owner_links=links, amendable=AMENDABLE)

    @r.get("/jobs/{job_id}/report.pdf")
    def job_pdf(request: Request, job_id: str, lang: str = ""):
        with rt.Session() as session:
            job = session.get(Job, job_id)
            if job is None or not job.versions:
                return missing()
            record = latest_version(session, job.id)
            if not verify_record(session, record).ok:
                return Response("This record failed verification, so no report is produced.",
                                status_code=409, media_type="text/plain")
            lang = norm_lang(lang or job.farmer.language)
            share = owner_share(session, record)
            session.commit()
            return Response(report_pdf(rt.settings, record, share, lang),
                            media_type="application/pdf", headers={
                "Content-Disposition": f'inline; filename="{report_filename(record, lang)}"'})

    @r.post("/jobs/{job_id}/amend")
    def job_amend(job_id: str, crop: str = Form(""), product: str = Form(""),
                  batch: str = Form(""), pilot: str = Form(""), drone_id: str = Form(""),
                  reason: str = Form("")):
        with rt.Session() as session:
            job = session.get(Job, job_id)
            if job is None or not job.versions:
                return missing()
            record = latest_version(session, job.id)
            back = go(f"/jobs/{job_id}")
            if not verify_record(session, record).ok:
                say("err", "d_sig_bad")
                return back
            current = json.loads(record.payload)["job"]
            typed = {"crop": crop, "product": product, "batch": batch, "pilot": pilot,
                     "drone_id": drone_id}
            changes = {k: v.strip() for k, v in typed.items()
                       if v.strip() != (current.get(k) or "") and (v.strip() or k == "batch")}
            if not changes or not reason.strip():
                say("err", "d_err_required", what=t("en", "d_reason"))
                return back
            new = amend_record(session, rt.signer, record, changes, reason)
            owner_share(session, new)
            session.commit()
            say("ok", "d_version_created", v=new.version)
            return back

    @r.post("/shares/{share_id}/revoke")
    def share_revoke(share_id: str):
        with rt.Session() as session:
            share = session.get(Share, share_id)
            if share is None:
                return missing()
            job_id = session.get(RecordVersion, share.record_id).job_id
            if revoke_share(session, share, actor="operator"):
                say("ok", "d_share_revoked")
            session.commit()
            return go(f"/jobs/{job_id}")

    # --- settings ---------------------------------------------------------------

    @r.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return render(request, "dash/settings.html", nav="settings", s=rt.store.values,
                      has_token=bool(rt.store["bot_token"]),
                      has_sync=bool(rt.store["sync_token"]), key_id=rt.signer.key_id,
                      public_key=rt.signer.public_b64, kinds=KINDS, **sync_ctx())

    @r.post("/settings")
    def settings_save(
            bot_token: str = Form(""), remove_token: str = Form(""),
            server_url: str = Form(""), sync_token: str = Form(""),
            remove_sync: str = Form(""), swath_m: str = Form(""), kind: str = Form("servo"),
            channel: str = Form("9"), pwm_on: str = Form("1150"), language: str = Form("uz"),
            drone_connection: str = Form("")):
        changes: dict = {}
        try:
            swath = _number(swath_m)
            if swath is None or not 0 < swath <= 50:
                raise ValueError(swath_m)
            changes["swath_m"] = swath
            sprayer = dict(rt.store["sprayer"])
            sprayer.update(kind=kind if kind in KINDS else "servo",
                           channel=int(_number(channel) or 9), pwm_on=int(_number(pwm_on) or 1150))
            if not 1 <= sprayer["channel"] <= 16:
                raise ValueError(channel)
            changes["sprayer"] = sprayer
        except ValueError as exc:
            say("err", "d_err_number", what=str(exc))
            return go("/settings")
        server_url = server_url.strip().rstrip("/")
        if server_url and not re.match(r"https?://[^\s/]+", server_url):
            say("err", "d_err_generic", msg="the server address must start with https://")
            return go("/settings")
        bot_token = bot_token.strip()
        if bot_token and not re.fullmatch(r"\d+:[\w-]{20,}", bot_token):
            say("err", "d_err_generic", msg="that does not look like a Telegram bot token")
            return go("/settings")
        changes["server_url"] = server_url
        if remove_token:
            changes["bot_token"] = ""
        elif bot_token:
            changes["bot_token"] = bot_token
        if remove_sync:
            changes["sync_token"] = ""
        elif sync_token.strip():
            changes["sync_token"] = sync_token.strip()
        changes["language"] = norm_lang(language)
        changes["drone_connection"] = drone_connection.strip() or "udpin:0.0.0.0:14550"
        rt.store.update(**changes)
        rt.reload()
        say("ok", "d_saved")
        resp = go("/settings")
        resp.set_cookie(LANG_COOKIE, changes["language"], max_age=365 * 24 * 3600,
                        httponly=True, samesite="lax")
        return resp

    # --- stop -------------------------------------------------------------------

    @r.post("/api/shutdown")
    def shutdown(x_atlas_token: str = Header(default="")):
        """Used by "Stop Atlas": only someone who can read the data folder can call it."""
        if not rt.shutdown_token or not hmac.compare_digest(
                x_atlas_token.encode(), rt.shutdown_token.encode()):
            return Response(status_code=403)
        if rt.on_shutdown is not None:
            rt.on_shutdown()
        return {"ok": True}

    return r

