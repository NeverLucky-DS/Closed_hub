import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot.handlers.callbacks import on_callback
from bot.handlers.messages import on_text_and_media
from bot.handlers.start import start_cmd
from config import get_settings
from db.pool import close_pool, create_pool
from db.repo import seed_whitelist_and_members

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    settings = get_settings()
    pool = await create_pool()
    application.bot_data["pool"] = pool
    await seed_whitelist_and_members(pool, settings.whitelist_seed_ids)
    log.info("DB pool ready, whitelist seeded if configured")


async def post_shutdown(application: Application) -> None:
    pool = application.bot_data.get("pool")
    if pool:
        await close_pool(pool)


def main() -> None:
    settings = get_settings()
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^(hry|hrn|fiy|fin|fic):"))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (~filters.COMMAND),
            on_text_and_media,
        )
    )
    log.info("Starting polling")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
