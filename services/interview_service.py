from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import Bot, User

from db import repo
from services import activity, company_sync, interviews_store, llm
from utils import nav_labels as N
from utils.telegram_user import user_display_handle
from utils.text_slug import interview_company_slug

log = logging.getLogger(__name__)

WELCOME_TELL = (
    "Расскажи про собеседование — текстом или голосом, можно несколькими сообщениями. "
    "По возможности:\n"
    "• должность / уровень;\n"
    "• когда примерно было;\n"
    "• сколько собеседующих;\n"
    "• длительность;\n"
    "• что спрашивали;\n"
    "• была ли задача на алгоритмы.\n\n"
    f"Когда всё передал — «{N.BTN_STORY_DONE}». Выйти без сохранения — «{N.BTN_STORY_CANCEL}»."
)


def _join_raw(lines: list[str]) -> str:
    return "\n\n".join(x.strip() for x in lines if x and str(x).strip()).strip()


def _build_file_body(
    *,
    ts: str,
    uh: str,
    had_voice: bool,
    summary_line: str,
    raw: str,
) -> str:
    r = raw.strip()
    if had_voice:
        s = (summary_line or "").strip() or "голосовое сообщение"
        return f"{ts} | {uh}\nКратко: {s}\n\n{r}"
    return r


async def build_interview_pending(
    pool, user: User, lines: list[str], had_voice: bool
) -> tuple[bool, str | None, dict | None]:
    raw = _join_raw(lines)
    if not raw:
        return False, f"Пока пусто — напиши или пришли голосовое, затем «{N.BTN_STORY_DONE}».", None
    try:
        data = await llm.extract_interview_story(pool, raw)
    except Exception:
        log.exception("interview_extract")
        return False, "Не получилось разобрать текст. Попробуй короче или разбей на части.", None

    try:
        prev = await llm.interview_confirmation_preview(pool, data, had_voice)
    except Exception:
        log.exception("interview_confirmation_preview")
        cr = str(data.get("company_ru") or "—").strip()
        rl = str(data.get("role") or "—").strip()
        prev = {"preview_ru": f"Компания: {cr}\nПозиция: {rl}", "summary_line": ""}

    preview_ru = (prev.get("preview_ru") or "").strip() or "Проверь данные в следующем сообщении."
    summary_line = str(prev.get("summary_line") or "").strip()

    pending = {
        "lines": lines,
        "had_voice": had_voice,
        "data": data,
        "preview_ru": preview_ru,
        "summary_line": summary_line,
        "raw": raw,
    }
    return True, None, pending


async def commit_interview_pending(
    pool, user: User, pending: dict, *, bot: Bot | None = None
) -> tuple[bool, str]:
    data = pending["data"]
    raw = str(pending["raw"] or "").strip()
    had_voice = bool(pending.get("had_voice"))
    summary_line = str(pending.get("summary_line") or "").strip()

    company_ru = str(data.get("company_ru") or "Не указано").strip() or "Не указано"
    slug = interview_company_slug(company_ru)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uh = user_display_handle(user)

    file_body = _build_file_body(
        ts=ts,
        uh=uh,
        had_voice=had_voice,
        summary_line=summary_line,
        raw=raw,
    )

    interviews_store.append_report(slug=slug, company_title=company_ru, body=file_body)
    pts = await activity.award(
        pool,
        user.id,
        "interview_submitted",
        {"company": company_ru, "slug": slug},
        bot=bot,
        announcer_label=uh,
    )
    total = await repo.get_member_activity_points(pool, user.id)
    msg = (
        f"Сохранил в файл по компании «{company_ru}». "
        f"Начислено очков: {pts}. Всего очков: {total}."
    )
    site_body = file_body if len(file_body.strip()) >= 10 else f"{file_body}\n\n(собес)"
    try:
        site_note = await company_sync.publish_interview_to_site(
            pool,
            user_id=user.id,
            company_ru=company_ru,
            data=data,
            block=site_body,
            raw=raw,
        )
        if site_note:
            msg = f"{msg}\n\n{site_note}"
    except Exception:
        log.exception("publish_interview_to_site")
    return True, msg
