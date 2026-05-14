from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from db import repo
from services import search_service


async def _active_pool_and_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return None
    if update.effective_chat and update.effective_chat.type != "private":
        return None
    pool = context.application.bot_data["pool"]
    uid = update.effective_user.id
    if await repo.member_status(pool, uid) != "active":
        await update.effective_message.reply_text("Нет доступа.")
        return None
    return pool, uid


async def resources_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await _active_pool_and_user(update, context)
    if data is None or not update.effective_message:
        return
    pool, _uid = data
    topics = await repo.list_resource_topics_with_counts(pool)
    await update.effective_message.reply_text(
        search_service.resources_menu_text(),
        reply_markup=search_service.resources_menu_keyboard(topics),
    )


async def files_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await _active_pool_and_user(update, context)
    if data is None or not update.effective_message:
        return
    pool, _uid = data

    query = search_service.command_query(context.args)
    if query:
        vec = await search_service.query_vector(query)
        rows = []
        if vec:
            rows = await repo.semantic_search_library_files(pool, vec, search_service.MAX_SEARCH_RESULTS)
        if not rows:
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
    data = await _active_pool_and_user(update, context)
    if data is None or not update.effective_message:
        return
    pool, _uid = data

    query = search_service.command_query(context.args)
    if query:
        vec = await search_service.query_vector(query)
        rows = []
        if vec:
            rows = await repo.semantic_search_resource_links(pool, vec, search_service.MAX_SEARCH_RESULTS)
            available = True
        if not rows:
            rows, available = await repo.search_resource_links(pool, query, search_service.MAX_SEARCH_RESULTS)
        title = f"Ссылки по запросу «{query}»"
        empty = f"В полезных ссылках ничего не нашёл по запросу «{query}»."
    else:
        rows, available = await repo.search_resource_links(pool, "", search_service.MAX_SEARCH_RESULTS)
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
    data = await _active_pool_and_user(update, context)
    if data is None or not update.effective_message:
        return
    pool, _uid = data

    query = search_service.command_query(context.args)
    if not query:
        await update.effective_message.reply_text(search_service.no_query_text("search"))
        return

    vec = await search_service.query_vector(query)
    file_rows = []
    link_rows = []
    if vec:
        file_rows = await repo.semantic_search_library_files(pool, vec, 8)
        link_rows = await repo.semantic_search_resource_links(pool, vec, 7)
        links_available = True
    if not file_rows:
        file_rows = await repo.search_library_files(pool, query, 8)
    if not link_rows:
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
