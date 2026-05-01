from __future__ import annotations

import logging
from datetime import datetime, timezone

import gspread

from config import get_settings
from utils.company_sheet import hr_workbook_sheet_title

log = logging.getLogger(__name__)

_HEADER = ["Контакт", "Дата и время (UTC)", "Комментарий"]
_COMMENT_MAX_CHARS = 30_000

_SHEET_WARN_WORKSHEETS = 180


def _format_dt_utc() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M") + " UTC"


def _get_or_create_worksheet(sh: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=2000, cols=10)


def _ensure_headers_and_spacer(ws: gspread.Worksheet) -> None:
    if ws.acell("A1").value:
        return
    ws.batch_update(
        [
            {"range": "A1:C1", "values": [_HEADER]},
            {"range": "A2:C2", "values": [["", "", ""]]},
        ],
        value_input_option="USER_ENTERED",
    )
    _apply_hr_column_format(ws)


def _apply_hr_column_format(ws: gspread.Worksheet) -> None:
    try:
        sid = ws.id
        ws.spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": sid,
                                "dimension": "COLUMNS",
                                "startIndex": 0,
                                "endIndex": 1,
                            },
                            "properties": {"pixelSize": 220},
                            "fields": "pixelSize",
                        }
                    },
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": sid,
                                "dimension": "COLUMNS",
                                "startIndex": 1,
                                "endIndex": 2,
                            },
                            "properties": {"pixelSize": 170},
                            "fields": "pixelSize",
                        }
                    },
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": sid,
                                "dimension": "COLUMNS",
                                "startIndex": 2,
                                "endIndex": 3,
                            },
                            "properties": {"pixelSize": 780},
                            "fields": "pixelSize",
                        }
                    },
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sid,
                                "startColumnIndex": 2,
                                "endColumnIndex": 3,
                                "startRowIndex": 0,
                                "endRowIndex": 5000,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "wrapStrategy": "WRAP",
                                    "verticalAlignment": "TOP",
                                }
                            },
                            "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment",
                        }
                    },
                ]
            }
        )
    except Exception:
        log.warning("google_sheets: не удалось задать ширину колонок / перенос", exc_info=True)


def _next_data_row(ws: gspread.Worksheet) -> int:
    """Первая запись — строка 3 (под заголовком и пустой строкой). Далее — через одну пустую после последней заполненной в A."""
    _ensure_headers_and_spacer(ws)
    depth = min(ws.row_count, 3000)
    rng = ws.get(f"A1:A{depth}")
    last_non_empty = 0
    for i, row in enumerate(rng, start=1):
        cell = row[0] if row else ""
        if str(cell).strip():
            last_non_empty = i
    return last_non_empty + 2


def append_hr_contact_row(
    *,
    company: str | None,
    contact_ref: str,
    summary: str | None,
    hr_db_id: int,
) -> str:
    """Три колонки: контакт, дата дд/мм/гггг + время UTC, комментарий. Пустая строка под заголовком и между записями."""
    settings = get_settings()
    if not settings.google_sheet_id or not settings.google_service_account_json_path:
        log.info("metric type=sheets_append status=skipped reason=no_config hr_id=%s", hr_db_id)
        return "skipped"
    try:
        gc = gspread.service_account(filename=settings.google_service_account_json_path)
        sh = gc.open_by_key(settings.google_sheet_id)
        n_ws = len(sh.worksheets())
        log.info("metric type=sheets_workbook worksheets=%s", n_ws)
        if n_ws >= _SHEET_WARN_WORKSHEETS:
            log.warning(
                "sheets: много вкладок (%s), лимит Google ~200 — позже лучше одна книга с колонкой «Компания»",
                n_ws,
            )
        title = hr_workbook_sheet_title(company)
        ws = _get_or_create_worksheet(sh, title)
        row_i = _next_data_row(ws)
        comment = (summary or "")[:_COMMENT_MAX_CHARS]
        data_row = [contact_ref, _format_dt_utc(), comment]
        ws.batch_update(
            [
                {"range": f"A{row_i}:C{row_i}", "values": [data_row]},
                {"range": f"A{row_i + 1}:C{row_i + 1}", "values": [["", "", ""]]},
            ],
            value_input_option="USER_ENTERED",
        )
        log.info(
            "metric type=sheets_append status=ok sheet=%s row=%s hr_id=%s worksheets=%s",
            title,
            row_i,
            hr_db_id,
            n_ws,
        )
        return "ok"
    except FileNotFoundError:
        log.error(
            "google_sheets: файл ключа не найден (%s). "
            "Локально проверь GOOGLE_SERVICE_ACCOUNT_JSON_PATH; в Docker — volume в docker-compose и GOOGLE_SERVICE_ACCOUNT_JSON_HOST.",
            settings.google_service_account_json_path,
        )
        log.info("metric type=sheets_append status=error reason=file_not_found hr_id=%s", hr_db_id)
        return "error"
    except Exception:
        log.exception("google_sheets append failed")
        log.info("metric type=sheets_append status=error reason=exception hr_id=%s", hr_db_id)
        return "error"
