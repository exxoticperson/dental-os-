from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress

from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from dental_os.config import AppConfig, load_config
from dental_os.parser import DentalParser
from dental_os.query_engine import QueryEngine
from dental_os.reminders import build_scheduler
from dental_os.services.drive import DriveService
from dental_os.services.google import GoogleClients
from dental_os.services.sheets import SheetService
from dental_os.telegram_handlers.messages import handle_photo, handle_text, help_command, init_command, start_command, summary_command


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
LOGGER = logging.getLogger(__name__)
WEBHOOK_PATH = "/telegram-webhook"


def build_application(config: AppConfig, *, webhook_mode: bool) -> tuple[Application, SheetService]:
    google = GoogleClients(config)
    sheets = SheetService(config, google)
    drive = DriveService(config, google)
    parser = DentalParser(config.default_timezone)
    query_engine = QueryEngine(sheets, parser, config.default_timezone)

    builder = Application.builder().token(config.telegram_token)
    if webhook_mode:
        builder = builder.updater(None)
    app = builder.build()
    app.bot_data["config"] = config
    app.bot_data["sheets"] = sheets
    app.bot_data["drive"] = drive
    app.bot_data["parser"] = parser
    app.bot_data["query_engine"] = query_engine

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("init", init_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app, sheets


async def run_webhook_mode(config: AppConfig) -> None:
    application, sheets = build_application(config, webhook_mode=True)
    scheduler = build_scheduler(application, config, sheets, application.bot_data["query_engine"], config.telegram_allowed_user_id)
    webhook_base_url = (config.webhook_base_url or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
    if not webhook_base_url:
        raise ValueError("WEBHOOK_BASE_URL or RENDER_EXTERNAL_URL is required for webhook mode.")

    await application.initialize()
    await application.start()
    scheduler.start()
    try:
        sheets.initialize()
    except Exception:
        LOGGER.warning("Initial sheet setup failed. Bot will retry on demand.", exc_info=True)

    await application.bot.set_webhook(url=f"{webhook_base_url}{WEBHOOK_PATH}", allowed_updates=Update.ALL_TYPES)

    aio_app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def telegram_webhook(request: web.Request) -> web.Response:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(text="ok")

    aio_app.router.add_get("/", health)
    aio_app.router.add_post(WEBHOOK_PATH, telegram_webhook)
    runner = web.AppRunner(aio_app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()

    LOGGER.info("Webhook bot running on port %s", port)
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        await application.bot.delete_webhook(drop_pending_updates=False)
        await runner.cleanup()
        await application.stop()
        await application.shutdown()


def run_polling_mode(config: AppConfig) -> None:
    application, sheets = build_application(config, webhook_mode=False)
    scheduler = build_scheduler(application, config, sheets, application.bot_data["query_engine"], config.telegram_allowed_user_id)
    scheduler.start()
    try:
        sheets.initialize()
    except Exception:
        LOGGER.warning("Initial sheet setup failed. Bot will retry on demand.", exc_info=True)
    application.run_polling(drop_pending_updates=True)


def main() -> None:
    config = load_config()
    webhook_base_url = config.webhook_base_url or os.getenv("RENDER_EXTERNAL_URL", "")
    if webhook_base_url or os.getenv("RENDER"):
        asyncio.run(run_webhook_mode(config))
        return
    run_polling_mode(config)


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        main()
