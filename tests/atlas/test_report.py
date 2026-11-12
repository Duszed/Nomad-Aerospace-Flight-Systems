"""Step 4 - REPORT: one-page PDF, coverage map, three languages, QR code."""

from __future__ import annotations

import io
import json
import re
import string
from pathlib import Path

import matplotlib
import pytest
from fontTools.ttLib import TTFont
from PIL import Image
from pypdf import PdfReader

from atlas import SAMPLE_DATA_NOTICE
from atlas.i18n import LANGUAGES, T, fmt_dt, fmt_num, fmt_signed_pct, norm_lang, t
from atlas.pipeline import process_log
from atlas.report import record_summary, render_map_png, render_pdf

URL = "http://testserver/v/abcDEF123tokenXYZ"


@pytest.fixture
def payload(processed):
    return json.loads(processed.record.payload)


def pdf_text(pdf: bytes) -> str:
    return PdfReader(io.BytesIO(pdf)).pages[0].extract_text()


# --- translations ------------------------------------------------------------

def test_every_text_exists_in_all_three_languages():
    for key, entry in T.items():
        assert set(entry) == set(LANGUAGES), key
        assert all(entry[lang].strip() for lang in LANGUAGES), key


def test_placeholders_match_across_languages():
    fields = lambda s: sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)
    for key, entry in T.items():
        assert fields(entry["uz"]) == fields(entry["ru"]) == fields(entry["en"]), key


def test_uzbek_uses_correct_o_and_g_letters():
    """oʻ / gʻ must use U+02BB, the tutuq belgisi U+02BC - no ASCII look-alikes."""
    wrong = ("'", "`", "‘", "’", "ʹ")
    for key, entry in T.items():
        assert not any(ch in entry["uz"] for ch in wrong), key
    all_uz = " ".join(entry["uz"] for entry in T.values())
    assert "oʻ" in all_uz and "gʻ" in all_uz and "ʼ" in all_uz
    assert re.search(r"[oOgG]ʼ", all_uz) is None      # ʼ never follows o/g
    assert "Oʻzbekiston" in all_uz and "Sugʻurta" in all_uz


def test_russian_is_cyrillic_and_english_is_ascii_letters():
    assert re.search(r"[а-яё]", t("ru", "report_title"))
    assert t("en", "report_title") == "Spray Application Record"
    assert t("en", "sample_notice") == SAMPLE_DATA_NOTICE


def test_language_fallback_and_formatting():
    assert norm_lang("uz-Latn") == "uz" and norm_lang("de") == "uz" and norm_lang(None) == "uz"
    assert fmt_num(97.2, 1, "uz") == "97,2" and fmt_num(97.2, 1, "en") == "97.2"
    assert fmt_num(None, 1, "ru") == "—"
    assert fmt_signed_pct(1.8, "en") == "+1.8%" and fmt_signed_pct(-2.0, "ru") == "−2,0%"
    assert fmt_dt("2026-05-14T05:30:00Z") == "14.05.2026 10:30 (UTC+5)"


