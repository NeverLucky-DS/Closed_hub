from __future__ import annotations

import re

_INVALID_IN_TITLE = re.compile(r"[\[\]:\\/*?]")

# Лист в книге Google для HR без определённого работодателя (фиксированный регистр для заметности).
HR_SHEET_UNKNOWN = "НЕИЗВЕСТНО"

_SOFT_UNKNOWN_COMPANY = frozenset(
    {
        "без компании",
        "неизвестно",
        "unknown",
        "n/a",
        "нет",
        "null",
        "none",
        "—",
        "-",
        "–",
        "не указано",
        "не указана",
        "не ясно",
        "уточнить",
    }
)


def normalize_company_sheet_title(company: str | None, max_len: int = 99) -> str:
    """Одинаковые по смыслу названия («Яндекс» / «яндекс») → один лист."""
    base = (company or "").strip() or "Без компании"
    base = base.replace("\u00a0", " ").strip()
    base = base.casefold()
    s = _INVALID_IN_TITLE.sub("_", base)
    s = s.replace("'", "").strip()
    if not s:
        s = "без компании"
    return s[:max_len]


def hr_workbook_sheet_title(company: str | None, max_len: int = 99) -> str:
    """Имя вкладки для строки HR: неизвестный работодатель → лист НЕИЗВЕСТНО, иначе нормализация как раньше."""
    s = (company or "").strip()
    if not s:
        return HR_SHEET_UNKNOWN
    if s.casefold() in _SOFT_UNKNOWN_COMPANY:
        return HR_SHEET_UNKNOWN
    return normalize_company_sheet_title(company, max_len=max_len)
