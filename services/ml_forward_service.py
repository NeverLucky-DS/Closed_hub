from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import Message
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import get_settings
from db import repo
from services import activity
from utils.telegram_user import user_display_handle

log = logging.getLogger(__name__)


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

    topic_id = settings.ml_forward_publish_topic_id
    if topic_id is None:
        await msg.reply_text(
            "Не настроена тема для пересылок (TELEGRAM_TOPIC_ML_FORWARD или TELEGRAM_TOPIC_NEWS)."
        )
        return True

    orig = msg.forward_origin
    fwd_date = orig.date
    if fwd_date.tzinfo is None:
        fwd_date = fwd_date.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age_days = (now - fwd_date).days
    if age_days > settings.ml_forward_max_age_days:
        await msg.reply_text(
            f"Сообщение слишком старое для ленты (~{age_days} дн., лимит {settings.ml_forward_max_age_days}). "
            "Перешли что-то свежее или опиши текстом."
        )
        return True

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    n_today = await repo.count_activity_reason_since(pool, uid, "ml_forward_shared", day_start)
    if n_today >= settings.ml_forward_daily_cap:
        await msg.reply_text(
            f"Сегодня уже много пересылок в ленту (лимит {settings.ml_forward_daily_cap}). Завтра снова."
        )
        return True

    snippet = _forward_snippet(msg)
    date_s = fwd_date.strftime("%Y-%m-%d %H:%M UTC")

    try:
        await context.bot.forward_message(
            chat_id=settings.telegram_group_chat_id,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
            message_thread_id=topic_id,
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
    await msg.reply_text(
        "Переслал в ленту хаба как есть (отправитель виден). "
        f"+{pts} очков, всего {total}. Содержание решаешь ты — лишь бы было уместно для сообщества."
    )
    return True
