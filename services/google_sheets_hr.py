from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import gspread

from config import get_settings

log = logging.getLogger(__name__)

# Колонки A–D пустые; данные HR с E (5-я колонка) подряд — как таблица/CSV.
_FIRST_DATA_COL = 5  # E
_LAST_DATA_COL = 11  # K
_HEADER_RANGE = "E1:K1"
_DATA_RANGE_TMPL = "E{row}:K{row}"

_INVALID_IN_TITLE = re.compile(r"[\[\]:\\/*?]")


def _sanitize_worksheet_title(company: str | None, max_len: int = 99) -> str:
    s = (company or "").strip() or "Без компании"
    s = _INVALID_IN_TITLE.sub("_", s)
    s = s.replace("'", "").strip()
    if not s:
        s = "Без компании"
    return s[:max_len]


def _header_row() -> list[str]:
    return [
        "Время (UTC)",
        "Контакт (@ник или ID)",
        "Роль / контакт",
        "Вакансии / направление",
        "Контекст / комментарий",
        "Кто добавил (TG id)",
        "ID в PostgreSQL",
    ]


def _get_or_create_worksheet(sh: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=2000, cols=_LAST_DATA_COL)


def _ensure_headers(ws: gspread.Worksheet) -> None:
    if ws.acell("E1").value:
        return
    ws.update(
        _HEADER_RANGE,
        [_header_row()],
        value_input_option="USER_ENTERED",
    )


def _next_data_row(ws: gspread.Worksheet) -> int:
    _ensure_headers(ws)
    col_e = ws.col_values(_FIRST_DATA_COL)
    return len(col_e) + 1


def append_hr_contact_row(
    *,
    company: str | None,
    contact_ref: str,
    role_hint: str | None,
    vacancies_hint: str | None,
    summary: str | None,
    source_user_id: int,
    hr_db_id: int,
) -> None:
    settings = get_settings()
    if not settings.google_sheet_id or not settings.google_service_account_json_path:
        return
    try:
        gc = gspread.service_account(filename=settings.google_service_account_json_path)
        sh = gc.open_by_key(settings.google_sheet_id)
        title = _sanitize_worksheet_title(company)
        ws = _get_or_create_worksheet(sh, title)
        row_i = _next_data_row(ws)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data = [
            ts,
            contact_ref,
            role_hint or "",
            vacancies_hint or "",
            (summary or "")[:4000],
            str(source_user_id),
            str(hr_db_id),
        ]
        ws.update(
            _DATA_RANGE_TMPL.format(row=row_i),
            [data],
            value_input_option="USER_ENTERED",
        )
        log.info("google_sheets hr row sheet=%s row=%s id=%s", title, row_i, hr_db_id)
    except Exception:
        log.exception("google_sheets append failed")
