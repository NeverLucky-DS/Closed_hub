from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from telegram import Message
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import get_settings
from db import repo
from services import activity, events_service
from utils.telegram_user import user_display_handle

log = logging.getLogger(__name__)

_MG_PENDING_KEY = "_ml_forward_mg_pending"
_MG_FLUSH_DELAY_SEC = 0.9


def _forward_snippet(msg: Message) -> str:
    chunks: list[str] = []
    if msg.text:
        chunks.append(msg.text.strip())
    if msg.caption:
        chunks.append(msg.caption.strip())
    if msg.photo:
        chunks.append("[фото]")
    if msg.video:
        chunks.append("[видео]")
    if msg.document:
        name = msg.document.file_name or "файл"
        chunks.append(f"[документ: {name}]")
    if msg.audio:
        chunks.append("[аудио]")
    if msg.voice:
        chunks.append("[голосовое]")
    if msg.video_note:
        chunks.append("[видеокружок]")
    if msg.poll:
        chunks.append(f"[опрос: {msg.poll.question}]")
    if not chunks:
        chunks.append("[без текста, только медиа/стикер]")
    return "\n".join(chunks)[:6500]


def forward_raw_for_site(messages: list[Message]) -> str:
    """Текст для таблицы events (как в личке: префикс + подписи альбома)."""
    if not messages:
        return ""
    ordered = sorted(messages, key=lambda m: m.message_id)
    bodies: list[str] = []
    for m in ordered:
        bits: list[str] = []
        if m.text:
            bits.append(m.text.strip())
        if m.caption:
            bits.append(m.caption.strip())
        if bits:
            bodies.append("\n".join(bits))
    if not bodies:
        body = _forward_snippet(ordered[0])
    else:
        body = "\n\n".join(bodies) if len(bodies) > 1 else bodies[0]
    if ordered[0].forward_origin:
        return f"[пересланное сообщение]\n{body}".strip()
    return body.strip()


_HUB_FORWARD_BLOCK_SESSIONS = frozenset(
    {"interview_tell", "interview_hub", "awaiting_folder_name", "awaiting_invite"}
)


async def user_context_allows_hub_forward(pool, uid: int, session_state: str) -> bool:
    """Не перехватывать пересланное в ленту, пока идёт другой пошаговый сценарий или сбор контекста HR."""
    if session_state in _HUB_FORWARD_BLOCK_SESSIONS:
        return False
    if await repo.get_open_hr_draft_for_user(pool, uid):
        return False
    return True


async def _forward_precheck(msg: Message, pool, uid: int) -> str | None:
    """Текст ошибки для пользователя или None, если можно пересылать."""
    settings = get_settings()
    if settings.telegram_group_chat_id is None:
        return "Пересылка в хаб не настроена: задай TELEGRAM_GROUP_CHAT_ID в .env."
    if settings.ml_forward_publish_topic_id is None:
        return (
            "Не настроена тема для пересылок (TELEGRAM_TOPIC_ML_FORWARD или TELEGRAM_TOPIC_NEWS)."
        )
    orig = msg.forward_origin
    fwd_date = orig.date
    if fwd_date.tzinfo is None:
        fwd_date = fwd_date.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age_days = (now - fwd_date).days
    if age_days > settings.ml_forward_max_age_days:
        return (
            f"Сообщение слишком старое для ленты (~{age_days} дн., лимит {settings.ml_forward_max_age_days}). "
            "Перешли что-то свежее или опиши текстом."
        )
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    n_today = await repo.count_activity_reason_since(pool, uid, "ml_forward_shared", day_start)
    if n_today >= settings.ml_forward_daily_cap:
        return (
            f"Сегодня уже много пересылок в ленту (лимит {settings.ml_forward_daily_cap}). Завтра снова."
        )
    return None


def _mg_key(msg: Message) -> tuple[int, str]:
    return (msg.chat_id, str(msg.media_group_id))


async def _flush_media_group_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    key: tuple[int, str] | None = context.job.data if context.job else None
    if key is None:
        return
    pending: dict[tuple[int, str], dict[str, Any]] = context.application.bot_data.setdefault(
        _MG_PENDING_KEY, {}
    )
    buf = pending.pop(key, None)
    if not buf:
        return

    ids: list[int] = sorted(buf["ids"])
    first: Message = buf["first"]
    last: Message = buf["last"]
    uid: int = buf["uid"]
    pool = context.application.bot_data["pool"]
    settings = get_settings()
    topic_id = settings.ml_forward_publish_topic_id
    assert settings.telegram_group_chat_id is not None and topic_id is not None

    orig = first.forward_origin
    fwd_date = orig.date
    if fwd_date.tzinfo is None:
        fwd_date = fwd_date.replace(tzinfo=timezone.utc)
    date_s = fwd_date.strftime("%Y-%m-%d %H:%M UTC")

    try:
        await context.bot.forward_messages(
            chat_id=settings.telegram_group_chat_id,
            from_chat_id=first.chat_id,
            message_ids=ids,
            message_thread_id=topic_id,
        )
    except TelegramError as e:
        log.warning("forward_messages failed: %s", e)
        await last.reply_text(
            "Не смог переслать в группу: проверь, что бот в супергруппе и может писать в эту тему."
        )
        return

    snippet = _forward_snippet(first)
    if len(ids) > 1:
        snippet = f"{snippet}\n[+ещё {len(ids) - 1}]"
    who = user_display_handle(last.from_user) if last.from_user else str(uid)
    short = (snippet.split("\n")[0] if snippet else "")[:160]
    pts = await activity.award(
        pool,
        uid,
        "ml_forward_shared",
        {"snippet_head": short, "forward_date": date_s},
        bot=context.bot,
        announcer_label=who,
    )
    total = await repo.get_member_activity_points(pool, uid)
    msg_list = buf.get("messages") or [first]
    msgs = sorted({m.message_id: m for m in msg_list}.values(), key=lambda m: m.message_id)
    raw_site = forward_raw_for_site(msgs)
    site_id = await events_service.try_ingest_forward_to_site(pool, context.application, uid, raw_site)
    log.info(
        "ml_forward album ok msg_ids=%s site_event_id=%s raw_len=%s",
        ids,
        site_id,
        len(raw_site),
    )
    if site_id:
        cover_src = next((m for m in msgs if events_service.message_has_visual_cover(m)), None)
        if cover_src:
            try:
                await events_service.save_event_cover_from_telegram_message(
                    context.bot, pool, site_id, cover_src
                )
            except Exception:
                log.exception("ml_forward album event_cover failed event_id=%s", site_id)
    site_note = " Также добавил в базу для сайта (Новости)." if site_id else ""
    await last.reply_text(
        "Переслал в ленту хаба как есть (отправитель виден). "
        f"+{pts} очков, всего {total}. Содержание решаешь ты — лишь бы было уместно для сообщества."
        f"{site_note}"
    )


