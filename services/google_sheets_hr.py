from __future__ import annotations

import logging
from datetime import datetime, timezone

import gspread

from config import get_settings

log = logging.getLogger(__name__)

WORKSHEET_TITLE = "HR_contacts"


def append_hr_contact_row(
    *,
    company: str | None,
    telegram_uid: int,
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
        try:
            ws = sh.worksheet(WORKSHEET_TITLE)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=WORKSHEET_TITLE, rows=2000, cols=10)
        if not ws.row_values(1):
            ws.append_row(
                [
                    "Время (UTC)",
                    "Компания",
                    "Telegram UID HR",
                    "Роль / кто это",
                    "Вакансии / направление",
                    "Контекст / комментарий",
                    "Кто добавил (TG id)",
                    "ID в PostgreSQL",
                ],
                value_input_option="USER_ENTERED",
            )
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ws.append_row(
            [
                ts,
                company or "",
                str(telegram_uid),
                role_hint or "",
                vacancies_hint or "",
                (summary or "")[:4000],
                str(source_user_id),
                str(hr_db_id),
            ],
            value_input_option="USER_ENTERED",
        )
        log.info("google_sheets hr row appended id=%s company=%s", hr_db_id, company)
    except Exception:
        log.exception("google_sheets append failed")
