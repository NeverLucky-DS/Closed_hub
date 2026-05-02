from __future__ import annotations

import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageOriginUser, Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from config import get_settings
from utils import nav_labels as N
from bot.keyboards import (
    interview_confirm_keyboard,
    interview_hub_keyboard,
    interview_tell_keyboard,
    invite_flow_keyboard,
    main_menu,
)
from db import repo
from services import company_sync, events_service, files_service, hr_service, interview_service, llm, ml_forward_service, routing
from services import interviews_store
from utils.telegram_user import user_display_handle
from utils.text_slug import slugify_folder

log = logging.getLogger(__name__)

_INVITE_UID = re.compile(r"^\s*(\d{6,15})\s*$")
_USERNAME_INVITE = re.compile(r"^\s*@([a-zA-Z][a-zA-Z0-9_]{4,31})\s*$")


def _invitee_id_from_forward(msg: Message) -> int | None:
    o = msg.forward_origin
    if o is None:
        return None
    if isinstance(o, MessageOriginUser):
        try:
            return int(o.sender_user.id)
        except (TypeError, AttributeError, ValueError):
            return None
    return None


def _is_hr_context_cancel(text: str) -> bool:
    return (text or "").strip().lower() in N.HR_CANCEL_ALIASES


async def _help_reply(message: Message, is_whitelist: bool) -> None:
    text = (
        "Кратко, как пользоваться ботом\n\n"
        f"Этот текст можно открыть кнопкой «{N.BTN_GUIDE}» или командой /help.\n"
        "Внизу: «Собесы» — раздел про собеседования.\n\n"
        "— Лента хаба\n"
        "Перешли сюда сообщение (в разумных пределах по давности) — оно попадёт в общую ленту, "
        "начислятся очки. Что публиковать, решаешь ты; итоги очков дублируются в теме «Рейтинг».\n\n"
        "— Новости (анонсы)\n"
        "Напиши текст анонса своими словами — проверю на дубли и при необходимости отправлю в тему новостей; на сайте это попадёт в ленту.\n\n"
        "— Файлы\n"
        "Пришли документ, например PDF — предложу папку в библиотеке. Список и скачивание: команда /files\n\n"
        "— Голосовые\n"
        "Распознаю речь и обработаю как обычный текст.\n\n"
        "— Собесы\n"
        "Читай истории наших слонов по компаниям или добавь свою — кнопка «Собесы»."
    )
    if is_whitelist:
        text += (
            f"\n\n— Приглашения\n"
            f"Кнопка «{N.BTN_INVITE}»: перешли любое сообщение от человека или пришли @username / числовой Telegram ID. "
            f"«{N.BTN_CANCEL_INVITE}» — выйти без приглашения."
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
        await msg.reply_text(reply, reply_markup=hr_service.hr_draft_cancel_keyboard(_hr_id))
        return

    draft = await repo.get_open_hr_draft_for_user(pool, uid)
    if draft and has_doc:
        draft = None

    if draft and user_text and not has_doc:
        if _is_hr_context_cancel(tr):
            await hr_service.cancel_hr_gathering(context.application, pool, uid)
            wl_user = await repo.is_whitelist(pool, uid)
            await msg.reply_text(
                "Ок, добавление HR отменено.",
                reply_markup=main_menu(wl_user),
            )
            return
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
        assess = await llm.assess_event_clarity(pool, user_text)
        if not assess.get("clear_enough"):
            hint = (assess.get("hint_ru") or "").strip() or (
                "По тексту неочевидно, что именно анонсируешь — добавь деталей или даты."
            )
            bucket = context.application.bot_data.setdefault("event_publish_anyway", {})
            now = time.time()
            for k, v in list(bucket.items()):
                if now > float(v.get("expires", 0)):
                    bucket.pop(k, None)
            bucket[f"{uid}:{msg.message_id}"] = {"raw": user_text, "expires": now + 600.0}
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Всё равно опубликовать",
                            callback_data=f"eva:{msg.message_id}",
                        ),
                        InlineKeyboardButton("Отмена", callback_data=f"evc:{msg.message_id}"),
                    ]
                ]
            )
            await msg.reply_text(
                f"{hint}\n\nМожно дополнить и отправить снова, или опубликовать как есть — кнопки ниже.",
                reply_markup=kb,
            )
            return
        log.info(
            "inbound event pipeline uid=%s mid=%s text_len=%s forward=%s",
            uid,
            msg.message_id,
            len(user_text or ""),
            bool(msg.forward_origin),
        )
        try:
            reply = await events_service.handle_event_message(
                pool,
                context.bot,
                context.application,
                source_user_id=uid,
                raw_text=user_text,
                announcer_label=user_display_handle(msg.from_user) if msg.from_user else str(uid),
                source_message=msg,
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
        if state in ("awaiting_invite", "interview_hub", "interview_tell", "interview_confirm"):
            await repo.clear_session(pool, uid)
        await _help_reply(msg, wl)
        return

    if wl and text_raw in N.INVITE_ALIASES:
        await repo.set_session(pool, uid, "awaiting_invite", {})
        await msg.reply_text(
            "Кого добавить в хаб?\n\n"
            "• Перешли сюда сообщение от этого человека (из личного или группового чата), или\n"
            "• одним сообщением пришли @username (например @ivan) или числовой Telegram ID.\n\n"
            "Пока открыт этот шаг, пересылки не уйдут в ленту хаба.\n"
            f"Выйти без приглашения — кнопка «{N.BTN_CANCEL_INVITE}» или «{N.BTN_GUIDE}».",
            reply_markup=invite_flow_keyboard(),
        )
        return

    if wl and state == "awaiting_invite":
        if text_raw in N.INVITE_CANCEL_ALIASES:
            await repo.clear_session(pool, uid)
            await msg.reply_text("Ок, приглашение отменено.", reply_markup=main_menu(True))
            return

        if msg.forward_origin:
            invitee_id = _invitee_id_from_forward(msg)
            if invitee_id is None:
                await msg.reply_text(
                    "Из пересылки не видно профиль пользователя (часто так у «скрытого» имени). "
                    "Пришли @username или числовой Telegram ID.",
                    reply_markup=invite_flow_keyboard(),
                )
                return
            if invitee_id == uid:
                await msg.reply_text(
                    "Это ты сам — укажи другого человека.",
                    reply_markup=invite_flow_keyboard(),
                )
                return
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

        if msg.document or msg.photo or msg.video or msg.audio or msg.video_note:
            await msg.reply_text(
                "Для приглашения нужна пересылка сообщения от человека или текстом @username / ID. "
                "Фото и файлы сюда не подходят.",
                reply_markup=invite_flow_keyboard(),
            )
            return

        invitee_id = None
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

        await msg.reply_text(
            "Не понял, кого пригласить. Перешли сообщение от человека или пришли @username / числовой ID.",
            reply_markup=invite_flow_keyboard(),
        )
        return

    st = await repo.member_status(pool, uid)
    if st != "active":
        await msg.reply_text(
            "Пока нет доступа: попроси кого-то из участников с правом приглашения нажать "
            f"«{N.BTN_INVITE}» и указать твой @username или числовой ID."
        )
        return

    if await ml_forward_service.user_context_allows_hub_forward(pool, uid, state):
        if await ml_forward_service.try_handle_forward(msg, context, pool, uid):
            log.info(
                "inbound handled by ml_forward uid=%s chat=%s mid=%s forward=%s",
                uid,
                chat_id,
                msg.message_id,
                bool(msg.forward_origin),
            )
            return

    pending_hr = await repo.get_hr_pending_confirm_for_user(pool, uid)
    if pending_hr and not msg.document:
        await msg.reply_text(
            "Сначала ответь на черновик HR кнопками «Верно» или «Нет» под тем сообщением."
        )
        return

    if text_raw == N.BTN_SITE:
        settings = get_settings()
        lines = [
            "Сайт хаба",
            "",
            f"Ниже — ваш Telegram UID: <code>{uid}</code>",
            "Это и есть логин на сайте (шаг «Telegram ID»).",
            "",
            "Пароля нет: после ввода ID на сайте вам придёт одноразовый код в этот чат.",
            "Введите его на сайте как код входа.",
        ]
        url = (settings.web_public_base_url or "").strip()
        if url:
            lines.extend(["", f"Открыть: {url.rstrip('/')}/login"])
        await msg.reply_text("\n".join(lines), parse_mode="HTML")
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
            await repo.set_session(
                pool, uid, "interview_tell", {"interview_lines": [], "interview_had_voice": False}
            )
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

    if state == "interview_confirm":
        await msg.reply_text(
            "Сначала нажми «Подтвердить» или «Править текст» под сообщением с проверкой.",
            reply_markup=interview_confirm_keyboard(),
        )
        return

    if state == "interview_tell":
        if text_raw in N.CANCEL_FLOW_ALIASES:
            await repo.clear_session(pool, uid)
            await msg.reply_text("Вышли без сохранения. Если передумаешь — снова «Собесы».", reply_markup=main_menu(wl))
            return
        if text_raw in N.DONE_ALIASES:
            lines = list(payload.get("interview_lines") or [])
            had_voice = bool(payload.get("interview_had_voice"))
            ok, err, pending = await interview_service.build_interview_pending(
                pool, user, lines, had_voice
            )
            if not ok or not pending:
                await msg.reply_text(err or "Не получилось.", reply_markup=interview_tell_keyboard())
                return
            preview = pending["preview_ru"]
            await repo.set_session(
                pool, uid, "interview_confirm", {"interview_pending": pending}
            )
            await msg.reply_text(
                f"Проверь, всё ли так:\n\n{preview}\n\n"
                "Если верно — «Подтвердить». Если нужно дописать — «Править текст».",
                reply_markup=interview_confirm_keyboard(),
            )
            return
        if text_raw:
            pl = dict(payload)
            lines = list(pl.get("interview_lines") or [])
            lines.append(text_raw)
            pl["interview_lines"] = lines
            pl.setdefault("interview_had_voice", False)
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
                await msg.reply_text(
                    f"Файл сохранён в папке «{label}».\n"
                    f"Технический ярлык папки: {slug}"
                )
                try:
                    await company_sync.offer_file_company_link(context.bot, pool, chat_id, int(fid))
                except Exception:
                    log.exception("offer_file_company_link folder_name")
            else:
                await msg.reply_text("Не удалось: нет файла или чужая запись.")
        return

    await _route_after_inbound(msg, context, pool, uid, chat_id, user_text, has_doc, mime)
