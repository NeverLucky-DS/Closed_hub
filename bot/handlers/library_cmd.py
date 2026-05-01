from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from db import repo


async def files_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_message:
        return
    pool = context.application.bot_data["pool"]
    uid = update.effective_user.id
    if await repo.member_status(pool, uid) != "active":
        await update.effective_message.reply_text("Нет доступа.")
        return

    rows = await repo.list_library_files(pool, 25)
    if not rows:
        await update.effective_message.reply_text("В библиотеке пока нет подтверждённых файлов.")
        return

    lines: list[str] = ["📚 Последние файлы — скачать кнопкой:"]
    kb_rows: list[list[InlineKeyboardButton]] = []
    for r in rows[:20]:
        fid = int(r["id"])
        cat = r["confirmed_category"] or "?"
        name = r["original_filename"] or Path(r["storage_path"]).name
        who = r.get("uploader_handle") or str(r["uploaded_by"])
        ts = r["confirmed_at"] or r["created_at"]
        lines.append(f"#{fid} · {cat} · {name[:36]} · {who} · {ts}")
        kb_rows.append([InlineKeyboardButton(f"⬇ {fid} {name[:24]}", callback_data=f"fdl:{fid}")])

    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3490] + "…"
    await update.effective_message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(kb_rows),
    )
