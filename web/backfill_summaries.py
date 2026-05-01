"""Разовая дозапись AI-саммари для уже опубликованных мероприятий.

Запуск в Docker (рабочая директория должна быть /app, иначе Python не увидит пакет web):
    docker compose exec -w /app web uv run python -m web.backfill_summaries
    # или:
    docker compose exec web sh -lc 'cd /app && uv run python -m web.backfill_summaries'

Локально из корня репозитория:
    uv run python -m web.backfill_summaries
"""
from __future__ import annotations

import asyncio
import logging

from db import repo
from db.pool import close_pool, create_pool
from db.schema_patch import apply_pending_patches
from services import llm

log = logging.getLogger("backfill")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


async def main(batch_size: int = 100, sleep_sec: float = 0.6) -> None:
    pool = await create_pool()
    try:
        await apply_pending_patches(pool)
        rows = await repo.list_events_without_summary(pool, limit=batch_size)
        log.info("backfill: %d events without summary", len(rows))
        ok = 0
        for r in rows:
            eid = int(r["id"])
            try:
                data = await llm.summarize_event(pool, r["raw_text"] or "")
                summary = (data.get("summary") or "").strip()
                if summary:
                    await repo.update_event_summary(pool, eid, summary)
                    ok += 1
                    log.info("event %s: ok", eid)
                else:
                    log.info("event %s: empty summary", eid)
            except Exception:
                log.warning("event %s: failed", eid, exc_info=True)
            await asyncio.sleep(sleep_sec)
        log.info("done: %d/%d updated", ok, len(rows))
    finally:
        await close_pool(pool)


def main_sync() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
