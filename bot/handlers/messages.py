from __future__ import annotations

import logging
import re
import time

from telegram import Message, MessageOriginUser, Update
from telegram.ext import ContextTypes

from bot.keyboards import main_menu
from db import repo
from services import events_service, files_service, hr_service, routing

log = logging.getLogger(__name__)

_UID_ONLY = re.compile(r"^\s*(\d{6,12})\s*$")


async def _help_reply(message: Message, is_whitelist: bool) -> None:
    text = (
        "Что можно отправить:\n"
        "• Пересланный или набранный текст про мероприятие — проверю дубликаты и опубликую в теме.\n"
        "• Сначала числовой Telegram UID HR отдельным сообщением, затем контекст одним или несколькими сообщениями.\n"
        "• PDF или другой файл — кратко опишу и предложу категорию.\n"
    )
    if is_whitelist:
        text += "\nТы можешь добавлять людей через «Добавить участника»."
    await message.reply_text(text, reply_markup=main_menu(is_whitelist))


async def on_text_and_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not update.effective_user:
        return
    pool = context.application.bot_data["pool"]
    uid = update.effective_user.id
    chat_id = msg.chat_id

    text_raw = (msg.text or msg.caption or "").strip()
    wl = await repo.is_whitelist(pool, uid)
    state, _payload = await repo.get_session(pool, uid)

    if text_raw in ("Справка", "Что отправить"):
        await _help_reply(msg, wl)
        return

    if wl and text_raw == "Добавить участника":
        await repo.set_session(pool, uid, "awaiting_invite", {})
        await msg.reply_text(
            "Перешли любое сообщение от человека (чтобы был виден отправитель) "
            "или пришли его числовой Telegram ID."
        )
        return

    if wl and state == "awaiting_invite":
        invitee_id: int | None = None
        if msg.forward_origin and isinstance(msg.forward_origin, MessageOriginUser):
            su = msg.forward_origin.sender_user
            if su:
                invitee_id = su.id
        if invitee_id is None and text_raw.isdigit() and 6 <= len(text_raw) <= 12:
            invitee_id = int(text_raw)
        if invitee_id is None:
            await msg.reply_text(
                "Не получилось определить пользователя. Перешли сообщение с видимым отправителем "
                "или пришли UID (обычно 6–12 цифр)."
            )
            return
        await repo.add_or_activate_member(pool, invitee_id, uid)
        await repo.clear_session(pool, uid)
        await msg.reply_text(f"Пользователь {invitee_id} активирован.")
        try:
            await context.bot.send_message(
                chat_id=invitee_id,
                text="Тебя добавили в закрытый бот. Открой чат с ботом и нажми /start.",
            )
        except Exception:
            log.exception("notify invitee failed")
        return

    st = await repo.member_status(pool, uid)
    if st != "active":
        await msg.reply_text("Нет доступа. Нужно приглашение от участника из белого списка.")
        return

    pending_hr = await repo.get_hr_pending_confirm_for_user(pool, uid)
    if pending_hr and not msg.document:
        await msg.reply_text("Сначала подтверди или отклони черновик HR кнопками под прошлым сообщением.")
        return

    parts: list[str] = []
    if msg.forward_origin:
        parts.append("[пересланное сообщение]")
    if msg.text or msg.caption:
        parts.append((msg.text or msg.caption or "").strip())
    user_text = "\n".join(parts).strip()

    has_doc = bool(msg.document)
    mime = msg.document.mime_type if msg.document else None
    file_id = msg.document.file_id if msg.document else None

    await repo.log_inbound(
        pool,
        uid,
        chat_id,
        msg.message_id,
        user_text or None,
        has_doc,
        file_id,
        mime,
    )

    if text_raw and _UID_ONLY.match(text_raw) and not has_doc:
        await repo.abandon_awaiting_hr_drafts(pool, uid)
        tg_uid = int(text_raw)
        _hr_id, reply = await hr_service.start_hr_uid_flow(pool, uid, tg_uid)
        await msg.reply_text(reply)
        return

    draft = await repo.get_open_hr_draft_for_user(pool, uid)
    if draft and has_doc:
        draft = None

    if draft and user_text and not has_doc:
        await hr_service.append_hr_context_and_schedule(
            context.application,
            pool,
            hr_contact_id=int(draft["id"]),
            telegram_uid=int(draft["telegram_uid"]),
            source_user_id=uid,
            chat_id=chat_id,
            text=user_text,
        )
        await msg.reply_text("Принял контекст. Если нужно — дополни; соберу черновик после короткой паузы.")
        return

    if routing.heuristic_route(user_text or None, has_doc, mime) is None:
        rl = context.application.bot_data.setdefault("route_rl", {})
        now = time.time()
        if now - rl.get(uid, 0) < 2.0:
            await msg.reply_text("Слишком частые запросы к ИИ. Подожди пару секунд.")
            return
        rl[uid] = now

    if has_doc and msg.document:
        doc = msg.document

        async def get_bytes() -> bytes:
            tg_file = await context.bot.get_file(doc.file_id)
            return bytes(await tg_file.download_as_bytearray())

        try:
            reply = await files_service.handle_document(
                pool,
                context.bot,
                user_id=uid,
                chat_id=chat_id,
                file_id=doc.file_id,
                mime_type=doc.mime_type,
                file_name=doc.file_name,
                get_file_bytes=get_bytes,
            )
        except Exception:
            log.exception("document pipeline")
            reply = "Ошибка при обработке файла. Попробуй ещё раз или уменьши размер."
        await msg.reply_text(reply)
        return

    intent = await routing.route_intent(pool, user_text or None, has_doc, mime)

    if intent == "event":
        if not user_text:
            await msg.reply_text("Пришли текст анонса или пересланное сообщение.")
            return
        try:
            reply = await events_service.handle_event_message(
                pool,
                context.bot,
                source_user_id=uid,
                raw_text=user_text,
            )
        except Exception:
            log.exception("event pipeline")
            reply = "Не удалось обработать мероприятие. Попробуй позже."
        await msg.reply_text(reply)
        return

    if intent == "hr_contact":
        if draft:
            await hr_service.append_hr_context_and_schedule(
                context.application,
                pool,
                hr_contact_id=int(draft["id"]),
                telegram_uid=int(draft["telegram_uid"]),
                source_user_id=uid,
                chat_id=chat_id,
                text=user_text,
            )
            await msg.reply_text("Принял контекст для HR.")
            return
        await msg.reply_text("Сначала пришли числовой Telegram UID контакта HR одним сообщением (только цифры).")
        return

    if intent == "file_material":
        await msg.reply_text("Пришли файл вложением (например PDF).")
        return

    await msg.reply_text(
        "Не понял задачу. Нажми «Справка» или опиши: мероприятие, HR UID + контекст, или прикрепи PDF."
    )
