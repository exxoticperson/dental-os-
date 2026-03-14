from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    telegram_token: str
    telegram_allowed_user_id: int | None
    google_spreadsheet_id: str
    google_oauth_client_id: str
    google_oauth_client_secret: str
    google_oauth_refresh_token: str
    google_drive_root_folder_id: str | None
    google_drive_root_folder_name: str
    default_timezone: str
    friday_summary_hour: int
    friday_summary_minute: int
    reminder_scan_minutes: int
    webhook_base_url: str | None


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


def load_config() -> AppConfig:
    telegram_token = os.getenv("TELEGRAM_TOKEN", "").strip()
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    oauth_client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    oauth_client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    oauth_refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()
    if not telegram_token:
        raise ValueError("TELEGRAM_TOKEN is required.")
    if not spreadsheet_id:
        raise ValueError("GOOGLE_SPREADSHEET_ID is required.")
    if not oauth_client_id or not oauth_client_secret or not oauth_refresh_token:
        raise ValueError("GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and GOOGLE_OAUTH_REFRESH_TOKEN are required.")
    return AppConfig(
        telegram_token=telegram_token,
        telegram_allowed_user_id=_optional_int("TELEGRAM_ALLOWED_USER_ID"),
        google_spreadsheet_id=spreadsheet_id,
        google_oauth_client_id=oauth_client_id,
        google_oauth_client_secret=oauth_client_secret,
        google_oauth_refresh_token=oauth_refresh_token,
        google_drive_root_folder_id=os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip() or None,
        google_drive_root_folder_name=os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_NAME", "Dental Study OS Patients").strip(),
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "Africa/Cairo").strip(),
        friday_summary_hour=int(os.getenv("FRIDAY_SUMMARY_HOUR", "9")),
        friday_summary_minute=int(os.getenv("FRIDAY_SUMMARY_MINUTE", "0")),
        reminder_scan_minutes=int(os.getenv("REMINDER_SCAN_MINUTES", "15")),
        webhook_base_url=os.getenv("WEBHOOK_BASE_URL", "").strip() or None,
    )
