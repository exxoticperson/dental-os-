from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from dental_os.config import load_config
from dental_os.parser import DentalParser
from dental_os.query_engine import QueryEngine
from dental_os.reminders import build_scheduler
from dental_os.services.drive import DriveService
from dental_os.services.google import GoogleClients
from dental_os.services.sheets import SheetService
from dental_os.telegram_handlers.messages import handle_photo, handle_text, help_command, init_command, start_command, summary_command


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")


def main() -> None:
    config = load_config()
    google = GoogleClients(config)
    sheets = SheetService(config, google)
    drive = DriveService(config, google)
    parser = DentalParser(config.default_timezone)
    query_engine = QueryEngine(sheets, parser, config.default_timezone)

    app = Application.builder().token(config.telegram_token).build()
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

    scheduler = build_scheduler(app, config, sheets, query_engine, config.telegram_allowed_user_id)
    scheduler.start()

    try:
        sheets.initialize()
    except Exception:
        logging.getLogger(__name__).warning("Initial sheet setup failed. Bot will retry on demand.", exc_info=True)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
