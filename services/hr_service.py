from __future__ import annotations

import asyncio
import logging
import re

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from config import get_settings
from db import repo

log = logging.getLogger(__name__)

_HR_NUMERIC = re.compile(r"^\s*(\d{6,15})\s*$")
_USERNAME = re.compile(r"^\s*@([a-zA-Z][a-zA-Z0-9_]{4,31})\s*$")


def normalize_hr_contact_ref(raw: str) -> str:
    s = raw.strip()
    m = _USERNAME.match(s)
    if m:
        return "@" + m.group(1).lower()
    m2 = _HR_NUMERIC.match(s)
    if m2:
        return m2.group(1)
    return s


def try_parse_hr_contact_line(text: str) -> str | None:
    if not text or not text.strip():
        return None
    s = text.strip()
    if _USERNAME.match(s):
        return normalize_hr_contact_ref(s)
    if _HR_NUMERIC.match(s):
        return normalize_hr_contact_ref(s)
    return None


def _cancel_task(app_tasks: dict[int, asyncio.Task], hr_contact_id: int) -> None:
    t = app_tasks.pop(hr_contact_id, None)
    if t and not t.done():
        t.cancel()


def schedule_hr_extract(
    application,
    *,
    hr_contact_id: int,
    chat_id: int,
    contact_ref: str,
) -> None:
    settings = get_settings()
    debounce = max(5, settings.hr_context_debounce_sec)
    tasks: dict[int, asyncio.Task] = application.bot_data.setdefault("hr_debounce_tasks", {})

    _cancel_task(tasks, hr_contact_id)

    async def _run() -> None:
        try:
            await asyncio.sleep(debounce)
            pool = application.bot_data["pool"]
            lines = await repo.get_hr_context_lines(pool, hr_contact_id)
            if not lines:
                return
            from services import llm

            data = await llm.extract_hr(pool, contact_ref, lines)
            summary = data.get("summary_ru")
            await repo.update_hr_contact_summary(
                pool,
                hr_contact_id,
                data.get("company"),
                data.get("role_hint"),
                data.get("vacancies_hint"),
                str(summary) if summary else None,
                "pending_confirm",
            )
            bot: Bot = application.bot
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Верно", callback_data=f"hry:{hr_contact_id}"),
                        InlineKeyboardButton("Нет", callback_data=f"hrn:{hr_contact_id}"),
                    ]
                ]
            )
            text = (
                "Черновик контакта HR:\n\n"
                f"{summary}\n\n"
                f"Контакт: {contact_ref}\n"
                f"Компания: {data.get('company')}\n"
                f"Роль: {data.get('role_hint')}\n"
                f"Вакансии: {data.get('vacancies_hint')}\n\n"
                "Верно?"
            )
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("hr extract failed")
            try:
                pool = application.bot_data["pool"]
                bot = application.bot
                await bot.send_message(
                    chat_id=chat_id,
                    text="Не получилось обработать контекст HR. Попробуй ещё раз или укороти текст.",
                )
            except Exception:
                log.exception("hr extract notify failed")

    tasks[hr_contact_id] = asyncio.create_task(_run())


async def start_hr_contact_ref_flow(pool, user_id: int, contact_ref: str) -> tuple[int, str]:
    norm = normalize_hr_contact_ref(contact_ref)
    draft = await repo.get_open_hr_draft_for_user(pool, user_id)
    if draft and normalize_hr_contact_ref(str(draft["contact_ref"])) == norm:
        hr_id = int(draft["id"])
    else:
        hr_id = await repo.create_hr_contact_draft(pool, norm, user_id)
    reply = (
        f"Принял контакт: {norm}.\n\n"
        "Расскажи про этого HR (можно несколькими сообщениями). По возможности укажи:\n"
        "• откуда контакт — источник (какое мероприятие, чат, пост, рекомендация и т.п.);\n"
        "• компанию и роль человека;\n"
        "• как прошло общение — тон, настроение, насколько открыто отвечали;\n"
        "• был ли личный контакт (писали в личку, встречались) или только публичное сообщение.\n\n"
        "Когда допишешь — соберу черновик с кнопками подтверждения."
    )
    return hr_id, reply


async def append_hr_context_and_schedule(
    application,
    pool,
    *,
    hr_contact_id: int,
    contact_ref: str,
    source_user_id: int,
    chat_id: int,
    text: str,
) -> None:
    await repo.append_hr_context(pool, hr_contact_id, text)
    schedule_hr_extract(
        application,
        hr_contact_id=hr_contact_id,
        chat_id=chat_id,
        contact_ref=contact_ref,
    )