def test_report_font_has_uzbek_and_cyrillic_glyphs():
    font = TTFont(str(Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"))
    cmap = font.getBestCmap()
    for ch in "ʻʼЁёЖжЩщ–·×":
        assert ord(ch) in cmap, f"font is missing {ch!r}"


# --- map ---------------------------------------------------------------------

def _colour_counts(png: bytes):
    import numpy as np
    px = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    red = int(((r > 200) & (g < 70) & (b < 70)).sum())
    amber = int(((r > 230) & (g > 150) & (g < 185) & (b < 70)).sum())
    green = int(((r < 120) & (g > 140) & (b < 140)).sum())
    return red, amber, green


def test_map_shows_sprayed_missed_in_red_and_overlap_in_amber(payload):
    png = render_map_png(payload, "en", dpi=100)
    assert png.startswith(b"\x89PNG")
    red, amber, green = _colour_counts(png)
    assert green > 10_000
    assert red > 150          # missed strips
    assert amber > 150        # double-sprayed strip (+ legend swatch)


def test_perfect_mission_map_has_no_missed_area(session, settings, signer, job_in,
                                                demo_boundary, perfect_ndjson, payload):
    p = process_log(session, settings, signer, perfect_ndjson, job_in, demo_boundary)
    clean = render_map_png(json.loads(p.record.payload), "en", dpi=100)
    faulty = render_map_png(payload, "en", dpi=100)
    # only the legend swatches remain red / amber
    assert _colour_counts(clean)[0] < _colour_counts(faulty)[0] / 3
    assert _colour_counts(clean)[1] < _colour_counts(faulty)[1] / 3


# --- PDF ---------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGUAGES)
def test_pdf_is_exactly_one_page_in_every_language(payload, processed, lang):
    pdf = render_pdf(payload, processed.record.payload_sha256, URL, lang)
    assert pdf.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    assert [round(float(v)) for v in reader.pages[0].mediabox[2:]] == [595, 842]  # A4


@pytest.mark.parametrize("lang", LANGUAGES)
def test_pdf_contains_numbers_details_and_verification_data(payload, processed, lang):
    text = pdf_text(render_pdf(payload, processed.record.payload_sha256, URL, lang))
    res = payload["results"]
    assert t(lang, "report_title") in text
    assert fmt_num(res["coverage_pct"], 1, lang) + "%" in text
    assert fmt_num(res["missed_area_ha"], 3, lang) in text
    assert fmt_num(res["overlap_area_ha"], 3, lang) in text
    assert fmt_num(res["litres_applied"], 1, lang) in text
    for value in ("Namuna Fermer (sample)", "Sample field 4.2 ha", "Cotton",
                  "Sample product 25 EC", "SAMPLE-0001", "Sample Pilot", "K30-SIM-001"):
        assert value in text
    assert URL in text
    assert processed.record.payload_sha256 in text
    assert payload["input"]["log_sha256"] in text
    assert payload["signer"]["key_id"] in text


@pytest.mark.parametrize("lang", LANGUAGES)
def test_pdf_labels_sample_data_and_estimated_litres(payload, processed, lang):
    text = pdf_text(render_pdf(payload, processed.record.payload_sha256, URL, lang))
    assert SAMPLE_DATA_NOTICE in text
    assert t(lang, "sample_notice") in text
    assert t(lang, "basis_estimated") in text


def test_customer_record_has_no_sample_banner(payload, processed):
    real = {**payload, "sample_data": False}
    text = pdf_text(render_pdf(real, processed.record.payload_sha256, URL, "en"))
    assert SAMPLE_DATA_NOTICE not in text and "SAMPLE DATA" not in text


def test_uzbek_pdf_keeps_the_correct_letters(payload, processed):
    text = pdf_text(render_pdf(payload, processed.record.payload_sha256, URL, "uz"))
    assert "koʻrsatkichlar".upper() in text.upper()
    assert "maʼlumotlar" in text
    assert "Oʻzbekistonda" in text


def test_qr_code_points_at_the_verify_url(payload, processed, monkeypatch):
    import qrcode
    seen = []
    real_make = qrcode.make
    monkeypatch.setattr(qrcode, "make", lambda data, **kw: seen.append(data) or real_make(data, **kw))
    render_pdf(payload, processed.record.payload_sha256, URL, "en")
    assert seen == [URL]


def test_third_party_copy_names_the_recipient_and_expiry(payload, processed):
    share = {"recipient_type": "bank", "expires_utc": "2026-12-11T00:00:00Z"}
    text = pdf_text(render_pdf(payload, processed.record.payload_sha256, URL, "en", share=share))
    assert "Shared with: Bank" in text and "valid until 11.12.2026" in text
    own = pdf_text(render_pdf(payload, processed.record.payload_sha256, URL, "en",
                              share={"recipient_type": "owner", "expires_utc": None}))
    assert "Shared with" not in own


def test_long_values_do_not_break_the_single_page(payload, processed):
    long = json.loads(json.dumps(payload))
    long["job"]["product"] = "Very long product name " * 12
    long["farmer"]["name"] = "Abdulazizxoʻjayev Muhammadshukurulloh Gʻayratjon oʻgʻli " * 3
    pdf = render_pdf(long, processed.record.payload_sha256, URL, "uz")
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 1


# --- Telegram summary --------------------------------------------------------

@pytest.mark.parametrize("lang", LANGUAGES)
def test_summary_text(payload, lang):
    text = record_summary(payload, lang)
    assert t(lang, "sample_notice") in text
    assert fmt_num(payload["results"]["coverage_pct"], 1, lang) + "%" in text
    assert t(lang, "basis_estimated") in text
    assert "{" not in text
