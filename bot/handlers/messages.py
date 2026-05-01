from __future__ import annotations

import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from utils import nav_labels as N
from bot.keyboards import (
    interview_hub_keyboard,
    interview_tell_keyboard,
    invite_flow_keyboard,
    main_menu,
)
from db import repo
from services import events_service, files_service, hr_service, interview_service, ml_forward_service, routing
from services import interviews_store
from utils.telegram_user import user_display_handle
from utils.text_slug import slugify_folder

log = logging.getLogger(__name__)

_INVITE_UID = re.compile(r"^\s*(\d{6,15})\s*$")
_USERNAME_INVITE = re.compile(r"^\s*@([a-zA-Z][a-zA-Z0-9_]{4,31})\s*$")


def _message_exits_invite_only_mode(msg: Message) -> bool:
    return bool(
        msg.document
        or msg.forward_origin
        or msg.photo
        or msg.video
        or msg.audio
        or msg.video_note
    )


async def _help_reply(message: Message, is_whitelist: bool) -> None:
    text = (
        "Кратко, как пользоваться ботом\n\n"
        f"Этот текст можно открыть кнопкой «{N.BTN_GUIDE}» или командой /help.\n"
        "Внизу: «Собесы» — раздел про собеседования.\n\n"
        "— Лента хаба\n"
        "Перешли сюда сообщение (в разумных пределах по давности) — оно попадёт в общую ленту, "
        "начислятся очки. Что публиковать, решаешь ты; итоги очков дублируются в теме «Рейтинг».\n\n"
        "— Мероприятия\n"
        "Напиши текст анонса своими словами — проверю на дубли и при необходимости отправлю в тему новостей.\n\n"
        "— Контакт HR\n"
        "Сначала одним сообщением @username или числовой ID, потом контекст (можно голосом, если настроен Groq). "
        "Важно: компанию-работодателя укажи отдельно; площадку знакомства (мероприятие, Future Today и т.п.) "
        "не подставляй вместо работодателя.\n\n"
        "— Файлы\n"
        "Пришли документ, например PDF — предложу папку в библиотеке. Список и скачивание: команда /files\n\n"
        "— Голосовые\n"
        "При наличии GROQ_API_KEY в настройках распознаю речь и обработаю как обычный текст.\n\n"
        "— Собесы\n"
        "Читай чужие истории по компаниям или добавь свою — кнопка «Собесы»."
    )
    if is_whitelist:
        text += (
            f"\n\n— Приглашения\n"
            f"Кнопка «{N.BTN_INVITE}»: нужен @username человека или его числовой Telegram ID "
            "(пересылка сообщений для приглашения не используется)."
        )
    await message.reply_text(text, reply_markup=main_menu(is_whitelist))


async def _route_after_inbound(
    msg: Message,
    context: ContextTypes.DEFAULT_TYPE,
    pool,
    uid: int,
    chat_id: int,
    user_text: str,
    has_doc: bool,
    mime: str | None,
) -> None:
    tr = (msg.text or msg.caption or "").strip()
    hr_token = hr_service.try_parse_hr_contact_line(tr)
    if hr_token and not has_doc:
        await repo.abandon_awaiting_hr_drafts(pool, uid)
        _hr_id, reply = await hr_service.start_hr_contact_ref_flow(pool, uid, hr_token)
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
            contact_ref=str(draft["contact_ref"]),
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
                uploader_handle=user_display_handle(msg.from_user) if msg.from_user else None,
            )
        except Exception:
            log.exception("document pipeline")
            reply = "Ошибка при обработке файла. Попробуй ещё раз или уменьши размер."
        wl_user = await repo.is_whitelist(pool, uid)
        await msg.reply_text(reply, reply_markup=main_menu(wl_user))
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
                announcer_label=user_display_handle(msg.from_user) if msg.from_user else str(uid),
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
                contact_ref=str(draft["contact_ref"]),
                source_user_id=uid,
                chat_id=chat_id,
                text=user_text,
            )
            await msg.reply_text("Принял контекст для HR.")
            return
        await msg.reply_text(
            "Сначала пришли контакт HR одним сообщением: @username (например @ivan_hr) "
            "или числовой Telegram ID, если он у тебя есть."
        )
        return

    if intent == "file_material":
        await msg.reply_text("Пришли файл вложением (например PDF).")
        return

    await msg.reply_text(
        f"Не разобрался в задаче. Нажми «{N.BTN_GUIDE}» или опиши: мероприятие, HR (@ник и контекст), либо прикрепи файл."
    )


