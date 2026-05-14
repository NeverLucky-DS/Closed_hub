"""
Embedding сервис для Jina AI.

Кодирует текст в 256-мерный вектор через jina-embeddings-v5-text-small.
Использует тот же JINA_API_KEY, что и Reader API.

Материалы (ссылки, файлы) кодируются с task=retrieval.passage.
Поисковый запрос пользователя — с task=retrieval.query.
"""
from __future__ import annotations

import hashlib
import json
import logging

import httpx

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "jina-embeddings-v5-text-small"
EMBEDDING_DIM = 256
JINA_EMBED_URL = "https://api.jina.ai/v1/embeddings"


def text_hash(text: str) -> str:
    """SHA-1 от текста — проверяем, нужно ли перекодировать."""
    return hashlib.sha1(text.encode()).hexdigest()


def vector_literal(vec: list[float]) -> str:
    """Конвертирует Python list → строку '[x,y,z]' для asyncpg $1::vector."""
    return "[" + ",".join(str(v) for v in vec) + "]"


async def embed_texts(texts: list[str], task: str) -> list[list[float]] | None:
    """
    Отправляет тексты в Jina Embeddings API, возвращает список векторов.
    task: 'retrieval.passage' (для материалов) или 'retrieval.query' (для запросов).
    Возвращает None, если JINA_API_KEY не задан или API вернула ошибку.
    """
    from config import get_settings

    key = (get_settings().jina_api_key or "").strip()
    if not key:
        log.debug("JINA_API_KEY не задан — embedding пропущен")
        return None

    if not texts:
        return []

    payload = {
        "model": EMBEDDING_MODEL,
        "task": task,
        "dimensions": EMBEDDING_DIM,
        "normalized": True,
        "input": texts,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                JINA_EMBED_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(payload),
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        log.warning("Jina Embeddings API error", exc_info=True)
        return None

    try:
        return [item["embedding"] for item in data["data"]]
    except (KeyError, TypeError):
        log.warning("Jina Embeddings unexpected response: %s", str(data)[:200])
        return None


def build_link_embedding_text(
    title: str,
    topic: str,
    user_note: str,
    ai_summary: str | None,
) -> str:
    """Собирает текст для кодирования ссылки."""
    parts = [title.strip()]
    if topic:
        parts.append(topic.strip())
    if user_note:
        parts.append(user_note.strip())
    if ai_summary:
        parts.append(ai_summary.strip())
    return " | ".join(p for p in parts if p)


def build_file_embedding_text(
    original_filename: str | None,
    category_label: str | None,
    summary: str | None,
    subject_tags: str | None,
    extracted_text_preview: str | None = None,
) -> str:
    """Собирает текст для кодирования файла."""
    parts: list[str] = []
    if original_filename:
        parts.append(original_filename.strip())
    if category_label:
        parts.append(category_label.strip())
    if summary:
        parts.append(summary.strip())
    if subject_tags:
        parts.append(subject_tags.strip())
    # Короткий preview — контекст без лишних расходов
    if extracted_text_preview:
        parts.append(extracted_text_preview[:400].strip())
    return " | ".join(p for p in parts if p)


async def refresh_resource_link_embedding(pool, link_id: int) -> bool:
    """
    Считывает ссылку из БД, строит текст, кодирует, сохраняет вектор.
    Возвращает True если embedding успешно обновлён.
    """
    from db import repo

    row = await repo.get_resource_link_with_topic(pool, link_id)
    if not row:
        return False

    text = build_link_embedding_text(
        title=str(row["title"] or ""),
        topic=str(row["topic_path"] or ""),
        user_note=str(row["user_note"] or ""),
        ai_summary=str(row["ai_summary"] or "") if row["ai_summary"] else None,
    )
    if not text:
        return False

    th = text_hash(text)
    if row.get("embedding_text_hash") == th:
        return True  # текст не изменился

    vecs = await embed_texts([text], "retrieval.passage")
    if not vecs:
        return False

    await repo.update_resource_link_embedding(pool, link_id, vecs[0], th)
    return True


async def refresh_file_embedding(pool, file_id: int) -> bool:
    """
    Считывает файл из БД, строит текст, кодирует, сохраняет вектор.
    Возвращает True если embedding успешно обновлён.
    """
    from db import repo

    row = await repo.get_file_record(pool, file_id)
    if not row or str(row.get("status") or "") != "confirmed":
        return False

    text = build_file_embedding_text(
        original_filename=str(row["original_filename"] or ""),
        category_label=str(row["confirmed_category"] or ""),
        summary=str(row["summary"] or "") if row["summary"] else None,
        subject_tags=str(row["subject_tags"] or "") if row["subject_tags"] else None,
        extracted_text_preview=str(row.get("extracted_text_preview") or "") or None,
    )
    if not text:
        return False

    th = text_hash(text)
    if row.get("embedding_text_hash") == th:
        return True

    vecs = await embed_texts([text], "retrieval.passage")
    if not vecs:
        return False

    await repo.update_file_embedding(pool, file_id, vecs[0], th)
    return True
