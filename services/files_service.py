from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from pypdf import PdfReader
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from config import get_settings
from db import repo
from services import llm

log = logging.getLogger(__name__)


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
) -> str:
    settings = get_settings()
    base = Path(settings.file_storage_path)
    base.mkdir(parents=True, exist_ok=True)

    data: bytes = await get_file_bytes()
    max_b = settings.max_pdf_size_mb * 1024 * 1024
    if len(data) > max_b:
        return f"Файл слишком большой. Лимит {settings.max_pdf_size_mb} МБ."

    h = hashlib.sha256(data).hexdigest()
    sub = base / h[:2]
    sub.mkdir(parents=True, exist_ok=True)
    ext = ".pdf"
    if file_name and "." in file_name:
        ext = Path(file_name).suffix[:8] or ext
    out_path = sub / f"{h}{ext}"
    out_path.write_bytes(data)

    text_sample = ""
    if mime_type and "pdf" in mime_type.lower():
        try:
            text_sample = _extract_pdf_text(out_path)
        except Exception:
            log.exception("pdf extract")
            text_sample = ""
    if not text_sample.strip():
        text_sample = f"(бинарный или без текста, mime={mime_type})"

    try:
        summ = await llm.summarize_file(pool, text_sample, settings.file_category_list)
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
        )
        return "Файл сохранён, но суммаризация через LLM не удалась. Категорию уточни позже вручную в БД."

    summary = str(summ.get("summary_ru") or "")
    cat = str(summ.get("suggested_category") or "other")
    if cat not in settings.file_category_list:
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
    )

    rows = []
    for c in settings.file_category_list:
        rows.append([InlineKeyboardButton(c, callback_data=f"fic:{file_id_row}:{c}")])
    rows.append(
        [
            InlineKeyboardButton("Да, ок", callback_data=f"fiy:{file_id_row}"),
            InlineKeyboardButton("Нет", callback_data=f"fin:{file_id_row}"),
        ]
    )
    kb = InlineKeyboardMarkup(rows)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"Файл принят.\n\nПредлагаемая категория: <b>{cat}</b>\n\n{summary}\n\n"
            "Выбери категорию кнопкой или подтверди «Да, ок»."
        ),
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    return "Файл обработан — смотри сообщение с кнопками."
