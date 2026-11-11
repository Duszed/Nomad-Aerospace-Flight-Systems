"""Step 7 - VERIFY: the FastAPI verify server.

Opening a QR code or share link shows the record, the map and one status:

    Verified   the record's hash was recomputed and its Ed25519 signature checks
    Modified   the stored record no longer matches what was signed
    Revoked    the farmer withdrew this share (nothing about the record is shown)
    Expired    the share's lifetime ran out (nothing is shown)

A record is reachable only through a share token. There is no listing, no
search and no way to get from one record to another. Pages load nothing
from other sites: no map tiles, fonts, scripts or analytics.
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .. import SAMPLE_DATA_NOTICE, __version__
from ..config import Settings
from ..db import Confirmation, RecordVersion, Share, SigningKey, utcnow
from ..i18n import (
    LANGUAGE_NAMES, LANGUAGES, fmt_dt, fmt_num, fmt_signed_pct, norm_lang,
    sprayer_source_text, t,
)
from ..records import MODIFIED, VERIFIED, Verification, latest_version, verify_record
from ..records.signing import ALGORITHM
from ..report import render_map_png
from ..shares import (
    ACTIVE, EXPIRED, REVOKED, find_share, log_event, report_filename, report_pdf, share_state,
)
from ..sync import SyncError, receive_bundle

NOT_FOUND = "not_found"
HTTP_STATUS = {VERIFIED: 200, MODIFIED: 200, REVOKED: 410, EXPIRED: 410, NOT_FOUND: 404,
               "home": 200}

# The verify side: reachable by anyone holding a share link.
PUBLIC_PREFIXES = ("/v/", "/api/verify/", "/api/sync/", "/.well-known/", "/healthz")
# The dashboard answers only to a browser on this same computer.
LOCAL_CLIENTS = {"127.0.0.1", "::1"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
VERIFY_CSP = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
              "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
DASHBOARD_CSP = ("default-src 'self'; img-src 'self' data: blob:; "
                 "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
                 "form-action 'self'; frame-ancestors 'none'")
STATIC_DIR = Path(__file__).parent / "static"

log = logging.getLogger("atlas.web")

_templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]))


@dataclass
class Resolution:
    status: str
    share: Share | None = None
    record: RecordVersion | None = None
    check: Verification | None = None


def resolve(session: Session, token: str, log: bool) -> Resolution:
    """Work out what a token may show. With `log`, record the access."""
    share = find_share(session, token)
    if share is None:
        return Resolution(NOT_FOUND)
    state = share_state(share)
    if state != ACTIVE:
        if log:
            log_event(session, share, "refused", state)
            session.commit()
        return Resolution(REVOKED if state == REVOKED else EXPIRED, share=share)
    record = session.get(RecordVersion, share.record_id)
    check = verify_record(session, record)
    if log:
        log_event(session, share, "opened", check.status)
        session.commit()
    return Resolution(check.status, share=share, record=record, check=check)


class _Fixed:
    """Settings and database that never change (the server, the tests)."""

    def __init__(self, settings: Settings, Session: sessionmaker) -> None:
        self.settings, self.Session = settings, Session


def _is_public(path: str) -> bool:
    return path.startswith(PUBLIC_PREFIXES)


def _local_only(request: Request) -> str | None:
    """Why a dashboard request is refused, or None if it may proceed."""
    client = request.client.host if request.client else ""
    if client not in LOCAL_CLIENTS:
        return "the dashboard only answers on the computer it runs on"
    host = urlsplit("//" + request.headers.get("host", "")).hostname or ""
    if host not in LOCAL_HOSTS:
        return "the dashboard must be opened as http://localhost"
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # A change must come from the dashboard's own pages, never from
        # another website open in the same browser.
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        if site is not None:
            if site not in ("same-origin", "none"):
                return "requests from other websites are refused"
        elif origin is not None and (urlsplit(origin).hostname or "") not in LOCAL_HOSTS:
            return "requests from other websites are refused"
    return None


def create_app(settings: Settings | None = None, Session: sessionmaker | None = None,
               runtime=None) -> FastAPI:
    """The verify server. With `runtime`, also the laptop dashboard.

    `runtime` (atlas.dashboard.Runtime) supplies the settings and database,
    which change when demo data is loaded or cleared. The dashboard routes
    exist only then, and only answer to this computer; the public server is
    always created without it.
    """
    app = FastAPI(title="Nomad Atlas", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)
    state = runtime if runtime is not None else _Fixed(settings, Session)

    @app.middleware("http")
    async def privacy_headers(request: Request, call_next):
        path = request.url.path
        dashboard = runtime is not None and not _is_public(path)
        if dashboard:
            refused = _local_only(request)
            if refused:
                return PlainTextResponse(f"Refused: {refused}.", status_code=403)
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if not dashboard:
            response.headers["Referrer-Policy"] = "no-referrer"  # tokens stay out of referrers
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = VERIFY_CSP
        else:
            response.headers["Referrer-Policy"] = "same-origin"  # nothing leaves this laptop
            if path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache"   # revalidate, never stale
            elif not path.startswith("/tiles/"):
                response.headers["Cache-Control"] = "no-store"
            if response.headers.get("content-type", "").startswith("text/html"):
                response.headers["Content-Security-Policy"] = DASHBOARD_CSP
        return response

    if runtime is not None:
        from ..dashboard.views import build_router
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        app.include_router(build_router(runtime, _templates))

        @app.exception_handler(Exception)
        async def plain_error(request: Request, exc: Exception):
            log.exception("dashboard error on %s", request.url.path)
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>Nomad Atlas</title>"
                "<body style='background:#070B14;color:#F3F6FA;font:18px sans-serif;"
                "padding:48px;max-width:720px'><h1>Something went wrong</h1>"
                "<p>Atlas could not finish that step. Your data is safe. "
                "Go back and try again; if it keeps happening, close Atlas and start it again."
                "</p><p><a style='color:#CFFF3E' href='/'>Back to Home</a></p></body>",
                status_code=500)

    def page(lang: str, status: str, **ctx) -> HTMLResponse:
        html = _templates.get_template("verify.html").render(
            lang=lang, status=status, t=lambda text_key, /, **kw: t(lang, text_key, **kw),
            languages=LANGUAGE_NAMES, sample_notice_en=SAMPLE_DATA_NOTICE,
            checked=fmt_dt(utcnow()), **ctx)
        return HTMLResponse(html, status_code=HTTP_STATUS[status])

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "service": "nomad-atlas", "version": __version__}

    if runtime is None:
        @app.get("/", response_class=HTMLResponse)
        def home(lang: str = "uz"):
            return page(norm_lang(lang), "home", token=None)

    @app.get("/v/{token}", response_class=HTMLResponse)
    def verify_page(token: str, lang: str | None = None):
        with state.Session() as session:
            r = resolve(session, token, log=True)
            if r.status == NOT_FOUND:
                return page(norm_lang(lang), NOT_FOUND, token=token)
            if r.status in (REVOKED, EXPIRED):
                return page(norm_lang(lang), r.status, token=token)

            payload = r.check.payload
            lang = norm_lang(lang or "uz")
            ctx = dict(token=token, record_id=r.record.id, version=r.record.version,
                       stored_sha256=r.record.payload_sha256,
                       recomputed_sha256=r.check.recomputed_sha256,
                       key_id=r.record.key_id, problems=r.check.problems)
            if r.status == MODIFIED:
                return page(lang, MODIFIED, **ctx)

            newest = latest_version(session, r.record.job_id)
            confirmation = session.get(Confirmation, r.record.id)
            res, job = payload["results"], payload["job"]
            w = payload.get("weather") or {}
            weather = ", ".join(x for x in (
                f"{fmt_num(w['temperature_c'], 0, lang)} °C" if w.get("temperature_c") is not None else "",
                (f"{t(lang, 'wind')} {fmt_num(w['wind_ms'], 1, lang)} {t(lang, 'u_ms')} "
                 f"{w.get('wind_direction') or ''}").strip() if w.get("wind_ms") is not None else "",
                f"{t(lang, 'humidity')} {fmt_num(w['humidity_pct'], 0, lang)}%" if w.get("humidity_pct") is not None else "",
            ) if x) or "—"
            ha, litre, l_ha = t(lang, "u_ha"), t(lang, "u_l"), t(lang, "u_l_ha")
            numbers = [
                (t(lang, "field_area"), f"{fmt_num(res['field_area_ha'], 2, lang)} {ha}", "", ""),
                (t(lang, "sprayed_area"), f"{fmt_num(res['covered_in_field_ha'], 2, lang)} {ha}", "", "sprayed"),
                (t(lang, "missed_area"), f"{fmt_num(res['missed_area_ha'], 3, lang)} {ha}",
                 t(lang, "missed_strips", n=res["missed_strips"]) if res["missed_strips"] else "", "missed"),
                (t(lang, "overlap_area"), f"{fmt_num(res['overlap_area_ha'], 3, lang)} {ha}", "", "overlap"),
                (t(lang, "outside_area"), f"{fmt_num(res['outside_field_ha'], 3, lang)} {ha}", "", ""),
                (t(lang, "litres_applied"), f"{fmt_num(res['litres_applied'], 1, lang)} {litre}",
                 t(lang, f"basis_{res['litres_basis']}"), ""),
                (t(lang, "planned_rate"), f"{fmt_num(res['planned_rate_l_ha'], 1, lang)} {l_ha}", "", ""),
                (t(lang, "actual_rate"), f"{fmt_num(res['actual_rate_l_ha'], 1, lang)} {l_ha}",
                 t(lang, "vs_plan", pct=fmt_signed_pct(res["rate_deviation_pct"], lang)), ""),
            ]
            details = [
                (t(lang, "farmer"), payload["farmer"]["name"]),
                (t(lang, "field"), payload["field"]["name"]),
                (t(lang, "crop"), job["crop"]),
                (t(lang, "product"), job["product"]),
                (t(lang, "batch"), job["batch"] or "—"),
                (t(lang, "swath"), f"{fmt_num(job['swath_m'], 1, lang)} {t(lang, 'u_m')}"),
                (t(lang, "pilot"), job["pilot"]),
                (t(lang, "drone_id"), job["drone_id"]),
                (t(lang, "started"), fmt_dt(job["started_utc"])),
                (t(lang, "ended"), fmt_dt(job["ended_utc"])),
                (t(lang, "weather"), weather),
                (t(lang, "sprayer_source"), sprayer_source_text(payload, lang)),
            ]
            ctx.update(
                payload=payload, numbers=numbers, details=details,
                coverage=fmt_num(res["coverage_pct"], 1, lang),
                estimated=res["litres_basis"] == "estimated",
                recipient=t(lang, f"rcp_{r.share.recipient_type}"),
                is_owner_link=r.share.recipient_type == "owner",
                expires=fmt_dt(r.share.expires_at, with_time=False) if r.share.expires_at else None,
                newer_version=newest.version if newest.id != r.record.id else None,
                confirmed=fmt_dt(confirmation.confirmed_at) if confirmation else None,
                created=fmt_dt(payload["created_utc"]),
                log_sha256=payload["input"]["log_sha256"])
            return page(lang, VERIFIED, **ctx)

    def _active_verified(session: Session, token: str) -> Resolution:
        """For downloads: only a live share of an intact record is served."""
        r = resolve(session, token, log=False)
        if r.status == NOT_FOUND:
            raise HTTPException(404, "no record is shared under this link")
        if r.status in (REVOKED, EXPIRED):
            raise HTTPException(410, f"this share is {r.status}")
        if r.status != VERIFIED:
            raise HTTPException(409, "this record failed verification")
        return r

    @app.get("/v/{token}/map.png")
    def map_png(token: str, lang: str = "uz"):
        with state.Session() as session:
            r = _active_verified(session, token)
            return Response(render_map_png(r.check.payload, norm_lang(lang), dpi=160),
                            media_type="image/png")

    @app.get("/v/{token}/report.pdf")
    def report(token: str, lang: str = "uz"):
        with state.Session() as session:
            r = _active_verified(session, token)
            lang = norm_lang(lang)
            pdf = report_pdf(state.settings, r.record, r.share, lang)
            name = report_filename(r.record, lang)
            return Response(pdf, media_type="application/pdf", headers={
                "Content-Disposition": f'inline; filename="{name}"'})

    @app.get("/v/{token}/record.json")
    def record_json(token: str):
        """Everything needed to check the signature without trusting this page."""
        with state.Session() as session:
            r = _active_verified(session, token)
            key = session.get(SigningKey, r.record.key_id)
            return JSONResponse({
                "payload": r.record.payload,           # canonical JSON, exactly as signed
                "sha256": r.record.payload_sha256,
                "signature": r.record.signature,
                "algorithm": ALGORITHM,
                "key_id": r.record.key_id,
                "public_key": key.public_key,
                "how_to_verify": "Ed25519-verify `signature` (base64) over the UTF-8 bytes "
                                 "of `payload` with `public_key` (base64, raw 32 bytes). "
                                 "`sha256` is the SHA-256 of the same bytes.",
            })

    @app.get("/api/verify/{token}")
    def api_verify(token: str):
        with state.Session() as session:
            r = resolve(session, token, log=True)
            body = {"status": r.status, "checked_utc": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
            if r.record is not None:
                body.update(record_id=r.record.id, version=r.record.version,
                            sha256=r.record.payload_sha256,
                            recomputed_sha256=r.check.recomputed_sha256,
                            problems=r.check.problems)
            return JSONResponse(body, status_code=HTTP_STATUS[r.status])

    @app.get("/.well-known/atlas-keys.json")
    def public_keys():
        with state.Session() as session:
            keys = session.scalars(select(SigningKey).order_by(SigningKey.created_at)).all()
            return {"algorithm": ALGORITHM, "keys": [
                {"key_id": k.key_id, "public_key": k.public_key, "label": k.label}
                for k in keys]}

    @app.post("/api/sync/record")
    async def sync_record(request: Request, authorization: str | None = Header(default=None)):
        """Laptop -> server: accept a signed record created offline."""
        settings = state.settings
        if not settings.sync_token or runtime is not None:
            raise HTTPException(403, "sync is not enabled on this server")
        given = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), settings.sync_token.encode()):
            raise HTTPException(401, "bad sync token")
        try:
            bundle = json.loads(await request.body())
        except ValueError:
            raise HTTPException(400, "body is not JSON") from None
        with state.Session() as session:
            try:
                created = receive_bundle(session, bundle)
            except SyncError as exc:
                session.rollback()
                raise HTTPException(422, str(exc)) from None
            session.commit()
        return JSONResponse({"ok": True, "created": created}, status_code=201 if created else 200)

    return app
