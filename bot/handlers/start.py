from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import main_menu
from db import repo


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool = context.application.bot_data["pool"]
    user = update.effective_user
    if not user:
        return
    uid = user.id
    wl = await repo.is_whitelist(pool, uid)
    st = await repo.member_status(pool, uid)
    if st != "active":
        await update.effective_message.reply_text(
            "Привет. Доступ только по приглашению: кто-то из белого списка должен "
            "добавить тебя через меню «Добавить участника», указав твой @username "
            "(если он публичный) или числовой Telegram ID."
        )
        return
    await update.effective_message.reply_text(
        "Ты в базе. Анонсы мероприятий, UID HR + контекст, PDF, голосовые (если есть GROQ_API_KEY), "
        "команда /files — библиотека файлов.",
        reply_markup=main_menu(wl),
    )