async def on_text_and_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    pool = context.application.bot_data["pool"]
    uid = user.id
    chat_id = msg.chat_id

    text_raw = (msg.text or msg.caption or "").strip()
    wl = await repo.is_whitelist(pool, uid)
    state, payload = await repo.get_session(pool, uid)

    if text_raw in N.GUIDE_ALIASES:
        if state in ("awaiting_invite", "interview_hub", "interview_tell"):
            await repo.clear_session(pool, uid)
        await _help_reply(msg, wl)
        return

    if wl and text_raw in N.INVITE_ALIASES:
        await repo.set_session(pool, uid, "awaiting_invite", {})
        await msg.reply_text(
            "Кого добавить в хаб?\n\n"
            "Одним сообщением пришли @username (например @ivan) или числовой Telegram ID.\n\n"
            "Пересланные сообщения для приглашения не подходят — они обрабатываются как обычный контент. "
            f"Вернуться без приглашения — «{N.BTN_CANCEL_INVITE}».",
            reply_markup=invite_flow_keyboard(),
        )
        return

    if wl and state == "awaiting_invite":
        if text_raw in N.INVITE_CANCEL_ALIASES:
            await repo.clear_session(pool, uid)
            await msg.reply_text("Ок, приглашение отменено.", reply_markup=main_menu(True))
            return

        if _message_exits_invite_only_mode(msg):
            await repo.clear_session(pool, uid)
            state, payload = "idle", {}
        else:
            invitee_id: int | None = None
            uname_m = _USERNAME_INVITE.match(text_raw)
            if uname_m:
                handle = f"@{uname_m.group(1)}"
                try:
                    chat = await context.bot.get_chat(handle)
                    if getattr(chat, "type", None) != "private":
                        await msg.reply_text(
                            "Это не личный профиль пользователя. Пришли @ник человека или числовой ID.",
                            reply_markup=invite_flow_keyboard(),
                        )
                        return
                    invitee_id = chat.id
                except TelegramError as e:
                    log.info("invite get_chat %s failed: %s", handle, e)
                    await msg.reply_text(
                        "Не нашёл пользователя с таким @ником. Проверь написание. "
                        "Если у человека нет публичного username — нужен числовой Telegram ID. "
                        "Иногда помогает, если человек один раз открыл этого бота.",
                        reply_markup=invite_flow_keyboard(),
                    )
                    return

            uid_m = _INVITE_UID.match(text_raw)
            if invitee_id is None and uid_m:
                invitee_id = int(uid_m.group(1))

            if invitee_id is not None:
                await repo.add_or_activate_member(pool, invitee_id, uid)
                await repo.clear_session(pool, uid)
                await msg.reply_text(
                    f"Пользователь {invitee_id} активирован.",
                    reply_markup=main_menu(True),
                )
                try:
                    await context.bot.send_message(
                        chat_id=invitee_id,
                        text="Тебя добавили в закрытый бот. Открой чат с ботом и нажми /start.",
                    )
                except Forbidden:
                    await msg.reply_text(
                        "Пользователь добавлен в базу, но я не могу написать ему первым: "
                        "пусть сам откроет бота и нажмёт /start."
                    )
                except Exception:
                    log.warning("notify invitee failed", exc_info=True)
                return

            await repo.clear_session(pool, uid)
            state, payload = "idle", {}

    st = await repo.member_status(pool, uid)
    if st != "active":
        await msg.reply_text(
            "Пока нет доступа: попроси кого-то из участников с правом приглашения нажать "
            f"«{N.BTN_INVITE}» и указать твой @username или числовой ID."
        )
        return

    if await ml_forward_service.user_context_allows_hub_forward(pool, uid, state):
        if await ml_forward_service.try_handle_forward(msg, context, pool, uid):
            return

    pending_hr = await repo.get_hr_pending_confirm_for_user(pool, uid)
    if pending_hr and not msg.document:
        await msg.reply_text(
            "Сначала ответь на черновик HR кнопками «Верно» или «Нет» под тем сообщением."
        )
        return

    if text_raw == N.BTN_INTERVIEWS:
        await repo.set_session(pool, uid, "interview_hub", {})
        await msg.reply_text(
            "Раздел про собеседования\n\n"
            f"«{N.BTN_READ_INTERVIEWS}» — выбери компанию и получи файл с рассказами.\n"
            f"«{N.BTN_SHARE_INTERVIEW}» — пошагово оформим твой опыт.\n"
            f"«{N.BTN_BACK_HOME}» — вернуться к обычному меню.",
            reply_markup=interview_hub_keyboard(),
        )
        return

    if state == "interview_hub":
        if text_raw in N.BACK_HOME_ALIASES:
            await repo.clear_session(pool, uid)
            await msg.reply_text("Возвращаю в главное меню.", reply_markup=main_menu(wl))
            return
        if text_raw in N.READ_ALIASES:
            companies = interviews_store.list_companies()
            if not companies:
                await msg.reply_text(
                    f"Пока никто не делился опытом. Можешь начать — «{N.BTN_SHARE_INTERVIEW}».",
                    reply_markup=interview_hub_keyboard(),
                )
                return
            rows: list[list[InlineKeyboardButton]] = []
            row_btns: list[InlineKeyboardButton] = []
            for _i, (slug, title) in enumerate(companies):
                label = title if len(title) <= 30 else title[:27] + "…"
                row_btns.append(InlineKeyboardButton(label, callback_data=f"ivd:{slug}"))
                if len(row_btns) >= 2:
                    rows.append(row_btns)
                    row_btns = []
            if row_btns:
                rows.append(row_btns)
            await msg.reply_text(
                "Выбери компанию — пришлю файл .md с собранными рассказами:",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return
        if text_raw in N.SHARE_ALIASES:
            await repo.set_session(pool, uid, "interview_tell", {"interview_lines": []})
            await msg.reply_text(
                interview_service.WELCOME_TELL,
                reply_markup=interview_tell_keyboard(),
            )
            return
        await msg.reply_text(
            f"Выбери действие кнопкой ниже или нажми «{N.BTN_BACK_HOME}».",
            reply_markup=interview_hub_keyboard(),
        )
        return

    if state == "interview_tell":
        if text_raw in N.CANCEL_FLOW_ALIASES:
            await repo.clear_session(pool, uid)
            await msg.reply_text("Вышли без сохранения. Если передумаешь — снова «Собесы».", reply_markup=main_menu(wl))
            return
        if text_raw in N.DONE_ALIASES:
            lines = list(payload.get("interview_lines") or [])
            ok, reply_txt = await interview_service.finalize_story(
                pool, user, lines, bot=context.bot
            )
            if ok:
                await repo.clear_session(pool, uid)
                await msg.reply_text(reply_txt, reply_markup=main_menu(wl))
            else:
                await msg.reply_text(reply_txt, reply_markup=interview_tell_keyboard())
            return
        if text_raw:
            pl = dict(payload)
            lines = list(pl.get("interview_lines") or [])
            lines.append(text_raw)
            pl["interview_lines"] = lines
            await repo.set_session(pool, uid, "interview_tell", pl)
            await msg.reply_text(
                f"Записал. Можно дополнить текстом или голосом, затем «{N.BTN_STORY_DONE}».",
                reply_markup=interview_tell_keyboard(),
            )
            return
        await msg.reply_text(
            "Пришли текст или голосовое с описанием собеседования.",
            reply_markup=interview_tell_keyboard(),
        )
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

    if state == "awaiting_folder_name" and text_raw and not has_doc:
        fid = payload.get("file_id")
        if fid:
            slug = slugify_folder(text_raw)
            label = text_raw.strip()[:120]
            await repo.ensure_file_category(pool, slug, label, uid)
            ok = await files_service.finalize_file_to_library(
                pool,
                file_id=int(fid),
                user_id=uid,
                slug=slug,
                label_ru=label,
                bot=context.bot,
                announcer_label=user_display_handle(user),
            )
            await repo.clear_session(pool, uid)
            if ok:
                await msg.reply_text(f"Файл сохранён в папке «{label}» (код {slug}).")
            else:
                await msg.reply_text("Не удалось: нет файла или чужая запись.")
        return

    await _route_after_inbound(msg, context, pool, uid, chat_id, user_text, has_doc, mime)
