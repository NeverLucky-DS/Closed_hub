from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import urlencode

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import get_settings

MAX_SEARCH_RESULTS = 15


def command_query(args: list[str] | tuple[str, ...] | None) -> str:
    return " ".join(args or []).strip()


def no_query_text(command: str) -> str:
    return f"Напиши запрос после команды, например: /{command} машинное обучение"


def links_not_ready_text() -> str:
    return (
        "Раздел полезных ссылок ещё не подключён: в базе нет таблицы resources "
        "или совместимой таблицы resource_links. Когда ветка resources-links добавит схему, "
        "эта команда начнёт искать по title, url, user_note, summary и topic."
    )


def _short(text: object, limit: int = 90) -> str:
    s = str(text or "").replace("\n", " ").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _file_name(row) -> str:
    return str(row["original_filename"] or Path(str(row["storage_path"])).name)


def _site_url(path: str, params: dict[str, object] | None = None) -> str | None:
    base = (get_settings().web_public_base_url or "").strip().rstrip("/")
    if not base:
        return None
    query = f"?{urlencode(params)}" if params else ""
    return f"{base}{path}{query}"


def _site_link(label: str, url: str | None) -> str:
    if not url or not url.startswith(("http://", "https://")):
        return escape(label)
    return f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'


def _grouped_file_lines(rows: list) -> list[str]:
    lines: list[str] = []
    sorted_rows = sorted(
        rows,
        key=lambda r: str(r["category_label"] or r["confirmed_category"] or "Без папки").lower(),
    )
    current_group = ""
    for row in sorted_rows:
        group = str(row["category_label"] or row["confirmed_category"] or "Без папки")
        if group != current_group:
            if lines:
                lines.append("")
            lines.append(f"<b>{escape(group)}</b>")
            current_group = group

        fid = int(row["id"])
        name = _short(_file_name(row), 70)
        summary = _short(row["summary"], 95)
        cat = str(row["confirmed_category"] or "")
        url = _site_url("/library", {"cat": cat, "file": fid}) if cat else None
        title = f"#{fid} {name}"
        line = f"• {_site_link(title, url)}"
        if summary:
            line += f" — {escape(summary)}"
        lines.append(line)
    return lines


def files_response(rows: list, *, title: str, empty_text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    if not rows:
        return empty_text, None

    lines = [escape(title), ""]
    lines.extend(_grouped_file_lines(rows[:MAX_SEARCH_RESULTS]))

    kb_rows: list[list[InlineKeyboardButton]] = []
    for row in rows[:MAX_SEARCH_RESULTS]:
        fid = int(row["id"])
        name = _short(_file_name(row), 28)
        kb_rows.append([InlineKeyboardButton(f"Скачать {fid}: {name}", callback_data=f"fdl:{fid}")])

    return "\n".join(lines), InlineKeyboardMarkup(kb_rows)


def _grouped_link_lines(rows: list) -> list[str]:
    lines: list[str] = []
    sorted_rows = sorted(rows, key=lambda r: str(r["topic"] or "Без темы").lower())
    current_group = ""
    for row in sorted_rows:
        group = str(row["topic"] or "Без темы")
        if group != current_group:
            if lines:
                lines.append("")
            lines.append(f"<b>{escape(group)}</b>")
            current_group = group

        title = _short(row["title"] or row["url"] or "Ссылка", 70)
        url = str(row["url"] or "").strip()
        note = _short(row["user_note"] or row["summary"], 95)
        line = f"• {_site_link(title, url or None)}"
        if note:
            line += f" — {escape(note)}"
        lines.append(line)
    return lines


def links_response(
    rows: list,
    *,
    resources_available: bool,
    title: str,
    empty_text: str,
) -> str:
    if not resources_available:
        return links_not_ready_text()
    if not rows:
        return empty_text

    lines = [escape(title), ""]
    lines.extend(_grouped_link_lines(rows[:MAX_SEARCH_RESULTS]))
    return "\n".join(lines)


def combined_response(file_rows: list, link_rows: list, *, links_available: bool, query: str) -> tuple[str, InlineKeyboardMarkup | None]:
    parts: list[str] = [f"Поиск по запросу «{escape(_short(query, 80))}»"]
    keyboard_rows: list[list[InlineKeyboardButton]] = []

    if file_rows:
        parts.extend(["", "<b>Файлы</b>"])
        parts.extend(_grouped_file_lines(file_rows[:8]))
        for row in file_rows[:8]:
            fid = int(row["id"])
            name = _short(_file_name(row), 28)
            keyboard_rows.append([InlineKeyboardButton(f"Скачать {fid}: {name}", callback_data=f"fdl:{fid}")])

    if links_available and link_rows:
        parts.extend(["", "<b>Ссылки</b>"])
        parts.extend(_grouped_link_lines(link_rows[:7]))

    if not file_rows and (not links_available or not link_rows):
        parts.append("")
        parts.append("Ничего не нашёл в файлах.")
        if links_available:
            parts.append("В полезных ссылках тоже пусто по этому запросу.")
        else:
            parts.append("Полезные ссылки пока не подключены: таблицы resources/resource_links нет.")

    markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None
    return "\n".join(parts), markup