async def _handle_forwarded_media_group(
    msg: Message,
    context: ContextTypes.DEFAULT_TYPE,
    pool,
    uid: int,
) -> bool:
    jq = context.application.job_queue
    if jq is None:
        log.error("JobQueue отсутствует — нужен python-telegram-bot[ext] для альбомов")
        await msg.reply_text("Не могу собрать альбом: внутренняя ошибка бота. Напиши админу.")
        return True

    key = _mg_key(msg)
    pending: dict[tuple[int, str], dict[str, Any]] = context.application.bot_data.setdefault(
        _MG_PENDING_KEY, {}
    )

    if key not in pending:
        err = await _forward_precheck(msg, pool, uid)
        if err:
            await msg.reply_text(err)
            return True
        pending[key] = {
            "ids": set(),
            "messages": [],
            "first": msg,
            "last": msg,
            "uid": uid,
            "job": None,
        }

    buf = pending[key]
    buf["ids"].add(msg.message_id)
    buf["messages"].append(msg)
    buf["last"] = msg

    prev_job = buf.get("job")
    if prev_job:
        prev_job.schedule_removal()

    job_name = f"ml_mg_fwd_{key[0]}_{key[1]}"
    job = jq.run_once(
        _flush_media_group_job,
        _MG_FLUSH_DELAY_SEC,
        data=key,
        name=job_name,
    )
    buf["job"] = job
    return True


async def try_handle_forward(
    msg: Message,
    context: ContextTypes.DEFAULT_TYPE,
    pool,
    uid: int,
) -> bool:
    if not msg.forward_origin:
        return False

    settings = get_settings()
    if settings.telegram_group_chat_id is None:
        await msg.reply_text(
            "Пересылка в хаб не настроена: задай TELEGRAM_GROUP_CHAT_ID в .env."
        )
        return True

    if settings.ml_forward_publish_topic_id is None:
        await msg.reply_text(
            "Не настроена тема для пересылок (TELEGRAM_TOPIC_ML_FORWARD или TELEGRAM_TOPIC_NEWS)."
        )
        return True

    if msg.media_group_id is not None:
        return await _handle_forwarded_media_group(msg, context, pool, uid)

    err = await _forward_precheck(msg, pool, uid)
    if err:
        await msg.reply_text(err)
        return True

    orig = msg.forward_origin
    fwd_date = orig.date
    if fwd_date.tzinfo is None:
        fwd_date = fwd_date.replace(tzinfo=timezone.utc)
    snippet = _forward_snippet(msg)
    date_s = fwd_date.strftime("%Y-%m-%d %H:%M UTC")

    try:
        await context.bot.forward_message(
            chat_id=settings.telegram_group_chat_id,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
            message_thread_id=settings.ml_forward_publish_topic_id,
        )
    except TelegramError as e:
        log.warning("forward_message failed: %s", e)
        await msg.reply_text(
            "Не смог переслать в группу: проверь, что бот в супергруппе и может писать в эту тему."
        )
        return True

    who = user_display_handle(msg.from_user) if msg.from_user else str(uid)
    short = (snippet.split("\n")[0] if snippet else "")[:160]
    pts = await activity.award(
        pool,
        uid,
        "ml_forward_shared",
        {"snippet_head": short, "forward_date": date_s},
        bot=context.bot,
        announcer_label=who,
    )
    total = await repo.get_member_activity_points(pool, uid)
    raw_site = forward_raw_for_site([msg])
    site_id = await events_service.try_ingest_forward_to_site(pool, context.application, uid, raw_site)
    log.info(
        "ml_forward single ok mid=%s site_event_id=%s raw_len=%s",
        msg.message_id,
        site_id,
        len(raw_site),
    )
    if site_id and events_service.message_has_visual_cover(msg):
        try:
            await events_service.save_event_cover_from_telegram_message(
                context.bot, pool, site_id, msg
            )
        except Exception:
            log.exception("ml_forward event_cover failed event_id=%s", site_id)
    site_note = " Также добавил в базу для сайта (Новости)." if site_id else ""
    await msg.reply_text(
        "Переслал в ленту хаба как есть (отправитель виден). "
        f"+{pts} очков, всего {total}. Содержание решаешь ты — лишь бы было уместно для сообщества."
        f"{site_note}"
    )
    return True
