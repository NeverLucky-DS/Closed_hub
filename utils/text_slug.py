from __future__ import annotations

import hashlib
import re
import unicodedata


def slugify_folder(name: str, max_len: int = 48) -> str:
    raw = unicodedata.normalize("NFKD", name.strip())
    ascii_s = raw.encode("ascii", "ignore").decode("ascii")
    s = ascii_s.lower()
    s = re.sub(r"[\s/\\]+", "-", s)
    s = re.sub(r"[^a-z0-9_-]+", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "custom"
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "custom"


def interview_company_slug(company_ru: str, max_len: int = 48) -> str:
    """Имя файла для опыта собесов; кириллица → стабильный hash-префикс."""
    s = slugify_folder(company_ru, max_len=max_len)
    if s and s != "custom":
        return s
    h = hashlib.sha256(company_ru.strip().encode("utf-8")).hexdigest()[:16]
    return f"co_{h}"
