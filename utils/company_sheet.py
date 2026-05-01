from __future__ import annotations

import re

_INVALID_IN_TITLE = re.compile(r"[\[\]:\\/*?]")


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
