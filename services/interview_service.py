from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import Bot, User

from db import repo
from services import activity, interviews_store, llm
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


async def finalize_story(
    pool, user: User, lines: list[str], *, bot: Bot | None = None
) -> tuple[bool, str]:
    raw = "\n\n".join(x.strip() for x in lines if x and str(x).strip()).strip()
    if not raw:
        return False, f"Пока пусто — напиши или пришли голосовое, затем «{N.BTN_STORY_DONE}»."
    try:
        data = await llm.extract_interview_story(pool, raw)
    except Exception:
        log.exception("interview_extract")
        return False, "Не получилось разобрать текст. Попробуй короче или разбей на части."

    company_ru = str(data.get("company_ru") or "Не указано").strip() or "Не указано"
    slug = interview_company_slug(company_ru)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uh = user_display_handle(user)
    algo = data.get("had_algorithm_task")
    algo_s = "да" if algo is True else ("нет" if algo is False else "неясно")
    ni = data.get("num_interviewers")
    ni_s = str(int(ni)) if isinstance(ni, (int, float)) else (str(ni) if ni not in (None, "") else "—")

    block = (
        f"### {ts} — {uh} (id {user.id})\n"
        f"- **Должность / уровень:** {data.get('role') or '—'}\n"
        f"- **Когда:** {data.get('when_text') or '—'}\n"
        f"- **Собеседующих:** {ni_s}\n"
        f"- **Длительность:** {data.get('duration_text') or '—'}\n"
        f"- **Задача на алгоритмы:** {algo_s}\n"
        f"- **Что спрашивали:** {data.get('questions_summary') or '—'}\n"
    )
    if data.get("notes_ru"):
        block += f"- **Заметки:** {data['notes_ru']}\n"
    block += "\n**Как рассказывал участник:**\n" + raw[:4000]
    if len(raw) > 4000:
        block += "\n…"

    interviews_store.append_report(slug=slug, company_title=company_ru, body=block)
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
    return True, msg
