from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from dental_os.config import AppConfig
from dental_os.date_utils import next_recurring_datetime
from dental_os.query_engine import QueryEngine
from dental_os.services.sheets import SheetService


def build_scheduler(
    application: Application,
    config: AppConfig,
    sheets: SheetService,
    query_engine: QueryEngine,
    chat_id: int | None,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(config.default_timezone))
    scheduler.add_job(
        scan_due_reminders,
        "interval",
        minutes=config.reminder_scan_minutes,
        args=[application, sheets, chat_id],
        id="scan_due_reminders",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        send_friday_summary,
        "cron",
        day_of_week="fri",
        hour=config.friday_summary_hour,
        minute=config.friday_summary_minute,
        args=[application, query_engine, chat_id],
        id="send_friday_summary",
        replace_existing=True,
        max_instances=1,
    )
    return scheduler


async def scan_due_reminders(application: Application, sheets: SheetService, chat_id: int | None) -> None:
    if not chat_id:
        return
    now_value = datetime.now()
    due_rows = sheets.get_due_schedule_rows(now_value)
    for row_number, record in due_rows:
        reminder_stamp = f"{record.get('Reminder_Date')}T{record.get('Time') or '09:00'}"
        marker = f"last_notified={reminder_stamp}"
        if marker in record.get("Notes", ""):
            continue
        await application.bot.send_message(chat_id=chat_id, text=f"Reminder: {record.get('Event')}.")
        record["Notes"] = f"{record.get('Notes', '').strip()} {marker}".strip()
        recurring = _extract_recurring(record.get("Notes", ""))
        if recurring:
            try:
                current_dt = datetime.fromisoformat(reminder_stamp)
                next_dt = next_recurring_datetime(current_dt, recurring)
                record["Date"] = next_dt.date().isoformat()
                record["Reminder_Date"] = next_dt.date().isoformat()
                if record.get("Time"):
                    record["Time"] = next_dt.strftime("%H:%M")
            except ValueError:
                pass
        else:
            record["Status"] = "Reminded"
        sheets.update_schedule_record(row_number, record)


async def send_friday_summary(application: Application, query_engine: QueryEngine, chat_id: int | None) -> None:
    if not chat_id:
        return
    await application.bot.send_message(chat_id=chat_id, text=query_engine.weekly_summary())


def _extract_recurring(notes: str) -> str:
    lower = notes.lower()
    if "recurring:" not in lower:
        return ""
    return lower.split("recurring:", 1)[1].split()[0].strip()
