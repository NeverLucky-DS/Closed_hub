from __future__ import annotations

import logging
import re
from pathlib import Path

from config import get_settings

log = logging.getLogger(__name__)

SUBDIR = "interviews"
_H1 = re.compile(r"^#\s+(.+?)\s*$")


def _root() -> Path:
    settings = get_settings()
    p = Path(settings.file_storage_path) / SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_companies() -> list[tuple[str, str]]:
    """(slug, заголовок для кнопки) — по .md в каталоге."""
    root = _root()
    out: list[tuple[str, str]] = []
    for f in sorted(root.glob("*.md")):
        slug = f.stem
        title = slug
        try:
            first = f.read_text(encoding="utf-8").splitlines()[:5]
            for line in first:
                m = _H1.match(line.strip())
                if m:
                    title = m.group(1).strip()
                    break
        except OSError:
            log.warning("interview file read failed %s", f)
        out.append((slug, title))
    return out


def path_for_slug(slug: str) -> Path:
    safe = slug.replace("/", "").replace("..", "")
    return _root() / f"{safe}.md"


def append_report(*, slug: str, company_title: str, body: str) -> Path:
    path = path_for_slug(slug)
    block = body.rstrip() + "\n"
    if not path.is_file():
        content = f"# {company_title}\n\n{block}\n"
        path.write_text(content, encoding="utf-8")
        log.info("interview file created slug=%s", slug)
        return path
    existing = path.read_text(encoding="utf-8").rstrip()
    path.write_text(existing + "\n\n" + block + "\n", encoding="utf-8")
    log.info("interview file appended slug=%s", slug)
    return path
