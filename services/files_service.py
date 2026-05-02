from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from config import get_settings
from db import repo
from services import activity, file_storage, llm

log = logging.getLogger(__name__)

_STATUS_RU = {
    "confirmed": "уже в библиотеке",
    "awaiting_confirm": "уже загружен, ждёт выбора папки",
    "processing": "обрабатывается",
}


def _extract_pdf_text(path: Path, max_chars: int = 20000) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages[:40]:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        parts.append(t)
        if sum(len(p) for p in parts) > max_chars:
            break
    return "\n".join(parts)[:max_chars]


def _category_prompt_block(rows: list) -> str:
    lines = [f"{r['slug']} — {r['label_ru']}" for r in rows]
    return "\n".join(lines)


async def finalize_file_to_library(
    pool,
    *,
    file_id: int,
    user_id: int,
    slug: str,
    label_ru: str | None = None,
    bot: Bot | None = None,
    announcer_label: str | None = None,
) -> bool:
    row = await repo.get_file_record(pool, file_id)
    if not row or int(row["uploaded_by"]) != user_id:
        return False
    cats = await repo.list_file_categories(pool)
    known = {c["slug"] for c in cats}
    if slug not in known:
        await repo.ensure_file_category(pool, slug, label_ru or slug, user_id)
    elif label_ru:
        await repo.ensure_file_category(pool, slug, label_ru, user_id)
    fn = Path(row["storage_path"]).name
    new_path = file_storage.move_into_category_folder(str(row["storage_path"]), slug, fn)
    await repo.update_file_record(
        pool,
        file_id,
        status="confirmed",
        confirmed_category=slug,
        storage_path=new_path,
        confirmed_at=datetime.now(timezone.utc),
    )
    await activity.award(
        pool,
        user_id,
        "library_file_confirmed",
        {"file_id": file_id, "slug": slug},
        bot=bot,
        announcer_label=announcer_label,
    )
    log.info("file_confirmed id=%s slug=%s user=%s", file_id, slug, user_id)
    return True


async def handle_document(
    pool,
    bot: Bot,
    *,
    user_id: int,
    chat_id: int,
    file_id: str,
    mime_type: str | None,
    file_name: str | None,
    get_file_bytes,
    uploader_handle: str | None = None,
) -> str:
    settings = get_settings()
    file_storage.library_root().mkdir(parents=True, exist_ok=True)

    data: bytes = await get_file_bytes()
    max_b = settings.max_pdf_size_mb * 1024 * 1024
    if len(data) > max_b:
        return f"Файл слишком большой. Лимит {settings.max_pdf_size_mb} МБ."

    h = hashlib.sha256(data).hexdigest()

    existing = await repo.find_active_file_by_sha256(pool, h)
    if existing:
        eid = int(existing["id"])
        st = str(existing["status"])
        st_ru = _STATUS_RU.get(st, st)
        oname = existing.get("original_filename") or ""
        cat = existing.get("confirmed_category") or existing.get("suggested_category")
        extra = ""
        if oname:
            extra = f" Имя в системе: {oname}."
        if cat:
            extra += f" Категория: {cat}."
        log.info("file_upload_duplicate sha256=%s… existing_id=%s", h[:16], eid)
        return (
            "Этот файл уже есть в хабе (совпадение по SHA-256 содержимого). "
            f"Запись #{eid}, {st_ru}.{extra} Повторно загружать не нужно."
        )

    staging = file_storage.staging_dir_for_hash(h)
    ext = ".bin"
    if file_name and "." in file_name:
        ext = Path(file_name).suffix[:8] or ext
    elif mime_type and "pdf" in mime_type.lower():
        ext = ".pdf"
    out_path = staging / f"{h}{ext}"
    out_path.write_bytes(data)

    text_sample = ""
    if mime_type and "pdf" in mime_type.lower():
        try:
            text_sample = _extract_pdf_text(out_path)
        except Exception:
            log.exception("pdf extract")
            text_sample = ""
    if not text_sample.strip():
        text_sample = f"(мало текста или не PDF, mime={mime_type})"

    cat_rows = await repo.list_file_categories(pool)
    block = _category_prompt_block(cat_rows)
    slugs = [r["slug"] for r in cat_rows]

    try:
        summ = await llm.summarize_file(pool, text_sample, block)
    except Exception:
        log.exception("summarize_file")
        await repo.insert_file_record(
            pool,
            str(out_path),
            h,
            mime_type,
            user_id,
            status="awaiting_confirm",
            summary=None,
            suggested_category="other",
            extracted_text_preview=text_sample[:2000],
            original_filename=file_name,
            subject_tags=None,
            uploader_handle=uploader_handle,
        )
        return "Файл сохранён, но суммаризация не удалась. Позже можно выбрать папку вручную."

    summary = str(summ.get("summary_ru") or "")
    cat = str(summ.get("suggested_category_slug") or "other")
    tags = summ.get("subject_tags")
    tags_s = str(tags) if tags else None
    if cat not in slugs:
        cat = "other"

    file_id_row = await repo.insert_file_record(
        pool,
        str(out_path),
        h,
        mime_type,
        user_id,
        status="awaiting_confirm",
        summary=summary,
        suggested_category=cat,
        extracted_text_preview=text_sample[:2000],
        original_filename=file_name,
        subject_tags=tags_s,
        uploader_handle=uploader_handle,
    )

    kb_rows: list[list[InlineKeyboardButton]] = []
    for i, r in enumerate(cat_rows):
        label = str(r["label_ru"])
        if len(label) > 30:
            label = label[:27] + "…"
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"fic:{file_id_row}:{i}")])
    kb_rows.append([InlineKeyboardButton("Своя папка (название текстом)", callback_data=f"fiw:{file_id_row}")])
    kb_rows.append(
        [
            InlineKeyboardButton("Да, эта папка", callback_data=f"fiy:{file_id_row}"),
            InlineKeyboardButton("Отмена", callback_data=f"fin:{file_id_row}"),
        ]
    )
    kb = InlineKeyboardMarkup(kb_rows)

    cat_label = next((str(r["label_ru"]) for r in cat_rows if r["slug"] == cat), cat)
    tag_line = f"\nТемы: {tags_s}" if tags_s else ""
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"Файл принят.\n\nПапка: <b>{cat_label}</b> (<code>{cat}</code>){tag_line}\n\n"
            f"{summary}\n\n"
            "Выбери папку кнопкой или нажми «Да, эта папка». Если нет подходящей — «Своя папка» и пришли короткое название."
        ),
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )

    _, pl = await repo.get_session(pool, user_id)
    pl2 = dict(pl)
    pl2["file_pick"] = {"id": file_id_row, "slugs": slugs}
    await repo.set_session(pool, user_id, "idle", pl2)

    return "Файл обработан — смотри сообщение с кнопками."
