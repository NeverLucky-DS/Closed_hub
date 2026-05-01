from __future__ import annotations

from telegram import Bot
from telegram.constants import ParseMode

from config import get_settings
from db import repo
from services import llm


async def handle_event_message(
    pool,
    bot: Bot,
    *,
    source_user_id: int,
    raw_text: str,
) -> str:
    settings = get_settings()
    recent = await repo.recent_events_texts(pool, 25)
    dedup = await llm.dedup_event(pool, raw_text, recent)
    should_add = bool(dedup.get("should_add")) and not bool(dedup.get("is_duplicate"))
    normalized = dedup.get("normalized_title")
    norm_title = str(normalized) if normalized else None

    if not should_add:
        return (
            "Похоже, такое мероприятие уже есть в базе или текст не похож на анонс. "
            "Если это ошибка — переформулируй и отправь снова."
        )

    pub_id: int | None = None
    topic_id = settings.events_publish_topic_id
    if settings.telegram_group_chat_id is not None and topic_id is not None:
        header = f"📌 <b>Мероприятие</b>"
        if norm_title:
            header += f"\n<b>{norm_title}</b>"
        body = raw_text[:3500]
        msg = await bot.send_message(
            chat_id=settings.telegram_group_chat_id,
            message_thread_id=topic_id,
            text=f"{header}\n\n{body}",
            parse_mode=ParseMode.HTML,
        )
        pub_id = msg.message_id

    await repo.insert_event(
        pool,
        raw_text,
        norm_title,
        source_user_id,
        status="published",
        published_message_id=pub_id,
    )
    if pub_id:
        return "Добавил в базу и опубликовал в теме «Новости»."
    return (
        "Добавил в базу. Публикация в группу не настроена "
        "(TELEGRAM_GROUP_CHAT_ID и TELEGRAM_TOPIC_NEWS или TELEGRAM_EVENTS_TOPIC_ID)."
    )
