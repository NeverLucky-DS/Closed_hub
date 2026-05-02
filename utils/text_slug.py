from __future__ import annotations

import hashlib
import re
import unicodedata


def slugify_folder(name: str, max_len: int = 48) -> str:
    """Имя папки в библиотеке: латиница как раньше; кириллица и др. — через Unicode \\w (путь на диске)."""
    raw = unicodedata.normalize("NFKC", (name or "").strip())
    if not raw:
        return "custom"
    s = raw.casefold()
    s = re.sub(r"[\s/\\]+", "-", s)
    s = re.sub(r"[^\w\-]+", "", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]
        s = f"cat_{h}"
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or f"cat_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:14]}"


def interview_company_slug(company_ru: str, max_len: int = 48) -> str:
    """Имя файла для опыта собесов; кириллица → стабильный hash-префикс."""
    s = slugify_folder(company_ru, max_len=max_len)
    if s and s != "custom":
        return s
    h = hashlib.sha256(company_ru.strip().encode("utf-8")).hexdigest()[:16]
    return f"co_{h}"
