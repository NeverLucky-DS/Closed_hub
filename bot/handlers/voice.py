from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from utils import nav_labels as N
from bot.handlers.messages import _route_after_inbound
from bot.keyboards import interview_hub_keyboard, interview_tell_keyboard
from config import get_settings
from db import repo
from services import groq_voice, llm, ml_forward_service

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

    session_state, _ = await repo.get_session(pool, uid)

    st = await repo.member_status(pool, uid)
    if st != "active":
        await msg.reply_text("Нет доступа.")
        return

    if await ml_forward_service.user_context_allows_hub_forward(pool, uid, session_state):
        if await ml_forward_service.try_handle_forward(msg, context, pool, uid):
            return

    pending_hr = await repo.get_hr_pending_confirm_for_user(pool, uid)
    if pending_hr:
        await msg.reply_text(
            "Сначала нажми «Верно» или «Нет» под черновиком HR — без этого дальше не пойдём."
        )
        return

    st_iv, _ = await repo.get_session(pool, uid)
    if st_iv == "interview_hub":
        await msg.reply_text(
            f"В этом шаге удобнее кнопки: «{N.BTN_READ_INTERVIEWS}» или «{N.BTN_SHARE_INTERVIEW}».",
            reply_markup=interview_hub_keyboard(),
        )
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

    state, payload = await repo.get_session(pool, uid)
    if state == "interview_tell":
        pl = dict(payload)
        lines = list(pl.get("interview_lines") or [])
        lines.append(transcript.strip())
        pl["interview_lines"] = lines
        await repo.set_session(pool, uid, "interview_tell", pl)
        await msg.reply_text(
            f"Добавил фрагмент из голосового. Продолжай или нажми «{N.BTN_STORY_DONE}».",
            reply_markup=interview_tell_keyboard(),
        )
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
