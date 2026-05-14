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


def _duplicate_file_message(existing, sha256_hex: str) -> str:
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
    log.info("file_upload_duplicate sha256=%s… existing_id=%s", sha256_hex[:16], eid)
    return (
        "Этот файл уже есть в хабе (совпадение по SHA-256 содержимого). "
        f"Запись #{eid}, {st_ru}.{extra} Повторно загружать не нужно."
    )


async def prepare_file_upload(
    pool,
    *,
    user_id: int,
    data: bytes,
    mime_type: str | None,
    file_name: str | None,
    uploader_handle: str | None = None,
) -> dict:
    settings = get_settings()
    file_storage.library_root().mkdir(parents=True, exist_ok=True)

    max_b = settings.max_pdf_size_mb * 1024 * 1024
    if len(data) > max_b:
        return {"ok": False, "error": f"Файл слишком большой. Лимит {settings.max_pdf_size_mb} МБ."}

    h = hashlib.sha256(data).hexdigest()
    existing = await repo.find_active_file_by_sha256(pool, h)
    if existing:
        return {"ok": False, "error": _duplicate_file_message(existing, h), "duplicate": True}

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
    categories = [{"slug": str(r["slug"]), "label_ru": str(r["label_ru"])} for r in cat_rows]
    slugs = [r["slug"] for r in categories]
    block = _category_prompt_block(cat_rows)

    summary = ""
    cat = "other"
    tags_s = None
    analysis_failed = False
    try:
        summ = await llm.summarize_file(pool, text_sample, block)
        summary = str(summ.get("summary_ru") or "")
        cat = str(summ.get("suggested_category_slug") or "other")
        tags = summ.get("subject_tags")
        tags_s = str(tags) if tags else None
    except Exception:
        log.exception("summarize_file")
        analysis_failed = True

    if cat not in slugs:
        cat = "other"

    file_id_row = await repo.insert_file_record(
        pool,
        str(out_path),
        h,
        mime_type,
        user_id,
        status="awaiting_confirm",
        summary=summary or None,
        suggested_category=cat,
        extracted_text_preview=text_sample[:2000],
        original_filename=file_name,
        subject_tags=tags_s,
        uploader_handle=uploader_handle,
    )
    cat_label = next((r["label_ru"] for r in categories if r["slug"] == cat), cat)
    return {
        "ok": True,
        "file_id": int(file_id_row),
        "original_filename": file_name,
        "mime_type": mime_type,
        "summary": summary,
        "suggested_category": cat,
        "category_label": cat_label,
        "subject_tags": tags_s,
        "categories": categories,
        "analysis_failed": analysis_failed,
        "manual_description_required": analysis_failed and not summary.strip(),
    }


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

    # Кодируем вектор после подтверждения, ошибка не мешает операции
    try:
        from services import embedding_service
        await embedding_service.refresh_file_embedding(pool, file_id)
    except Exception:
        log.debug("embedding refresh failed for file_id=%s", file_id, exc_info=True)

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
    data: bytes = await get_file_bytes()
    draft = await prepare_file_upload(
        pool,
        user_id=user_id,
        data=data,
        mime_type=mime_type,
        file_name=file_name,
        uploader_handle=uploader_handle,
    )
    if not draft.get("ok"):
        return str(draft.get("error") or "Не удалось обработать файл.")

    kb_rows: list[list[InlineKeyboardButton]] = []
    categories = list(draft.get("categories") or [])
    for i, r in enumerate(categories):
        label = str(r.get("label_ru") or r.get("slug") or "Папка")
        if len(label) > 30:
            label = label[:27] + "…"
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"fic:{draft['file_id']}:{i}")])
    kb_rows.append([InlineKeyboardButton("Своя папка (название текстом)", callback_data=f"fiw:{draft['file_id']}")])
    kb_rows.append(
        [
            InlineKeyboardButton("Да, эта папка", callback_data=f"fiy:{draft['file_id']}"),
            InlineKeyboardButton("Отмена", callback_data=f"fin:{draft['file_id']}"),
        ]
    )
    kb = InlineKeyboardMarkup(kb_rows)

    tag_line = f"\nТемы: {draft['subject_tags']}" if draft.get("subject_tags") else ""
    summary = str(draft.get("summary") or "Описание не удалось собрать автоматически.")
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"Файл принят.\n\nПапка: <b>{draft['category_label']}</b> (<code>{draft['suggested_category']}</code>){tag_line}\n\n"
            f"{summary}\n\n"
            "Выбери папку кнопкой или нажми «Да, эта папка». Если нет подходящей — «Своя папка» и пришли короткое название."
        ),
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )

    _, pl = await repo.get_session(pool, user_id)
    pl2 = dict(pl)
    pl2["file_pick"] = {"id": draft["file_id"], "slugs": [r["slug"] for r in categories]}
    await repo.set_session(pool, user_id, "idle", pl2)

    return "Файл обработан — смотри сообщение с кнопками."
