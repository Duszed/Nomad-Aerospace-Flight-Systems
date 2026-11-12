"""Translations: Uzbek (Latin), Russian, English.

Uzbek Latin is written with the correct letters: oʻ and gʻ use U+02BB
(ʻ, modifier letter turned comma) and the tutuq belgisi uses U+02BC (ʼ) -
never a plain apostrophe or backtick.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

LANGUAGES = ("uz", "ru", "en")
DEFAULT_LANGUAGE = "uz"
LANGUAGE_NAMES = {"uz": "Oʻzbekcha", "ru": "Русский", "en": "English"}

# Uzbekistan is UTC+5 all year (no daylight saving time)
TASHKENT = timezone(timedelta(hours=5))

T: dict[str, dict[str, str]] = {
    # --- report ---------------------------------------------------------------
    "report_title": {
        "en": "Spray Application Record",
        "ru": "Отчёт об обработке поля",
        "uz": "Purkash ishlari hisoboti",
    },
    "sample_notice": {
        "en": "Sample data – not a customer record",
        "ru": "Демонстрационные данные – не является записью клиента",
        "uz": "Namuna maʼlumotlar – mijoz yozuvi emas",
    },
    "record": {"en": "Record", "ru": "Запись", "uz": "Yozuv"},
    "version": {"en": "version", "ru": "версия", "uz": "versiya"},
    "key_numbers": {"en": "Key numbers", "ru": "Основные показатели",
                    "uz": "Asosiy koʻrsatkichlar"},
    "job_details": {"en": "Job details", "ru": "Сведения о работе",
                    "uz": "Ish tafsilotlari"},
    "coverage": {"en": "Coverage", "ru": "Покрытие", "uz": "Qamrov"},
    "field_area": {"en": "Field area", "ru": "Площадь поля", "uz": "Dala maydoni"},
    "sprayed_area": {"en": "Sprayed in field", "ru": "Обработано на поле",
                     "uz": "Dalada purkalgan"},
    "missed_area": {"en": "Missed", "ru": "Пропущено", "uz": "Purkalmay qolgan"},
    "missed_strips": {"en": "{n} strip(s)", "ru": "участков: {n}", "uz": "{n} ta boʻlak"},
    "overlap_area": {"en": "Double-sprayed", "ru": "Обработано дважды",
                     "uz": "Ikki marta purkalgan"},
    "outside_area": {"en": "Sprayed outside field", "ru": "Обработано вне поля",
                     "uz": "Dala tashqarisida purkalgan"},
    "litres_applied": {"en": "Volume applied", "ru": "Внесено раствора",
                       "uz": "Sarflangan suyuqlik"},
    "planned_rate": {"en": "Planned rate", "ru": "Норма по плану", "uz": "Rejadagi meʼyor"},
    "actual_rate": {"en": "Actual rate", "ru": "Фактическая норма", "uz": "Haqiqiy meʼyor"},
    "vs_plan": {"en": "{pct} vs plan", "ru": "{pct} к плану", "uz": "rejaga nisbatan {pct}"},
    "basis_estimated": {"en": "estimated", "ru": "оценка", "uz": "taxminiy"},
    "basis_measured": {"en": "measured by flow meter", "ru": "измерено расходомером",
                       "uz": "sarf oʻlchagich bilan oʻlchangan"},
    "basis_operator": {"en": "operator-reported", "ru": "по данным оператора",
                       "uz": "operator maʼlumoti"},
    "estimated_note": {
        "en": "Estimated: no flow meter on this aircraft. Volume = sprayed distance × swath × planned rate.",
        "ru": "Оценка: на борту нет расходомера. Объём = пройденный путь с опрыскиванием × ширина захвата × плановая норма.",
        "uz": "Taxminiy: dronda sarf oʻlchagich yoʻq. Hajm = purkash masofasi × qamrash kengligi × rejadagi meʼyor.",
    },
    "farmer": {"en": "Farmer", "ru": "Фермер", "uz": "Fermer"},
    "field": {"en": "Field", "ru": "Поле", "uz": "Dala"},
    "crop": {"en": "Crop", "ru": "Культура", "uz": "Ekin"},
    "product": {"en": "Product", "ru": "Препарат", "uz": "Preparat"},
    "batch": {"en": "Batch", "ru": "Номер партии", "uz": "Partiya raqami"},
    "pilot": {"en": "Pilot", "ru": "Пилот", "uz": "Uchuvchi"},
    "drone_id": {"en": "Drone ID", "ru": "Номер дрона", "uz": "Dron raqami"},
    "swath": {"en": "Swath width", "ru": "Ширина захвата", "uz": "Qamrash kengligi"},
    "started": {"en": "Started", "ru": "Начало", "uz": "Boshlanishi"},
    "ended": {"en": "Ended", "ru": "Окончание", "uz": "Tugashi"},
    "weather": {"en": "Weather", "ru": "Погода", "uz": "Ob-havo"},
    "wind": {"en": "wind", "ru": "ветер", "uz": "shamol"},
    "humidity": {"en": "humidity", "ru": "влажность", "uz": "namlik"},
    "flight": {"en": "Flight", "ru": "Полёт", "uz": "Parvoz"},
    "flight_summary": {
        "en": "{speed} m/s at {height} m, {passes} spray runs",
        "ru": "{speed} м/с на высоте {height} м, рабочих проходов: {passes}",
        "uz": "{speed} m/s, balandlik {height} m, {passes} ta purkash oʻtishi",
    },
    "sprayer_source": {"en": "Sprayer signal", "ru": "Сигнал опрыскивателя",
                       "uz": "Purkagich signali"},
    "src_servo": {"en": "pump output, channel {ch}", "ru": "выход насоса, канал {ch}",
                  "uz": "nasos chiqishi, {ch}-kanal"},
    "src_relay": {"en": "relay", "ru": "реле", "uz": "rele"},
    "src_flow": {"en": "flow meter", "ru": "расходомер", "uz": "sarf oʻlchagich"},
    "src_speed": {"en": "inferred from ground speed", "ru": "по путевой скорости",
                  "uz": "yer tezligi boʻyicha aniqlangan"},
    "log_hash": {"en": "Flight log SHA-256", "ru": "SHA-256 журнала полёта",
                 "uz": "Parvoz jurnali SHA-256"},
    "record_hash": {"en": "Record SHA-256", "ru": "SHA-256 записи", "uz": "Yozuv SHA-256"},
    "signed_by": {"en": "Signed with Ed25519, key {key}",
                  "ru": "Подписано Ed25519, ключ {key}",
                  "uz": "Ed25519 bilan imzolangan, kalit {key}"},
    "scan_to_verify": {"en": "Scan to verify this record",
                       "ru": "Отсканируйте, чтобы проверить запись",
                       "uz": "Yozuvni tekshirish uchun skanerlang"},
    "shared_with": {"en": "Shared with", "ru": "Доступ предоставлен", "uz": "Kimga ulashilgan"},
    "valid_until": {"en": "valid until {date}", "ru": "действует до {date}",
                    "uz": "{date} gacha amal qiladi"},
    "supersedes": {"en": "Replaces an earlier version. Reason: {reason}",
                   "ru": "Заменяет предыдущую версию. Причина: {reason}",
                   "uz": "Oldingi versiya oʻrniga. Sabab: {reason}"},
    "footer": {
        "en": "Nomad Aerospace · Nomad Atlas · Record data is stored in Uzbekistan and shared only by the farmer.",
        "ru": "Nomad Aerospace · Nomad Atlas · Данные записи хранятся в Узбекистане; доступ к ним открывает только фермер.",
        "uz": "Nomad Aerospace · Nomad Atlas · Yozuv maʼlumotlari Oʻzbekistonda saqlanadi va faqat fermer tomonidan ulashiladi.",
    },
    # --- map legend -----------------------------------------------------------
    "legend_sprayed": {"en": "Sprayed", "ru": "Обработано", "uz": "Purkalgan"},
    "legend_missed": {"en": "Missed", "ru": "Пропущено", "uz": "Purkalmagan"},
    "legend_overlap": {"en": "Double-sprayed", "ru": "Обработано дважды",
                       "uz": "Ikki marta purkalgan"},
    "legend_boundary": {"en": "Field boundary", "ru": "Граница поля", "uz": "Dala chegarasi"},
    # --- units ----------------------------------------------------------------
    "u_ha": {"en": "ha", "ru": "га", "uz": "ga"},
    "u_l": {"en": "L", "ru": "л", "uz": "l"},
    "u_l_ha": {"en": "L/ha", "ru": "л/га", "uz": "l/ga"},
    "u_m": {"en": "m", "ru": "м", "uz": "m"},
    "u_ms": {"en": "m/s", "ru": "м/с", "uz": "m/s"},
    # --- recipients -----------------------------------------------------------
    "rcp_owner": {"en": "Farmer (own link)", "ru": "Фермер (личная ссылка)",
                  "uz": "Fermer (shaxsiy havola)"},
    "rcp_bank": {"en": "Bank", "ru": "Банк", "uz": "Bank"},
    "rcp_insurer": {"en": "Insurer", "ru": "Страховая компания", "uz": "Sugʻurta kompaniyasi"},
    "rcp_buyer": {"en": "Export buyer", "ru": "Экспортный покупатель", "uz": "Eksport xaridori"},
    "rcp_cluster": {"en": "Cluster", "ru": "Кластер", "uz": "Klaster"},
    # --- status ---------------------------------------------------------------
    "st_verified": {"en": "Verified", "ru": "Подтверждено", "uz": "Tasdiqlandi"},
    "st_modified": {"en": "Modified", "ru": "Изменено", "uz": "Oʻzgartirilgan"},
    "st_revoked": {"en": "Revoked", "ru": "Отозвано", "uz": "Bekor qilingan"},
    "st_expired": {"en": "Expired", "ru": "Срок истёк", "uz": "Muddati tugagan"},
    "st_not_found": {"en": "Not found", "ru": "Не найдено", "uz": "Topilmadi"},
    # --- verify page ----------------------------------------------------------
    "web_title": {"en": "Nomad Atlas – record verification",
                  "ru": "Nomad Atlas – проверка записи",
                  "uz": "Nomad Atlas – yozuvni tekshirish"},
    "web_verified": {
        "en": "This record matches what Nomad Aerospace signed. Its hash was recomputed and its Ed25519 signature checked just now.",
        "ru": "Запись совпадает с подписанной Nomad Aerospace. Хеш пересчитан, подпись Ed25519 проверена только что.",
        "uz": "Yozuv Nomad Aerospace imzolagan nusxa bilan bir xil. Xesh qayta hisoblandi va Ed25519 imzosi hozir tekshirildi.",
    },
    "web_modified": {
        "en": "This record does not match its signature. It has been changed after signing. Do not rely on it.",
        "ru": "Запись не соответствует подписи: она была изменена после подписания. Не полагайтесь на неё.",
        "uz": "Yozuv imzoga mos kelmaydi: u imzolangandan keyin oʻzgartirilgan. Unga tayanmang.",
    },
    "web_revoked": {
        "en": "The farmer has revoked this share. The record is no longer available through this link.",
        "ru": "Фермер отозвал доступ. Запись по этой ссылке больше недоступна.",
        "uz": "Fermer bu ulashishni bekor qilgan. Yozuv bu havola orqali endi ochilmaydi.",
    },
    "web_expired": {
        "en": "This share link has expired. Ask the farmer for a new one.",
        "ru": "Срок действия ссылки истёк. Попросите у фермера новую.",
        "uz": "Havolaning amal qilish muddati tugagan. Fermerdan yangisini soʻrang.",
    },
    "web_not_found": {
        "en": "No record is shared under this link.",
        "ru": "По этой ссылке запись не найдена.",
        "uz": "Bu havola boʻyicha yozuv topilmadi.",
    },
    "web_problems": {"en": "What does not match", "ru": "Что не совпадает",
                     "uz": "Nima mos kelmayapti"},
    "web_newer": {
        "en": "A newer version of this record exists (version {v}). This link shows version {this}.",
        "ru": "Существует более новая версия записи (версия {v}). По этой ссылке показана версия {this}.",
        "uz": "Bu yozuvning yangiroq versiyasi bor ({v}-versiya). Bu havola {this}-versiyani koʻrsatadi.",
    },
    "web_download_pdf": {"en": "Download PDF", "ru": "Скачать PDF", "uz": "PDF yuklab olish"},
    "web_download_json": {"en": "Signed record (JSON)", "ru": "Подписанная запись (JSON)",
                          "uz": "Imzolangan yozuv (JSON)"},
    "web_confirmed": {"en": "Confirmed by the farmer on {date}",
                      "ru": "Подтверждено фермером {date}",
                      "uz": "Fermer tomonidan {date} da tasdiqlangan"},
    "web_map": {"en": "Coverage map", "ru": "Карта покрытия", "uz": "Qamrov xaritasi"},
    "web_checked": {"en": "Checked", "ru": "Проверено", "uz": "Tekshirildi"},
    # --- bot ------------------------------------------------------------------
    "bot_welcome": {
        "en": "Welcome to Nomad Atlas.\nTo receive your spray reports, share your phone number with the button below.",
        "ru": "Добро пожаловать в Nomad Atlas.\nЧтобы получать отчёты об обработке, отправьте свой номер телефона кнопкой ниже.",
        "uz": "Nomad Atlas xizmatiga xush kelibsiz.\nPurkash hisobotlarini olish uchun quyidagi tugma orqali telefon raqamingizni yuboring.",
    },
    "btn_share_phone": {"en": "Share my phone number", "ru": "Отправить мой номер",
                        "uz": "Telefon raqamimni yuborish"},
    "bot_registered": {
        "en": "You are registered, {name}. Your reports will arrive here.\n/shares – who can see your records\n/lang – change language",
        "ru": "Вы зарегистрированы, {name}. Отчёты будут приходить сюда.\n/shares – кому открыт доступ к вашим записям\n/lang – сменить язык",
        "uz": "Siz roʻyxatdan oʻtdingiz, {name}. Hisobotlar shu yerga keladi.\n/shares – yozuvlaringizni kim koʻra oladi\n/lang – tilni oʻzgartirish",
    },
    "bot_unknown_phone": {
        "en": "This number is not registered with Nomad Aerospace. Please contact your operator.",
        "ru": "Этот номер не зарегистрирован в Nomad Aerospace. Обратитесь к вашему оператору.",
        "uz": "Bu raqam Nomad Aerospace tizimida roʻyxatdan oʻtmagan. Operatoringizga murojaat qiling.",
    },
    "bot_not_own_contact": {
        "en": "Please share your own number using the button.",
        "ru": "Пожалуйста, отправьте свой собственный номер с помощью кнопки.",
        "uz": "Iltimos, tugma orqali oʻz raqamingizni yuboring.",
    },
    "bot_not_registered": {"en": "Please register first: /start",
                           "ru": "Сначала зарегистрируйтесь: /start",
                           "uz": "Avval roʻyxatdan oʻting: /start"},
    "bot_report_caption": {
        "en": "Spray report · {field}\nCoverage: {coverage}%\nSprayed: {sprayed} of {area} ha\nMissed: {missed} ha · Double-sprayed: {overlap} ha\nApplied: {litres} L ({basis})\nRate: {actual} L/ha (plan {planned})",
        "ru": "Отчёт об обработке · {field}\nПокрытие: {coverage}%\nОбработано: {sprayed} из {area} га\nПропущено: {missed} га · Дважды: {overlap} га\nВнесено: {litres} л ({basis})\nНорма: {actual} л/га (план {planned})",
        "uz": "Purkash hisoboti · {field}\nQamrov: {coverage}%\nPurkalgan: {area} ga dan {sprayed} ga\nPurkalmagan: {missed} ga · Ikki marta: {overlap} ga\nSarflangan: {litres} l ({basis})\nMeʼyor: {actual} l/ga (reja {planned})",
    },
    "btn_confirm": {"en": "✅ Confirm", "ru": "✅ Подтвердить", "uz": "✅ Tasdiqlash"},
    "btn_share": {"en": "🔗 Share", "ru": "🔗 Поделиться", "uz": "🔗 Ulashish"},
    "bot_confirmed": {"en": "Thank you. Record confirmed.",
                      "ru": "Спасибо. Запись подтверждена.",
                      "uz": "Rahmat. Yozuv tasdiqlandi."},
    "bot_already_confirmed": {"en": "Already confirmed.", "ru": "Уже подтверждено.",
                              "uz": "Allaqachon tasdiqlangan."},
    "bot_choose_recipient": {
        "en": "Who do you want to share this record with?",
        "ru": "С кем вы хотите поделиться этой записью?",
        "uz": "Bu yozuvni kim bilan ulashmoqchisiz?",
    },
    "bot_share_created": {
        "en": "Share link for: {recipient}\nValid until {until}\n{url}\n\nOnly someone with this link can see the record. You can revoke it at any time: /shares",
        "ru": "Ссылка для: {recipient}\nДействует до {until}\n{url}\n\nЗапись увидит только тот, у кого есть эта ссылка. Доступ можно отозвать в любой момент: /shares",
        "uz": "Havola: {recipient} uchun\n{until} gacha amal qiladi\n{url}\n\nYozuvni faqat shu havolaga ega boʻlgan kishi koʻra oladi. Istalgan vaqtda bekor qilishingiz mumkin: /shares",
    },
    "bot_shares_header": {"en": "Your active shares:", "ru": "Ваши действующие доступы:",
                          "uz": "Amaldagi ulashishlaringiz:"},
    "bot_share_line": {"en": "{recipient} · {field} · until {until} · opened {opens}×",
                       "ru": "{recipient} · {field} · до {until} · открытий: {opens}",
                       "uz": "{recipient} · {field} · {until} gacha · {opens} marta ochilgan"},
    "bot_no_shares": {"en": "You have no active shares.", "ru": "У вас нет действующих доступов.",
                      "uz": "Sizda amaldagi ulashishlar yoʻq."},
    "btn_revoke": {"en": "Revoke: {recipient}", "ru": "Отозвать: {recipient}",
                   "uz": "Bekor qilish: {recipient}"},
    "bot_revoked": {"en": "Share revoked. The link no longer works.",
                    "ru": "Доступ отозван. Ссылка больше не работает.",
                    "uz": "Ulashish bekor qilindi. Havola endi ishlamaydi."},
    "bot_not_yours": {"en": "This record does not belong to you.",
                      "ru": "Эта запись вам не принадлежит.",
                      "uz": "Bu yozuv sizga tegishli emas."},
    "bot_choose_language": {"en": "Choose your language:", "ru": "Выберите язык:",
                            "uz": "Tilni tanlang:"},
    "bot_language_set": {"en": "Language set to English.", "ru": "Язык: русский.",
                         "uz": "Til: oʻzbekcha."},
}


def norm_lang(lang: str | None) -> str:
    lang = (lang or "").lower()[:2]
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def t(lang: str, key: str, /, **fmt) -> str:
    """Translate `key` into `lang` (falls back to English)."""
    entry = T[key]
    text = entry.get(norm_lang(lang)) or entry["en"]
    return text.format(**fmt) if fmt else text


def fmt_num(value: float | int | None, digits: int, lang: str) -> str:
    """Number with the language's decimal separator (comma for uz/ru)."""
    if value is None:
        return "—"
    text = f"{value:.{digits}f}"
    return text if norm_lang(lang) == "en" else text.replace(".", ",")


def fmt_signed_pct(value: float, lang: str) -> str:
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{fmt_num(abs(value), 1, lang)}%"


def fmt_dt(iso_utc: str | datetime | None, with_time: bool = True) -> str:
    """UTC timestamp shown in Uzbekistan time."""
    if not iso_utc:
        return "—"
    if isinstance(iso_utc, str):
        dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    else:
        dt = iso_utc
    local = dt.astimezone(TASHKENT)
    return local.strftime("%d.%m.%Y %H:%M (UTC+5)" if with_time else "%d.%m.%Y")


def sprayer_source_text(payload: dict, lang: str) -> str:
    """Localised sprayer-signal description from the stored English one."""
    src = (payload.get("input") or {}).get("sprayer_source", "")
    if src.startswith("pump servo"):
        ch = "".join(c for c in src.split("channel")[1].split(",")[0] if c.isdigit())
        return t(lang, "src_servo", ch=ch)
    if src.startswith("relay"):
        return t(lang, "src_relay")
    if src.startswith("flow"):
        return t(lang, "src_flow")
    return t(lang, "src_speed")
