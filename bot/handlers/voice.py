from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.messages import _route_after_inbound
from config import get_settings
from db import repo
from services import groq_voice, llm

log = logging.getLogger(__name__)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not update.effective_user or not msg.voice:
        return
    pool = context.application.bot_data["pool"]
    uid = update.effective_user.id
    chat_id = msg.chat_id
    settings = get_settings()

    state, _ = await repo.get_session(pool, uid)
    if state == "awaiting_invite":
        await repo.clear_session(pool, uid)

    st = await repo.member_status(pool, uid)
    if st != "active":
        await msg.reply_text("Нет доступа.")
        return

    pending_hr = await repo.get_hr_pending_confirm_for_user(pool, uid)
    if pending_hr:
        await msg.reply_text("Сначала ответь по черновику HR кнопками.")
        return

    if not settings.groq_api_key:
        await msg.reply_text("Голосовые не настроены: добавь GROQ_API_KEY в .env и перезапусти бота.")
        return

    try:
        vf = await context.bot.get_file(msg.voice.file_id)
        audio = bytes(await vf.download_as_bytearray())
        transcript = await groq_voice.transcribe_ogg_opus(audio, "voice.ogg")
    except Exception:
        log.exception("voice transcribe")
        await msg.reply_text("Не удалось распознать голос. Попробуй ещё раз или напиши текстом.")
        return

    if not transcript:
        await msg.reply_text("Пустая расшифровка.")
        return

    gist = ""
    try:
        g = await llm.voice_gist(pool, transcript)
        gist = str(g.get("gist_ru") or "").strip()
    except Exception:
        log.exception("voice_gist")

    await repo.log_inbound(
        pool,
        uid,
        chat_id,
        msg.message_id,
        f"[голосовое] {transcript[:3500]}",
        False,
        None,
        "voice/ogg",
    )

    header = f"🎤 Распознал:\n{transcript[:3500]}"
    if gist:
        header = f"🎤 {gist}\n\nТекст:\n{transcript[:3500]}"
    await msg.reply_text(header + ("\n…" if len(transcript) > 3500 else ""))

    await _route_after_inbound(msg, context, pool, uid, chat_id, transcript, False, None)
