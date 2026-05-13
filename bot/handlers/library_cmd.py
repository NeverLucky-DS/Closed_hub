from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from db import repo
from services import search_service


async def files_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_message:
        return
    if update.effective_chat and update.effective_chat.type != "private":
        return
    pool = context.application.bot_data["pool"]
    uid = update.effective_user.id
    if await repo.member_status(pool, uid) != "active":
        await update.effective_message.reply_text("Нет доступа.")
        return

    query = search_service.command_query(context.args)
    if query:
        rows = await repo.search_library_files(pool, query, search_service.MAX_SEARCH_RESULTS)
        text, markup = search_service.files_response(
            rows,
            title=f"Файлы по запросу «{query}»",
            empty_text=f"В библиотеке ничего не нашёл по запросу «{query}».",
        )
        await update.effective_message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
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


async def links_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_message:
        return
    if update.effective_chat and update.effective_chat.type != "private":
        return
    pool = context.application.bot_data["pool"]
    uid = update.effective_user.id
    if await repo.member_status(pool, uid) != "active":
        await update.effective_message.reply_text("Нет доступа.")
        return

    query = search_service.command_query(context.args)
    rows, available = await repo.search_resource_links(pool, query, search_service.MAX_SEARCH_RESULTS)
    if query:
        title = f"Ссылки по запросу «{query}»"
        empty = f"В полезных ссылках ничего не нашёл по запросу «{query}»."
    else:
        title = "Последние полезные ссылки"
        empty = "В полезных ссылках пока пусто."
    await update.effective_message.reply_text(
        search_service.links_response(
            rows,
            resources_available=available,
            title=title,
            empty_text=empty,
        ),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_message:
        return
    if update.effective_chat and update.effective_chat.type != "private":
        return
    pool = context.application.bot_data["pool"]
    uid = update.effective_user.id
    if await repo.member_status(pool, uid) != "active":
        await update.effective_message.reply_text("Нет доступа.")
        return

    query = search_service.command_query(context.args)
    if not query:
        await update.effective_message.reply_text(search_service.no_query_text("search"))
        return

    file_rows = await repo.search_library_files(pool, query, 8)
    link_rows, links_available = await repo.search_resource_links(pool, query, 7)
    text, markup = search_service.combined_response(
        file_rows,
        link_rows,
        links_available=links_available,
        query=query,
    )
    await update.effective_message.reply_text(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
