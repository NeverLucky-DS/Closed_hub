from __future__ import annotations

import logging
import re

from telegram import Bot, Message
from telegram.constants import ParseMode
from telegram.ext import Application

from config import get_settings
from db import repo
from services import activity, event_summary_worker, llm
from services.file_storage import events_covers_root

log = logging.getLogger(__name__)

_MAX_EVENT_COVER_BYTES = 4 * 1024 * 1024

_FORWARD_MARKERS = ("[пересланное сообщение]", "[переслано]")


def _fallback_event_title(raw_text: str) -> str:
    t = (raw_text or "").strip()
    for m in _FORWARD_MARKERS:
        if t.startswith(m):
            t = t[len(m) :].lstrip()
    line = t.split("\n", 1)[0].strip() if t else ""
    if not line:
        return "Анонс"
    return (line[:200] + "…") if len(line) > 200 else line


def _normalize_raw_for_exact_dup(raw_text: str) -> str:
    t = (raw_text or "").strip()
    return re.sub(r"\s+", " ", t)


def _document_is_image(doc) -> bool:
    if not doc:
        return False
    mime = (doc.mime_type or "").lower()
    name = (doc.file_name or "").lower()
    if mime.startswith("image/"):
        return True
    return any(name.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))


def message_has_visual_cover(msg: Message) -> bool:
    if msg.photo or msg.animation:
        return True
    if msg.video and msg.video.thumbnail:
        return True
    if msg.document and _document_is_image(msg.document):
        return True
    return False


async def save_event_cover_from_telegram_message(bot: Bot, pool, event_id: int, msg: Message) -> None:
    """Тянет превью/фото из сообщения Telegram и пишет в storage + cover_image_path."""
    file_id: str | None = None
    suffix = ".jpg"

    if msg.photo:
        file_id = msg.photo[-1].file_id
        suffix = ".jpg"
    elif msg.animation:
        anim = msg.animation
        if anim.thumbnail:
            file_id = anim.thumbnail.file_id
            suffix = ".jpg"
        else:
            try:
                tf_meta = await bot.get_file(anim.file_id)
            except Exception:
                log.warning("event_cover: animation meta failed event_id=%s", event_id, exc_info=True)
                return
            if tf_meta.file_size and tf_meta.file_size > _MAX_EVENT_COVER_BYTES:
                log.info("event_cover: animation file too large event_id=%s", event_id)
                return
            mime_a = (anim.mime_type or "").lower()
            if "gif" in mime_a or mime_a == "image/gif":
                file_id = anim.file_id
                suffix = ".gif"
            else:
                log.info(
                    "event_cover: animation without jpg thumb, not gif event_id=%s mime=%s",
                    event_id,
                    mime_a,
                )
                return
    elif msg.video and msg.video.thumbnail:
        file_id = msg.video.thumbnail.file_id
        suffix = ".jpg"
    elif msg.document and _document_is_image(msg.document):
        file_id = msg.document.file_id
        mime = (msg.document.mime_type or "").lower()
        name = (msg.document.file_name or "").lower()
        if "png" in mime or name.endswith(".png"):
            suffix = ".png"
        elif "webp" in mime or name.endswith(".webp"):
            suffix = ".webp"
        elif "gif" in mime or name.endswith(".gif"):
            suffix = ".gif"
        else:
            suffix = ".jpg"
    else:
        log.info("event_cover: no image media event_id=%s", event_id)
        return

    try:
        tg_file = await bot.get_file(file_id)
    except Exception:
        log.warning("event_cover: get_file failed event_id=%s", event_id, exc_info=True)
        return

    if tg_file.file_size and tg_file.file_size > _MAX_EVENT_COVER_BYTES:
        log.warning("event_cover: skip huge file event_id=%s size=%s", event_id, tg_file.file_size)
        return

    try:
        data = bytes(await tg_file.download_as_bytearray())
    except Exception:
        log.warning("event_cover: download failed event_id=%s", event_id, exc_info=True)
        return

    if len(data) > _MAX_EVENT_COVER_BYTES:
        return

    root = events_covers_root()
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{event_id}{suffix}"
    dest.write_bytes(data)
    rel = f"events/covers/{event_id}{suffix}"
    await repo.update_event_cover_path(pool, event_id, rel)
    log.info("event_cover: saved event_id=%s %s bytes=%s", event_id, rel, len(data))


