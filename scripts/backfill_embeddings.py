"""
Backfill embeddings для старых ссылок и файлов.

Запуск:
    uv run python scripts/backfill_embeddings.py
    uv run python scripts/backfill_embeddings.py --only links
    uv run python scripts/backfill_embeddings.py --only files
    uv run python scripts/backfill_embeddings.py --batch-size 32

Скрипт кодирует батчами, пропускает уже закодированные (если текст не изменился).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg

from config import get_settings
from db import repo
from services import embedding_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def backfill_links(pool: asyncpg.Pool, batch_size: int) -> tuple[int, int]:
    """Кодирует ссылки без эмбеддинга. Возвращает (ok, errors)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM resource_links ORDER BY id ASC"
        )

    total = len(rows)
    ok = errors = 0
    log.info("resource_links: %d записей, начинаем кодирование батчами по %d", total, batch_size)

    ids = [int(r["id"]) for r in rows]
    for batch_start in range(0, len(ids), batch_size):
        batch_ids = ids[batch_start : batch_start + batch_size]

        # Получаем данные батча
        async with pool.acquire() as conn:
            link_rows = await conn.fetch(
                """
                SELECT l.id, l.title, l.url, l.user_note, l.ai_summary,
                       l.embedding_text_hash,
                       CASE WHEN p.id IS NULL THEN t.title ELSE p.title || ' / ' || t.title END AS topic_path
                FROM resource_links l
                INNER JOIN resource_topics t ON t.id = l.topic_id
                LEFT JOIN resource_topics p ON p.id = t.parent_id
                WHERE l.id = ANY($1::bigint[])
                """,
                batch_ids,
            )

        texts = []
        to_encode = []
        for row in link_rows:
            text = embedding_service.build_link_embedding_text(
                title=str(row["title"] or ""),
                topic=str(row["topic_path"] or ""),
                user_note=str(row["user_note"] or ""),
                ai_summary=str(row["ai_summary"] or "") if row["ai_summary"] else None,
            )
            th = embedding_service.text_hash(text)
            if row.get("embedding_text_hash") == th:
                continue  # уже закодировано, пропускаем
            texts.append(text)
            to_encode.append((int(row["id"]), th))

        if not to_encode:
            log.info("Батч %d–%d: все уже закодированы, пропуск",
                     batch_start + 1, batch_start + len(batch_ids))
            ok += len(batch_ids)
            continue

        vecs = await embedding_service.embed_texts(texts, "retrieval.passage")
        if vecs is None:
            log.error("Jina вернула ошибку для батча %d–%d", batch_start + 1, batch_start + len(batch_ids))
            errors += len(to_encode)
            continue

        for (link_id, th), vec in zip(to_encode, vecs):
            await repo.update_resource_link_embedding(pool, link_id, vec, th)
            ok += 1

        log.info("resource_links: %d/%d готово (%d ошибок)", ok, total, errors)

    return ok, errors


async def backfill_files(pool: asyncpg.Pool, batch_size: int) -> tuple[int, int]:
    """Кодирует файлы без эмбеддинга. Возвращает (ok, errors)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.id, f.original_filename, f.confirmed_category, f.summary,
                   f.subject_tags, f.extracted_text_preview, f.embedding_text_hash,
                   COALESCE(c.label_ru, f.confirmed_category) AS category_label
            FROM files f
            LEFT JOIN file_categories c ON c.slug = f.confirmed_category
            WHERE f.status = 'confirmed'
            ORDER BY f.id ASC
            """
        )

    total = len(rows)
    ok = errors = 0
    log.info("files: %d записей, начинаем кодирование батчами по %d", total, batch_size)

    all_rows = list(rows)
    for batch_start in range(0, len(all_rows), batch_size):
        batch = all_rows[batch_start : batch_start + batch_size]

        texts = []
        to_encode = []
        for row in batch:
            text = embedding_service.build_file_embedding_text(
                original_filename=str(row["original_filename"] or ""),
                category_label=str(row["category_label"] or ""),
                summary=str(row["summary"] or "") if row["summary"] else None,
                subject_tags=str(row["subject_tags"] or "") if row["subject_tags"] else None,
                extracted_text_preview=str(row.get("extracted_text_preview") or "") or None,
            )
            th = embedding_service.text_hash(text)
            if row.get("embedding_text_hash") == th:
                ok += 1
                continue
            texts.append(text)
            to_encode.append((int(row["id"]), th))

        if not to_encode:
            log.info("Батч %d–%d: все уже закодированы, пропуск",
                     batch_start + 1, batch_start + len(batch))
            continue

        vecs = await embedding_service.embed_texts(texts, "retrieval.passage")
        if vecs is None:
            log.error("Jina вернула ошибку для батча %d–%d", batch_start + 1, batch_start + len(batch))
            errors += len(to_encode)
            continue

        for (file_id, th), vec in zip(to_encode, vecs):
            await repo.update_file_embedding(pool, file_id, vec, th)
            ok += 1

        log.info("files: %d/%d готово (%d ошибок)", ok, total, errors)

    return ok, errors


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill embeddings для ссылок и файлов",
        epilog=(
            "Пример для локального запуска к Docker Postgres:\n"
            "  uv run python scripts/backfill_embeddings.py "
            "--db-url postgresql://closedhub:closedhub@localhost:5433/closedhub"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--only", choices=["links", "files"], help="Кодировать только ссылки или только файлы")
    parser.add_argument("--batch-size", type=int, default=20, help="Размер батча (default: 20)")
    parser.add_argument(
        "--db-url",
        help=(
            "URL базы данных (переопределяет DATABASE_URL из .env). "
            "Нужен при локальном запуске к Docker Postgres: "
            "postgresql://closedhub:closedhub@localhost:5433/closedhub"
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.jina_api_key:
        log.error("JINA_API_KEY не задан — backfill невозможен")
        sys.exit(1)

    db_url = args.db_url or settings.database_url
    log.info("Подключаемся к БД: %s", db_url.split("@")[-1])  # не показываем пароль

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)

    try:
        if args.only != "files":
            ok, errors = await backfill_links(pool, args.batch_size)
            log.info("resource_links: итог ok=%d errors=%d", ok, errors)

        if args.only != "links":
            ok, errors = await backfill_files(pool, args.batch_size)
            log.info("files: итог ok=%d errors=%d", ok, errors)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
