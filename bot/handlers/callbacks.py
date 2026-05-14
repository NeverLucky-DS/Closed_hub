from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from telegram import InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.keyboards import main_menu
from db import repo
from services import activity, company_sync, events_service, files_service, hr_service, interview_service, interviews_store, resources_service, search_service
from services.google_sheets_hr import append_hr_contact_row
from utils.telegram_user import user_display_handle

log = logging.getLogger(__name__)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    pool = context.application.bot_data["pool"]
    user = update.effective_user
    if not user:
        return
    data = query.data

    if data.startswith("res:"):
        if await repo.member_status(pool, user.id) != "active":
            await query.answer("Нет доступа", show_alert=True)
            return
        if data == "res:search":
            await repo.set_session(pool, user.id, "resource_search", {})
            await query.answer()
            await query.edit_message_text(
                "Напиши запрос одним сообщением.\n\n"
                "Например: машинное обучение, резюме, system design."
            )
            return
        if data == "res:latest":
            rows, available = await repo.search_resource_links(pool, "", search_service.MAX_SEARCH_RESULTS)
            await query.answer()
            await query.edit_message_text(
                search_service.links_response(
                    rows,
                    resources_available=available,
                    title="Последние полезные ссылки",
                    empty_text="В полезных ссылках пока пусто.",
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return
        if data.startswith("res:topic:"):
            try:
                topic_id = int(data.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("Некорректная тема", show_alert=True)
                return
            topic = await repo.get_resource_topic(pool, topic_id)
            if not topic:
                await query.answer("Тема не найдена", show_alert=True)
                return
            rows = await repo.list_resource_links(pool, topic_id=topic_id, limit=search_service.MAX_SEARCH_RESULTS)
            await query.answer()
            await query.edit_message_text(
                search_service.links_response(
                    rows,
                    resources_available=True,
                    title=f"Ссылки: {topic['path_title']}",
                    empty_text=f"В теме «{topic['path_title']}» пока нет ссылок.",
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return

    if data == "rlcan":
        _, session_payload = await repo.get_session(pool, user.id)
        await repo.clear_session(pool, user.id)
        await query.answer("Отменено")
        try:
            await query.edit_message_text("Добавление ссылки отменено.")
        except Exception:
            pass
        return

    if data == "rlok":
        _, session_payload = await repo.get_session(pool, user.id)
        if not session_payload or not session_payload.get("url"):
            await query.answer("Черновик устарел — пришли ссылку снова.", show_alert=True)
            return

        url = session_payload["url"]
        draft = session_payload.get("draft") or {}

        # Проверяем дубликат
        existing = await repo.find_resource_link_by_url(pool, url)
        if existing:
            await repo.clear_session(pool, user.id)
            await query.answer("Ссылка уже есть в базе.", show_alert=True)
            try:
                await query.edit_message_text(
                    f"Эта ссылка уже сохранена:\n<b>{existing['title']}</b>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return

        link_title = (draft.get("title") or "").strip()[:80] or resources_service.default_title_for_url(url)
        ai_summary = (draft.get("ai_summary") or "").strip()[:400] or None
        description = (draft.get("description_ru") or "").strip()[:2000]
        main_topic = (draft.get("main_topic") or "").strip()
        is_new_topic = bool(draft.get("is_new_topic", False))

        # Найти или создать тему
        topics = await repo.list_resource_topics(pool)
        tid: int | None = None

        if main_topic:
            mt_lower = main_topic.lower()
            for t in topics:
                if str(t["title"]).strip().lower() == mt_lower:
                    tid = int(t["id"])
                    break
            if tid is None and is_new_topic:
                slug, ttl, topic_err = resources_service.normalize_topic_title(main_topic)
                if not topic_err and slug and ttl:
                    tid = await repo.ensure_resource_topic(pool, slug, ttl, user.id)

        if tid is None and topics:
            # fallback: первая тема по порядку
            tid = int(topics[0]["id"])

        if tid is None:
            await query.answer("Не удалось определить тему. Добавь ссылку через сайт.", show_alert=True)
            return

        rid = await repo.insert_resource_link(
            pool,
            topic_id=tid,
            url=url,
            title=link_title,
            user_note=description or "—",
            ai_summary=ai_summary,
            added_by=user.id,
        )
        # Кодируем вектор после сохранения, ошибка не мешает подтверждению
        try:
            from services import embedding_service
            await embedding_service.refresh_resource_link_embedding(pool, rid)
        except Exception:
            pass
        await repo.clear_session(pool, user.id)
        await query.answer("Сохранено!")
        try:
            await query.edit_message_text(
                f"✅ Ссылка сохранена в теме «{main_topic or '—'}».\n\n"
                f"<b>{link_title}</b>\n{url}",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        return

    if data.startswith("hrx:"):
        hr_id = int(data.split(":", 1)[1])
        row = await repo.get_hr_contact(pool, hr_id)
        if not row or int(row["source_user_id"]) != user.id:
            await query.answer("Нет доступа", show_alert=True)
            return
        if str(row["status"]) != "awaiting_context":
            await query.answer("Уже не в режиме добавления HR", show_alert=True)
            return
        hr_service.cancel_hr_debounce(context.application, hr_id)
        ok = await repo.abandon_hr_contact_by_id(pool, hr_id, user.id)
        if not ok:
            await query.answer("Не удалось отменить", show_alert=True)
            return
        await query.answer("Отменено")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        wl = await repo.is_whitelist(pool, user.id)
        await context.bot.send_message(
            chat_id=user.id,
            text="Добавление HR отменено.",
            reply_markup=main_menu(wl),
        )
        return

    if data.startswith("eva:"):
        mid = int(data.split(":", 1)[1])
        bucket = context.application.bot_data.setdefault("event_publish_anyway", {})
        key = f"{user.id}:{mid}"
        entry = bucket.pop(key, None)
        if not entry or time.time() > float(entry.get("expires", 0)):
            await query.answer("Устарело — отправь анонс текстом снова.", show_alert=True)
            return
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        raw = str(entry["raw"])
        try:
            reply = await events_service.handle_event_message(
                pool,
                context.bot,
                context.application,
                source_user_id=user.id,
                raw_text=raw,
                announcer_label=user_display_handle(user),
                source_message=None,
            )
        except Exception:
            log.exception("eva force publish")
            reply = "Не удалось опубликовать. Попробуй позже."
        await context.bot.send_message(chat_id=user.id, text=reply)
        return

    if data.startswith("evc:"):
        mid = int(data.split(":", 1)[1])
        bucket = context.application.bot_data.setdefault("event_publish_anyway", {})
        bucket.pop(f"{user.id}:{mid}", None)
        await query.answer("Ок")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if data in ("ivok", "ived"):
        state, pl = await repo.get_session(pool, user.id)
        if state != "interview_confirm":
            await query.answer("Это сообщение уже не актуально.", show_alert=True)
            return
        pending = pl.get("interview_pending")
        if not pending or not isinstance(pending, dict):
            await query.answer("Нет данных", show_alert=True)
            return
        wl = await repo.is_whitelist(pool, user.id)
        if data == "ived":
            await query.answer("Ок")
            lines = list(pending.get("lines") or [])
            had_voice = bool(pending.get("had_voice"))
            await repo.set_session(
                pool,
                user.id,
                "interview_tell",
                {"interview_lines": lines, "interview_had_voice": had_voice},
            )
            try:
                await query.edit_message_text(
                    "Ок — дополни рассказ и снова нажми «Готово, сохранить»."
                )
            except Exception:
                pass
            return
        await query.answer("Сохраняю…")
        _, reply_txt = await interview_service.commit_interview_pending(
            pool, user, pending, bot=context.bot
        )
        await repo.clear_session(pool, user.id)
        try:
            await query.edit_message_text("Сохранено.")
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user.id, text=reply_txt, reply_markup=main_menu(wl)
        )
        return

    if data.startswith("fco:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Ошибка кнопки", show_alert=True)
            return
        fid = int(parts[1])
        cid = int(parts[2])
        frow = await repo.get_file_record(pool, fid)
        if not frow or int(frow["uploaded_by"]) != user.id:
            await query.answer("Нет доступа", show_alert=True)
            return
        if str(frow["status"]) != "confirmed":
            await query.answer("Файл ещё не в библиотеке", show_alert=True)
            return
        res = await repo.link_company_file(pool, cid, fid, user.id, None)
        await query.answer("Закреплено за компанией" if res == "ok" else "Не получилось")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        if res == "ok":
            try:
                await context.bot.send_message(chat_id=user.id, text="Файл привязан к карточке компании на сайте.")
            except Exception:
                log.exception("notify file company link")
        elif res == "duplicate":
            try:
                await context.bot.send_message(chat_id=user.id, text="Этот файл уже был привязан к этой компании.")
            except Exception:
                log.exception("notify duplicate company file")
        return

    if data.startswith("fcs:"):
        fid = int(data.split(":", 1)[1])
        frow = await repo.get_file_record(pool, fid)
        if not frow or int(frow["uploaded_by"]) != user.id:
            await query.answer("Нет доступа", show_alert=True)
            return
        await query.answer("Ок")
        try:
            await query.edit_message_text(
                "Файл в библиотеке. Закрепление к компании пропущено — при желании сделай это на сайте."
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        return

    await query.answer()

    if data.startswith("hry:"):
        hr_id = int(data.split(":", 1)[1])
        row = await repo.get_hr_contact(pool, hr_id)
        if not row or int(row["source_user_id"]) != user.id:
            await query.edit_message_text("Нет доступа к этому черновику.")
            return
        await repo.update_hr_contact_summary(
            pool,
            hr_id,
            row["company"],
            row["role_hint"],
            row["vacancies_hint"],
            row["summary"],
            "confirmed",
        )
        uh = user_display_handle(user)
        pts_hr = await activity.award(
            pool,
            user.id,
            "hr_contact_confirmed",
            {"hr_id": hr_id},
            bot=context.bot,
            announcer_label=uh,
        )
        row2 = await repo.get_hr_contact(pool, hr_id)
        sheet = "skipped"
        if row2:
            sheet = append_hr_contact_row(
                company=row2["company"],
                contact_ref=str(row2["contact_ref"]),
                summary=row2["summary"],
                hr_db_id=hr_id,
            )
        pts_note = f" +{pts_hr} оч." if pts_hr else ""
        if sheet == "ok":
            done = f"Сохранено. HR в базе и строка добавлена в Google Sheets.{pts_note}"
        elif sheet == "error":
            done = (
                "Сохранено в базу. Google Sheets не обновилась — проверь, что JSON ключ доступен боту "
                "(в Docker: volume в docker-compose, переменная GOOGLE_SERVICE_ACCOUNT_JSON_HOST на хосте). "
                "Подробности в логах контейнера."
                f"{pts_note}"
            )
        else:
            done = f"Сохранено в базу. Google Sheets не настроен (нет GOOGLE_SHEET_ID или пути к ключу).{pts_note}"
        try:
            site_line = await company_sync.link_confirmed_hr_to_company_line(pool, hr_id, user.id)
            if site_line:
                done = f"{done}\n\n{site_line}"
        except Exception:
            log.exception("link_confirmed_hr_to_company_line")
        await query.edit_message_text(done)
        return

    if data.startswith("ivd:"):
        if await repo.member_status(pool, user.id) != "active":
            await query.answer("Нет доступа", show_alert=True)
            return
        slug = data.split(":", 1)[1]
        if not re.fullmatch(r"[a-z0-9_\-]+", slug):
            await query.answer("Некорректный выбор", show_alert=True)
            return
        path = interviews_store.path_for_slug(slug)
        if not path.is_file():
            await query.answer("Файл не найден", show_alert=True)
            return
        fname = f"{slug}_interviews.md"
        with open(path, "rb") as fh:
            document = InputFile(fh, filename=fname)
            await context.bot.send_document(chat_id=user.id, document=document)
        await query.answer("Отправил файл")
        return

    if data.startswith("hrn:"):
        hr_id = int(data.split(":", 1)[1])
        row = await repo.get_hr_contact(pool, hr_id)
        if not row or int(row["source_user_id"]) != user.id:
            await query.edit_message_text("Нет доступа к этому черновику.")
            return
        await repo.update_hr_contact_summary(
            pool,
            hr_id,
            row["company"],
            row["role_hint"],
            row["vacancies_hint"],
            row["summary"],
            "awaiting_context",
        )
        await query.edit_message_text("Ок, пришли уточнения текстом — я пересоберу черновик.")
        return

    if data.startswith("fiy:"):
        fid = int(data.split(":", 1)[1])
        frow = await repo.get_file_record(pool, fid)
        if not frow or int(frow["uploaded_by"]) != user.id:
            await query.edit_message_text("Нет доступа к файлу.")
            return
        slug = frow["suggested_category"] or "other"
        cats = await repo.list_file_categories(pool)
        label = next((str(c["label_ru"]) for c in cats if c["slug"] == slug), slug)
        ok = await files_service.finalize_file_to_library(
            pool,
            file_id=fid,
            user_id=user.id,
            slug=slug,
            label_ru=label,
            bot=context.bot,
            announcer_label=user_display_handle(user),
        )
        if ok:
            await query.edit_message_text(f"Файл в папке «{label}» ({slug}).")
            try:
                ch = query.message.chat_id if query.message else user.id
                await company_sync.offer_file_company_link(context.bot, pool, ch, fid)
            except Exception:
                log.exception("offer_file_company_link fiy")
        else:
            await query.edit_message_text("Не удалось переместить файл.")
        return

    if data.startswith("fin:"):
        fid = int(data.split(":", 1)[1])
        frow = await repo.get_file_record(pool, fid)
        if not frow or int(frow["uploaded_by"]) != user.id:
            await query.edit_message_text("Нет доступа к файлу.")
            return
        await repo.update_file_record(pool, fid, status="cancelled")
        await query.edit_message_text("Отменено. Можешь прислать файл снова.")
        return

    if data.startswith("fic:"):
        _, fid_s, idx_s = data.split(":", 2)
        fid = int(fid_s)
        idx = int(idx_s)
        frow = await repo.get_file_record(pool, fid)
        if not frow or int(frow["uploaded_by"]) != user.id:
            await query.edit_message_text("Нет доступа к файлу.")
            return
        _, pl = await repo.get_session(pool, user.id)
        fp = pl.get("file_pick") or {}
        slugs = fp.get("slugs")
        slug = "other"
        if isinstance(slugs, list) and 0 <= idx < len(slugs):
            slug = str(slugs[idx])
        else:
            cats = await repo.list_file_categories(pool)
            if 0 <= idx < len(cats):
                slug = str(cats[idx]["slug"])
        label = next(
            (str(c["label_ru"]) for c in await repo.list_file_categories(pool) if c["slug"] == slug),
            slug,
        )
        ok = await files_service.finalize_file_to_library(
            pool,
            file_id=fid,
            user_id=user.id,
            slug=slug,
            label_ru=label,
            bot=context.bot,
            announcer_label=user_display_handle(user),
        )
        if ok:
            await query.edit_message_text(f"Файл в папке «{label}» ({slug}).")
            try:
                ch = query.message.chat_id if query.message else user.id
                await company_sync.offer_file_company_link(context.bot, pool, ch, fid)
            except Exception:
                log.exception("offer_file_company_link fic")
        else:
            await query.edit_message_text("Не удалось сохранить.")
        return

    if data.startswith("fiw:"):
        fid = int(data.split(":", 1)[1])
        frow = await repo.get_file_record(pool, fid)
        if not frow or int(frow["uploaded_by"]) != user.id:
            await query.edit_message_text("Нет доступа.")
            return
        await repo.set_session(pool, user.id, "awaiting_folder_name", {"file_id": fid})
        await query.edit_message_text(
            "Пришли одним сообщением название новой папки (по-русски или по-английски). "
            "Я создам slug для хранения на диске."
        )
        return

    if data.startswith("fdl:"):
        if await repo.member_status(pool, user.id) != "active":
            await query.answer("Нет доступа", show_alert=True)
            return
        fid = int(data.split(":", 1)[1])
        frow = await repo.get_file_record(pool, fid)
        if not frow or frow["status"] != "confirmed":
            await query.answer("Файл недоступен", show_alert=True)
            return
        path = Path(str(frow["storage_path"]))
        if not path.is_file():
            log.warning("missing file on disk id=%s path=%s", fid, path)
            await query.answer("Файл не найден на сервере", show_alert=True)
            return
        fname = frow["original_filename"] or path.name
        with open(path, "rb") as fh:
            document = InputFile(fh, filename=fname)
            await context.bot.send_document(
                chat_id=user.id,
                document=document,
                caption=f"#{fid} [{frow['confirmed_category']}]",
            )
        await query.answer("Отправил в личку")
        return