async def try_ingest_forward_to_site(
    pool,
    application: Application,
    source_user_id: int,
    raw_text: str,
) -> int | None:
    """
    После пересылки в группу (ml_forward): insert + очередь саммари.
    Дедуп только по точному совпадению нормализованного raw_text с недавними событиями —
    LLM-дедуп здесь давал ложные «дубликаты» для разных постов с канала.
    Ручные анонсы по-прежнему проходят через llm.dedup_event в handle_event_message.
    Без второго начисления event_published (очки уже за ml_forward_shared).
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        log.info("hub_site_sync: skip empty raw uid=%s", source_user_id)
        return None

    log.info(
        "hub_site_sync: ingest uid=%s text_len=%s head=%r",
        source_user_id,
        len(raw_text),
        raw_text[:80].replace("\n", " "),
    )
    key = _normalize_raw_for_exact_dup(raw_text)
    recent = await repo.recent_events_texts(pool, 80)
    if key and any(key == _normalize_raw_for_exact_dup(t) for t in recent if t):
        log.warning(
            "hub_site_sync: skip exact duplicate uid=%s head=%r",
            source_user_id,
            raw_text[:80].replace("\n", " "),
        )
        return None

    title_for_db = _fallback_event_title(raw_text)
    event_id = await repo.insert_event(
        pool,
        raw_text,
        title_for_db,
        source_user_id,
        status="published",
        published_message_id=None,
    )
    log.info("hub_site_sync: inserted event_id=%s uid=%s", event_id, source_user_id)
    try:
        await event_summary_worker.enqueue_event_summary(application, event_id, raw_text)
    except Exception:
        log.exception("hub_site_sync: enqueue failed event_id=%s", event_id)
    return event_id


async def handle_event_message(
    pool,
    bot: Bot,
    application: Application,
    *,
    source_user_id: int,
    raw_text: str,
    announcer_label: str,
    source_message: Message | None = None,
) -> str:
    settings = get_settings()
    recent = await repo.recent_events_texts(pool, 25)
    dedup = await llm.dedup_event(pool, raw_text, recent)
    should_add = bool(dedup.get("should_add")) and not bool(dedup.get("is_duplicate"))
    normalized = dedup.get("normalized_title")
    norm_title = str(normalized).strip() if normalized else None

    if not should_add:
        return (
            "Похоже, такое мероприятие уже есть в базе или текст не похож на анонс. "
            "Если это ошибка — переформулируй и отправь снова."
        )

    title_for_db = norm_title or _fallback_event_title(raw_text)

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

    event_id = await repo.insert_event(
        pool,
        raw_text,
        title_for_db,
        source_user_id,
        status="published",
        published_message_id=pub_id,
    )

    try:
        await event_summary_worker.enqueue_event_summary(application, event_id, raw_text)
    except Exception:
        log.exception("enqueue event summary failed event_id=%s", event_id)

    if source_message:
        try:
            await save_event_cover_from_telegram_message(bot, pool, event_id, source_message)
        except Exception:
            log.exception("event_cover failed event_id=%s", event_id)

    await activity.award(
        pool,
        source_user_id,
        "event_published",
        {"has_forum_post": bool(pub_id)},
        bot=bot,
        announcer_label=announcer_label,
    )
    if pub_id:
        return (
            "Добавил в базу (сразу на сайт), саммари догонит в фоне. "
            "Опубликовал в теме «Новости»."
        )
    return (
        "Добавил в базу (сразу на сайт), саммари догонит в фоне. "
        "Публикация в группу не настроена "
        "(TELEGRAM_GROUP_CHAT_ID и TELEGRAM_TOPIC_NEWS или TELEGRAM_EVENTS_TOPIC_ID)."
    )
