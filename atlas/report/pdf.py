"""Step 4 - REPORT: the one-page PDF.

Laid out directly on a ReportLab canvas so it is always exactly one A4 page:
header, coverage map, key numbers, job details, QR code to the verify page.
Fonts are DejaVu Sans (shipped with matplotlib): full Cyrillic, and the
Uzbek Latin letters oʻ / gʻ (U+02BB) and ʼ (U+02BC).
"""

from __future__ import annotations

import io
from pathlib import Path

import matplotlib
import qrcode
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .. import SAMPLE_DATA_NOTICE
from ..i18n import (
    fmt_dt, fmt_num, fmt_signed_pct, norm_lang, sprayer_source_text, t,
)
from .map import COLOR_MISSED, COLOR_OVERLAP, COLOR_SPRAYED, render_map_png

FONT = "AtlasSans"
FONT_BOLD = "AtlasSans-Bold"
INK = HexColor("#1f2428")
MUTED = HexColor("#6a737d")
RULE = HexColor("#d0d7de")
BRAND = HexColor("#14532d")
AMBER_BG = HexColor("#fff4d6")
AMBER_INK = HexColor("#8a5a00")

PAGE_W, PAGE_H = A4
MARGIN = 36.0


def _register_fonts() -> None:
    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    font_dir = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    pdfmetrics.registerFont(TTFont(FONT, str(font_dir / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(font_dir / "DejaVuSans-Bold.ttf")))


def _fit(text: str, font: str, size: float, max_w: float, min_size: float = 5.5):
    """Shrink, then truncate, so `text` fits in `max_w` points."""
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_w:
        size -= 0.25
    if pdfmetrics.stringWidth(text, font, size) > max_w:
        while len(text) > 1 and pdfmetrics.stringWidth(text + "…", font, size) > max_w:
            text = text[:-1]
        text += "…"
    return text, size


def _text(c: Canvas, x: float, y: float, text: str, font: str = FONT, size: float = 9,
          color=INK, max_w: float | None = None, align: str = "left") -> None:
    if max_w is not None:
        text, size = _fit(text, font, size, max_w)
    c.setFont(font, size)
    c.setFillColor(color)
    if align == "right":
        c.drawRightString(x, y, text)
    elif align == "center":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)


def _wrap(text: str, font: str, size: float, max_w: float, max_lines: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if pdfmetrics.stringWidth(trial, font, size) <= max_w:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1], _ = _fit(lines[-1] + " …", font, size, max_w, min_size=size)
    return [ln for ln in lines if ln]


def _weather_text(w: dict, lang: str) -> str:
    parts = []
    if w.get("temperature_c") is not None:
        parts.append(f"{fmt_num(w['temperature_c'], 0, lang)} °C")
    if w.get("wind_ms") is not None:
        wind = f"{t(lang, 'wind')} {fmt_num(w['wind_ms'], 1, lang)} {t(lang, 'u_ms')}"
        if w.get("wind_direction"):
            wind += f" {w['wind_direction']}"
        parts.append(wind)
    if w.get("humidity_pct") is not None:
        parts.append(f"{t(lang, 'humidity')} {fmt_num(w['humidity_pct'], 0, lang)}%")
    return ", ".join(parts) or "—"


