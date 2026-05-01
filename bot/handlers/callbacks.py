from __future__ import annotations

import logging
from pathlib import Path

from telegram import InputFile, Update
from telegram.ext import ContextTypes

from db import repo
from services import files_service
from services.google_sheets_hr import append_hr_contact_row

log = logging.getLogger(__name__)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    pool = context.application.bot_data["pool"]
    user = update.effective_user
    if not user:
        return
    data = query.data

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
        row2 = await repo.get_hr_contact(pool, hr_id)
        if row2:
            append_hr_contact_row(
                company=row2["company"],
                telegram_uid=int(row2["telegram_uid"]),
                role_hint=row2["role_hint"],
                vacancies_hint=row2["vacancies_hint"],
                summary=row2["summary"],
                source_user_id=int(row2["source_user_id"]),
                hr_db_id=hr_id,
            )
        await query.edit_message_text("Сохранено. HR добавлен в базу (и в таблицу, если настроен Google Sheets).")
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
            pool, file_id=fid, user_id=user.id, slug=slug, label_ru=label
        )
        if ok:
            await query.edit_message_text(f"Файл в папке «{label}» ({slug}).")
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
            pool, file_id=fid, user_id=user.id, slug=slug, label_ru=label
        )
        if ok:
            await query.edit_message_text(f"Файл в папке «{label}» ({slug}).")
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
