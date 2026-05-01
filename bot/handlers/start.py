from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.messages import _help_reply
from bot.keyboards import main_menu
from db import repo
from utils import nav_labels as N


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
            "Привет.\n\n"
            "Здесь закрытый бот хаба: доступ выдаётся участником с правом приглашения. "
            f"Попроси его нажать «{N.BTN_INVITE}» и указать твой публичный @username "
            "или числовой Telegram ID."
        )
        return
    pts = await repo.get_member_activity_points(pool, uid)
    await update.effective_message.reply_text(
        f"С возвращением. Сейчас у тебя {pts} очков активности.\n\n"
        "Снизу — главное меню: «Собесы» и подсказка «Как пользоваться». "
        "Остальное без кнопок: пересылай в чат то, что хочешь видеть в ленте, "
        "пиши анонсы мероприятий, кинь PDF, начни диалог с HR (@ник отдельным сообщением, потом контекст). "
        "Список файлов в библиотеке: /files\n\n"
        "Для HR важно: в тексте отдельно назови компанию-работодателя; площадку знакомства "
        "(мероприятие, Future Today и т.д.) не подставляй вместо работодателя.",
        reply_markup=main_menu(wl),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool = context.application.bot_data["pool"]
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    uid = user.id
    wl = await repo.is_whitelist(pool, uid)
    st = await repo.member_status(pool, uid)
    if st != "active":
        await msg.reply_text(
            "Подсказка доступна после активации в хабе. "
            f"Попроси пригласить тебя через «{N.BTN_INVITE}»."
        )
        return
    await _help_reply(msg, wl)
