from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from utils.text_slug import slugify_folder

log = logging.getLogger(__name__)

MAX_URL_LEN = 1000
MAX_NOTE_LEN = 2000
MAX_TOPIC_LEN = 80


def normalize_url(raw: str) -> tuple[str | None, str | None]:
    """Проверяет ссылку перед сохранением. Сервер сам URL не скачивает."""
    url = (raw or "").strip()
    if not url:
        return None, "Укажи ссылку."
    if len(url) > MAX_URL_LEN:
        return None, "Ссылка слишком длинная."
    if re.search(r"[\s\x00-\x1f]", url):
        return None, "В ссылке есть пробелы или служебные символы."

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "Нужна ссылка вида https://example.com/..."
    return url, None


def normalize_note(raw: str) -> str:
    """Обрезает пользовательское описание ресурса до безопасной длины."""
    return (raw or "").strip()[:MAX_NOTE_LEN]


def default_title_for_url(url: str) -> str:
    """Делает короткий заголовок из домена и пути, без сетевого запроса."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").removeprefix("www.")
    path = (parsed.path or "").strip("/")
    if not path:
        return host or url
    part = path.split("/")[-1] or path
    part = part.replace("-", " ").replace("_", " ").strip()
    if not part:
        return host or url
    return f"{host} / {part[:80]}"


async def fetch_and_enrich_link(pool, url: str) -> dict:
    """
    Читает страницу через Jina Reader и строит черновик через Mistral.
    При любой ошибке возвращает fallback с title из URL и пустым ai_summary.
    """
    from config import get_settings
    from db import repo
    from services import llm

    fallback = {
        "title": default_title_for_url(url),
        "main_topic": None,
        "is_new_topic": False,
        "description_ru": "",
        "key_topics": [],
        "ai_summary": "",
        "enriched": False,
    }

    settings = get_settings()
    jina_key = (settings.jina_api_key or "").strip()
    if not jina_key:
        log.debug("JINA_API_KEY не задан — пропускаем обогащение ссылки")
        return fallback

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"https://r.jina.ai/{url}",
                headers={"Authorization": f"Bearer {jina_key}", "Accept": "text/plain"},
            )
            page_text = r.text or ""
    except Exception:
        log.warning("Jina fetch failed for %s", url, exc_info=True)
        return fallback

    if not page_text.strip():
        return fallback

    try:
        topics = await repo.list_resource_topics(pool)
        topic_names = [str(t["title"]) for t in topics]
        data = await llm.enrich_resource_link(pool, url, page_text, topic_names)
    except Exception:
        log.warning("Mistral enrich failed for %s", url, exc_info=True)
        return fallback

    return {
        "title": (str(data.get("title") or "").strip()[:200] or default_title_for_url(url)),
        "main_topic": (str(data.get("main_topic") or "")).strip() or None,
        "is_new_topic": bool(data.get("is_new_topic", False)),
        "description_ru": (str(data.get("description_ru") or "")).strip()[:500],
        "key_topics": [str(t) for t in (data.get("key_topics") or [])[:5]],
        "ai_summary": (str(data.get("ai_summary") or "")).strip()[:400],
        "enriched": True,
    }


def normalize_topic_title(raw: str) -> tuple[str | None, str | None, str | None]:
    """Готовит название и slug новой папки ресурсов."""
    title = " ".join((raw or "").strip().split())
    if not title:
        return None, None, "Напиши название новой темы."
    if len(title) > MAX_TOPIC_LEN:
        return None, None, "Название темы слишком длинное."
    slug = slugify_folder(title, max_len=60)
    return slug, title, None
