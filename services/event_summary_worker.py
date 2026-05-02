from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram.ext import Application

from db import repo
from services import llm

log = logging.getLogger(__name__)


async def _process_job(pool, event_id: int, raw_text: str) -> None:
    try:
        data = await llm.summarize_event(pool, raw_text)
    except Exception:
        log.exception("summarize_event failed for event_id=%s", event_id)
        return
    summary = (data.get("summary") or "").strip() or None
    title = (data.get("title") or "").strip() or None
    if summary:
        try:
            await repo.update_event_summary(pool, event_id, summary)
        except Exception:
            log.exception("update_event_summary failed event_id=%s", event_id)
    if title:
        try:
            await repo.update_event_normalized_title(pool, event_id, title)
        except Exception:
            log.exception("update_event_normalized_title failed event_id=%s", event_id)


async def _worker_loop(app: Application) -> None:
    q: asyncio.Queue[dict[str, Any]] = app.bot_data["event_summary_queue"]
    pool = app.bot_data["pool"]
    log.info("event summary queue worker started")
    while True:
        job = await q.get()
        try:
            await _process_job(pool, int(job["event_id"]), str(job["raw_text"]))
        except Exception:
            log.exception("event summary job crashed")
        finally:
            q.task_done()


def start_event_summary_worker(app: Application) -> None:
    if app.bot_data.get("event_summary_worker"):
        return
    app.bot_data["event_summary_queue"] = asyncio.Queue()
    app.bot_data["event_summary_worker"] = asyncio.create_task(_worker_loop(app))


async def enqueue_event_summary(app: Application, event_id: int, raw_text: str) -> None:
    q = app.bot_data["event_summary_queue"]
    await q.put({"event_id": event_id, "raw_text": raw_text})


async def stop_event_summary_worker(app: Application) -> None:
    task = app.bot_data.pop("event_summary_worker", None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