def render_pdf(payload: dict, record_sha256: str, verify_url: str, lang: str = "uz",
               share: dict | None = None) -> bytes:
    """Render a record as a one-page PDF.

    `share` (optional) = {"recipient_type": ..., "expires_utc": iso or None}:
    printed on copies made for a third party, whose QR is that share's link.
    """
    lang = norm_lang(lang)
    _register_fonts()
    res, job = payload["results"], payload["job"]
    ha, litre, l_ha = t(lang, "u_ha"), t(lang, "u_l"), t(lang, "u_l_ha")

    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, pageCompression=1)
    c.setTitle(f"{t(lang, 'report_title')} {payload['record_id'][:8]}")
    c.setAuthor("Nomad Aerospace - Nomad Atlas")
    left, right = MARGIN, PAGE_W - MARGIN
    y = PAGE_H - MARGIN

    # --- header --------------------------------------------------------------
    _text(c, left, y - 14, "NOMAD ATLAS", FONT_BOLD, 10, BRAND)
    _text(c, left, y - 36, t(lang, "report_title"), FONT_BOLD, 18, INK, max_w=350)
    ident = f"{t(lang, 'record')} {payload['record_id'][:8].upper()} · {t(lang, 'version')} {payload['version']}"
    _text(c, right, y - 14, ident, FONT, 8.5, MUTED, align="right")
    _text(c, right, y - 27, fmt_dt(payload["created_utc"]), FONT, 8.5, MUTED, align="right")
    y -= 46
    c.setStrokeColor(BRAND)
    c.setLineWidth(1.4)
    c.line(left, y, right, y)
    y -= 8

    # --- sample-data banner --------------------------------------------------
    if payload.get("sample_data"):
        c.setFillColor(AMBER_BG)
        c.rect(left, y - 20, right - left, 20, stroke=0, fill=1)
        local = t(lang, "sample_notice")
        banner = SAMPLE_DATA_NOTICE if lang == "en" else f"{local}  ·  {SAMPLE_DATA_NOTICE}"
        _text(c, (left + right) / 2, y - 13.5, banner, FONT_BOLD, 9, AMBER_INK,
              max_w=right - left - 16, align="center")
        y -= 28

    if payload.get("supersedes_sha256"):
        _text(c, left, y - 9, t(lang, "supersedes", reason=payload.get("amendment_reason") or "—"),
              FONT, 8, MUTED, max_w=right - left)
        y -= 16

    # --- map (left) + key numbers (right) ------------------------------------
    block_top = y
    map_w, map_h = 262.0, 349.0
    png = render_map_png(payload, lang, width_in=map_w / 72 * 1.08, height_in=map_h / 72 * 1.08)
    c.drawImage(ImageReader(io.BytesIO(png)), left, block_top - map_h, map_w, map_h)
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.rect(left, block_top - map_h, map_w, map_h, stroke=1, fill=0)

    col = left + map_w + 20
    col_w = right - col
    ky = block_top - 4
    _text(c, col, ky - 8, t(lang, "key_numbers").upper(), FONT_BOLD, 7.5, MUTED)
    ky -= 20
    _text(c, col, ky - 30, f"{fmt_num(res['coverage_pct'], 1, lang)}%", FONT_BOLD, 34, BRAND)
    _text(c, right, ky - 30, t(lang, "coverage"), FONT, 11, MUTED, align="right")
    ky -= 44

    missed_note = t(lang, "missed_strips", n=res["missed_strips"]) if res["missed_strips"] else ""
    basis = t(lang, f"basis_{res['litres_basis']}")
    rows = [
        (t(lang, "field_area"), f"{fmt_num(res['field_area_ha'], 2, lang)} {ha}", "", None),
        (t(lang, "sprayed_area"), f"{fmt_num(res['covered_in_field_ha'], 2, lang)} {ha}", "",
         COLOR_SPRAYED),
        (t(lang, "missed_area"), f"{fmt_num(res['missed_area_ha'], 3, lang)} {ha}", missed_note,
         COLOR_MISSED),
        (t(lang, "overlap_area"), f"{fmt_num(res['overlap_area_ha'], 3, lang)} {ha}", "",
         COLOR_OVERLAP),
        (t(lang, "outside_area"), f"{fmt_num(res['outside_field_ha'], 3, lang)} {ha}", "", None),
        (t(lang, "litres_applied"), f"{fmt_num(res['litres_applied'], 1, lang)} {litre}", basis,
         None),
        (t(lang, "planned_rate"), f"{fmt_num(res['planned_rate_l_ha'], 1, lang)} {l_ha}", "", None),
        (t(lang, "actual_rate"), f"{fmt_num(res['actual_rate_l_ha'], 1, lang)} {l_ha}",
         t(lang, "vs_plan", pct=fmt_signed_pct(res["rate_deviation_pct"], lang)), None),
    ]
    for label, value, note, swatch in rows:
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(col, ky, right, ky)
        lx = col
        if swatch:
            c.setFillColor(HexColor(swatch))
            c.rect(col, ky - 14.5, 6, 6, stroke=0, fill=1)
            lx = col + 10
        _text(c, lx, ky - 15, label, FONT, 9, INK, max_w=col_w * 0.6 - (lx - col))
        _text(c, right, ky - 15, value, FONT_BOLD, 10, INK, align="right")
        if note:
            _text(c, right, ky - 26, note, FONT, 7.5, MUTED, max_w=col_w, align="right")
            ky -= 33
        else:
            ky -= 24
    c.line(col, ky, right, ky)
    if res["litres_basis"] == "estimated":
        ny = ky - 12
        for line in _wrap(t(lang, "estimated_note"), FONT, 7, col_w, 4):
            _text(c, col, ny, line, FONT, 7, MUTED)
            ny -= 9.5

    y = block_top - map_h - 16

    # --- job details ---------------------------------------------------------
    _text(c, left, y - 8, t(lang, "job_details").upper(), FONT_BOLD, 7.5, MUTED)
    y -= 16
    height = res.get("avg_spray_height_m")
    flight = t(lang, "flight_summary", speed=fmt_num(res["avg_spray_speed_ms"], 1, lang),
               height=fmt_num(height, 1, lang), passes=res["passes"])
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
        (t(lang, "weather"), _weather_text(payload.get("weather") or {}, lang)),
        (t(lang, "flight"), flight),
    ]
    half = (right - left - 20) / 2
    row_h = 17.0
    for i, (label, value) in enumerate(details):
        cx = left + (i % 2) * (half + 20)
        cy = y - (i // 2) * row_h
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(cx, cy, cx + half, cy)
        _text(c, cx, cy - 11.5, label, FONT, 8, MUTED, max_w=half * 0.36)
        _text(c, cx + half, cy - 11.5, str(value), FONT, 8.5, INK, max_w=half * 0.62,
              align="right")
    y -= ((len(details) + 1) // 2) * row_h
    c.line(left, y, left + half, y)
    c.line(left + half + 20, y, right, y)
    y -= 14

    # --- verification block --------------------------------------------------
    qr_size = 112.0
    qr_img = qrcode.make(verify_url, border=1,
                         error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr_buf = io.BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_y = y - qr_size
    c.drawImage(ImageReader(io.BytesIO(qr_buf.getvalue())), left, qr_y, qr_size, qr_size)

    vx = left + qr_size + 16
    vw = right - vx
    vy = y - 4
    _text(c, vx, vy - 10, t(lang, "scan_to_verify"), FONT_BOLD, 11, INK, max_w=vw)
    vy -= 24
    _text(c, vx, vy - 8, verify_url, FONT, 7.5, BRAND, max_w=vw)
    vy -= 18
    if share and share.get("recipient_type") not in (None, "owner"):
        line = f"{t(lang, 'shared_with')}: {t(lang, 'rcp_' + share['recipient_type'])}"
        if share.get("expires_utc"):
            line += " · " + t(lang, "valid_until",
                              date=fmt_dt(share["expires_utc"], with_time=False))
        _text(c, vx, vy - 8, line, FONT_BOLD, 8, INK, max_w=vw)
        vy -= 15
    for label, value in (
        (t(lang, "record_hash"), record_sha256),
        (t(lang, "log_hash"), payload["input"]["log_sha256"]),
    ):
        _text(c, vx, vy - 7, label, FONT, 7, MUTED)
        _text(c, vx, vy - 16, value, FONT, 6.6, INK, max_w=vw)
        vy -= 22
    _text(c, vx, vy - 7,
          f"{t(lang, 'signed_by', key=payload['signer']['key_id'])} · "
          f"{t(lang, 'sprayer_source')}: {sprayer_source_text(payload, lang)}",
          FONT, 7, MUTED, max_w=vw)

    # --- footer --------------------------------------------------------------
    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.line(left, MARGIN + 12, right, MARGIN + 12)
    _text(c, left, MARGIN + 2, t(lang, "footer"), FONT, 6.5, MUTED, max_w=right - left)
    if payload.get("sample_data"):
        c.saveState()
        c.setFillColor(AMBER_INK)
        c.setFillAlpha(0.10)
        c.setFont(FONT_BOLD, 54)
        c.translate(PAGE_W / 2, PAGE_H / 2)
        c.rotate(35)
        c.drawCentredString(0, 0, "SAMPLE DATA")
        c.restoreState()

    c.showPage()
    c.save()
    return buf.getvalue()


def record_summary(payload: dict, lang: str) -> str:
    """The short text summary sent with the PDF on Telegram."""
    res = payload["results"]
    text = t(
        lang, "bot_report_caption",
        field=payload["field"]["name"],
        coverage=fmt_num(res["coverage_pct"], 1, lang),
        sprayed=fmt_num(res["covered_in_field_ha"], 2, lang),
        area=fmt_num(res["field_area_ha"], 2, lang),
        missed=fmt_num(res["missed_area_ha"], 3, lang),
        overlap=fmt_num(res["overlap_area_ha"], 3, lang),
        litres=fmt_num(res["litres_applied"], 1, lang),
        basis=t(lang, f"basis_{res['litres_basis']}"),
        actual=fmt_num(res["actual_rate_l_ha"], 1, lang),
        planned=fmt_num(res["planned_rate_l_ha"], 1, lang),
    )
    if payload.get("sample_data"):
        text = f"⚠️ {t(lang, 'sample_notice')}\n\n{text}"
    return text
