from telegram import Update
from telegram.ext import ContextTypes

from db import repo


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
        await query.edit_message_text("Сохранено в базе как подтверждённый контакт HR.")
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
        cat = frow["suggested_category"] or "other"
        await repo.update_file_record(
            pool,
            fid,
            status="confirmed",
            confirmed_category=str(cat),
        )
        await query.edit_message_text(f"Файл зафиксирован с категорией: {cat}")
        return

    if data.startswith("fin:"):
        fid = int(data.split(":", 1)[1])
        frow = await repo.get_file_record(pool, fid)
        if not frow or int(frow["uploaded_by"]) != user.id:
            await query.edit_message_text("Нет доступа к файлу.")
            return
        await repo.update_file_record(pool, fid, status="cancelled")
        await query.edit_message_text("Отменено. Пришли файл снова или выбери категорию позже.")
        return

    if data.startswith("fic:"):
        _, rest = data.split(":", 1)
        fid_str, cat = rest.split(":", 1)
        fid = int(fid_str)
        frow = await repo.get_file_record(pool, fid)
        if not frow or int(frow["uploaded_by"]) != user.id:
            await query.edit_message_text("Нет доступа к файлу.")
            return
        await repo.update_file_record(
            pool,
            fid,
            status="confirmed",
            confirmed_category=cat,
        )
        await query.edit_message_text(f"Файл зафиксирован с категорией: {cat}")
        return
