from __future__ import annotations

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
