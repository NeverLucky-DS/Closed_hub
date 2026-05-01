from __future__ import annotations

import html
import logging
from typing import Any

from telegram import Bot
from telegram.constants import ParseMode

from config import get_settings

log = logging.getLogger(__name__)

_REASON_RU = {
    "hr_contact_confirmed": "подтвердил(а) контакт HR",
    "library_file_confirmed": "подтвердил(а) файл в библиотеке",
    "event_published": "добавил(а) мероприятие в базу",
    "interview_submitted": "поделился(а) опытом собеседования",
    "ml_forward_shared": "переслал(а) пост в ленту хаба",
}


async def notify_award(
    bot: Bot,
    *,
    who_label: str,
    reason: str,
    points: int,
    total: int,
    meta: dict[str, Any] | None = None,
) -> None:
    settings = get_settings()
    chat_id = settings.telegram_group_chat_id
    topic_id = settings.telegram_topic_rating
    if chat_id is None or topic_id is None:
        return
    action = _REASON_RU.get(reason, reason)
    safe_who = html.escape(who_label)
    line = f"⭐ <b>+{points}</b> очков — {action}\n{safe_who} · всего: <b>{total}</b>"
    if meta:
        extra = []
        if reason == "library_file_confirmed" and meta.get("slug"):
            extra.append(html.escape(f"папка: {meta['slug']}"))
        if reason == "interview_submitted" and meta.get("company"):
            extra.append(html.escape(f"компания: {meta['company']}"))
        if reason == "ml_forward_shared" and meta.get("snippet_head"):
            extra.append(html.escape(str(meta["snippet_head"])[:120]))
        if extra:
            line += "\n" + " · ".join(extra)
    try:
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=line,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        log.warning("rating topic notify failed", exc_info=True)
