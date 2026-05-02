from __future__ import annotations

import json
import logging
from typing import Any

from telegram import Bot

from config import get_settings
from db import repo
from services import llm

log = logging.getLogger(__name__)


def _companies_block(rows: list) -> str:
    if not rows:
        return "(на сайте пока нет карточек компаний)"
    lines = []
    for r in rows:
        lines.append(f"{int(r['id'])} | {r['name']}")
    return "\n".join(lines)


async def _ensure_company_from_match(
    pool,
    user_id: int,
    decision: dict[str, Any],
    fallback_name: str | None,
) -> tuple[int | None, str | None]:
    act = str(decision.get("action") or "skip").lower()
    if act == "match":
        cid = decision.get("company_id")
        if cid is None:
            return None, None
        row = await repo.get_company_by_id(pool, int(cid))
        if row:
            return int(row["id"]), str(row["name"])
        return None, None
    if act == "create":
        name = decision.get("new_company_name_ru") or fallback_name
        if not name or len(str(name).strip()) < 2:
            return None, None
        name_s = str(name).strip()[:200]
        slug = await repo.allocate_unique_company_slug(pool, name_s)
        new_id = await repo.insert_company(pool, slug, name_s, None, user_id, [])
        return new_id, name_s
    return None, None


async def publish_interview_to_site(
    pool,
    *,
    user_id: int,
    company_ru: str,
    data: dict[str, Any],
    block: str,
    raw: str,
) -> str:
    rows = await repo.list_companies_compact(pool, 200)
    hint = (company_ru or "").strip()
    if hint.lower() in ("не указано", "-", "—", ""):
        hint = ""

    detail = json.dumps(
        {
            "structured": {
                k: data.get(k)
                for k in ("role", "when_text", "questions_summary", "notes_ru", "company_ru")
            },
            "excerpt": raw[:4000],
        },
        ensure_ascii=False,
    )

    company_id: int | None = None
    chosen_name: str | None = None

    if hint:
        company_id = await repo.find_company_id_by_name_ci(pool, hint)

    if company_id is None:
        try:
            decision = await llm.match_company_for_hub(
                pool,
                mode="interview_review",
                companies_block=_companies_block(rows),
                hint=hint or "не указано в поле company — определи компанию из текста отзыва",
                detail=detail,
            )
            company_id, chosen_name = await _ensure_company_from_match(
                pool, user_id, decision, fallback_name=hint or None
            )
        except Exception:
            log.exception("company_match interview")

    if company_id is None and hint:
        slug = await repo.allocate_unique_company_slug(pool, hint)
        company_id = await repo.insert_company(pool, slug, hint[:200], None, user_id, [])
        chosen_name = hint

    if company_id is None:
        return (
            "На сайт отзыв не записан — не удалось сопоставить с компанией. "
            "Текст сохранён в файле бота; добавь компанию на сайте и перенеси вручную при необходимости."
        )

    crow = await repo.get_company_by_id(pool, company_id)
    cname = str(crow["name"]) if crow else (chosen_name or "компания")
    code = await repo.insert_company_interview_review(pool, company_id, user_id, block, None)
    if code != "ok":
        log.warning("insert_company_interview_review failed code=%s", code)
        return f"Файл в боте сохранён. На сайте ошибка записи отзыва ({code})."
    slug = str(crow["slug"]) if crow else ""
    base = (get_settings().web_public_base_url or "").strip().rstrip("/")
    if slug and base:
        return f"На сайте отзыв добавлен к «{cname}» (Собесы): {base}/companies/{slug}/interviews"
    if slug:
        return f"На сайте отзыв добавлен к «{cname}» — раздел «Собесы»: /companies/{slug}/interviews"
    return f"На сайте отзыв добавлен к «{cname}»."


async def link_confirmed_hr_to_company_line(pool, hr_id: int, user_id: int) -> str:
    row = await repo.get_hr_contact(pool, hr_id)
    if not row or str(row["status"]) != "confirmed":
        return ""
    if row.get("company_id"):
        return ""
    hname = (row.get("company") or "").strip()
    if not hname or hname.lower() in ("не указано", "-", "—"):
        return ""

    rows = await repo.list_companies_compact(pool, 200)
    company_id = await repo.find_company_id_by_name_ci(pool, hname)
    chosen: str | None = None

    if company_id is None and rows:
        try:
            summ = (row.get("summary") or "")[:2000]
            decision = await llm.match_company_for_hub(
                pool,
                mode="hr_contact",
                companies_block=_companies_block(rows),
                hint=hname,
                detail=f"Поле company из карточки HR: {hname}\nКратко: {summ}",
            )
            company_id, chosen = await _ensure_company_from_match(
                pool, user_id, decision, fallback_name=hname
            )
        except Exception:
            log.exception("company_match hr")

    if company_id is None:
        company_id = await repo.find_company_id_by_name_ci(pool, hname)

    if company_id is None:
        return ""

    ok = await repo.set_hr_contact_company(pool, hr_id, company_id)
    if not ok:
        return ""
    crow = await repo.get_company_by_id(pool, company_id)
    cn = str(crow["name"]) if crow else (chosen or "")
    slug = str(crow["slug"]) if crow else ""
    base = (get_settings().web_public_base_url or "").strip().rstrip("/")
    if slug and base:
        return f"На сайте контакт привязан к «{cn}»: {base}/companies/{slug}/hr"
    if slug:
        return f"На сайте контакт привязан к «{cn}»: /companies/{slug}/hr"
    return f"На сайте контакт привязан к «{cn}»."


async def offer_file_company_link(bot: Bot, pool, chat_id: int, file_id: int) -> None:
    rows = await repo.list_companies_compact(pool, 22)
    if not rows:
        return
    from bot.keyboards import company_file_link_keyboard

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "Закрепить этот файл за компанией на сайте?\n"
            "Выбери карточку или «Пропустить» — тогда файл останется только в библиотеке."
        ),
        reply_markup=company_file_link_keyboard(file_id, rows),
    )
