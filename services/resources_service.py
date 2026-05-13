from __future__ import annotations

import re
from urllib.parse import urlparse

from utils.text_slug import slugify_folder

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


def normalize_topic_title(raw: str) -> tuple[str | None, str | None, str | None]:
    """Готовит название и slug новой папки ресурсов."""
    title = " ".join((raw or "").strip().split())
    if not title:
        return None, None, "Напиши название новой темы."
    if len(title) > MAX_TOPIC_LEN:
        return None, None, "Название темы слишком длинное."
    slug = slugify_folder(title, max_len=60)
    return slug, title, None
