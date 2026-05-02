import logging

from telegram.error import Conflict
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from bot.handlers.callbacks import on_callback
from bot.handlers.library_cmd import files_command
from bot.handlers.messages import on_text_and_media
from bot.handlers.start import help_cmd, start_cmd
from bot.handlers.voice import on_voice
from config import get_settings
from db.pool import close_pool, create_pool
from db.repo import seed_whitelist_and_members
from db.schema_patch import apply_pending_patches
from services.event_summary_worker import start_event_summary_worker, stop_event_summary_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


async def on_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        log.warning(
            "Telegram 409 Conflict: с этим BOT_TOKEN уже идёт getUpdates в другом процессе. "
            "Останови дубликат (второй docker compose, локальный uv run, другой сервер)."
        )
        return
    log.error("Unhandled error in handler", exc_info=err)


async def post_init(application: Application) -> None:
    settings = get_settings()
    pool = await create_pool()
    application.bot_data["pool"] = pool
    await apply_pending_patches(pool)
    await seed_whitelist_and_members(pool, settings.whitelist_seed_ids)
    start_event_summary_worker(application)
    log.info("DB ready (migrations + whitelist seed + event summary queue)")


async def post_shutdown(application: Application) -> None:
    await stop_event_summary_worker(application)
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
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("files", files_command))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.VOICE, on_voice))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (~filters.COMMAND),
            on_text_and_media,
        )
    )
    app.add_error_handler(on_error)
    log.info("Starting polling")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
