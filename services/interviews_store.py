from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from config import get_settings
from utils.text_slug import interview_company_slug

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
    sep = "\n\n---\n\n"
    if not path.is_file():
        content = f"# {company_title}\n\n{block}\n"
        path.write_text(content, encoding="utf-8")
        log.info("interview file created slug=%s", slug)
        return path
    existing = path.read_text(encoding="utf-8").rstrip()
    path.write_text(existing + sep + block + "\n", encoding="utf-8")
    log.info("interview file appended slug=%s", slug)
    return path


def append_site_review_for_company(
    *,
    company_display_name: str,
    author_telegram_id: int,
    body: str,
    hr_contact_ref: str | None = None,
) -> Path:
    """Тот же .md, что собирает бот при «Поделиться» — чтобы «Читать собесы» видел отзывы с сайта."""
    name = (company_display_name or "").strip() or "Компания"
    slug = interview_company_slug(name)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"### {ts} — с сайта (Telegram id: {author_telegram_id})",
        "",
        body.strip(),
    ]
    if hr_contact_ref:
        lines.extend(["", f"Упомянут HR: {hr_contact_ref}"])
    lines.append("")
    block = "\n".join(lines)
    return append_report(slug=slug, company_title=name, body=block)
