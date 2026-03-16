from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import dateparser
from dateparser.search import search_dates
from zoneinfo import ZoneInfo


TIME_COLON_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", re.IGNORECASE)
TIME_AT_RE = re.compile(r"\bat\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
TIME_MERIDIEM_RE = re.compile(r"\b(\d{1,2})\s*(am|pm)\b", re.IGNORECASE)
SLANG_REPLACEMENTS = (
    (r"\b2mrw\b", "tomorrow"),
    (r"\btmrw\b", "tomorrow"),
    (r"\btmw\b", "tomorrow"),
    (r"\btomo\b", "tomorrow"),
    (r"\bafter tmrw\b", "day after tomorrow"),
    (r"\bafter tmw\b", "day after tomorrow"),
    (r"\bafter tomorrow\b", "day after tomorrow"),
    (r"\bin a wk\b", "in a week"),
    (r"\bnext wk\b", "next week"),
    (r"\bthis wk\b", "this week"),
    (r"\bf\/up\b", "follow up"),
    (r"\bfu\b", "follow up"),
    (r"\bqz\b", "quiz"),
    (r"\basg\b", "assignment"),
    (r"\bpls\b", "please"),
    (r"\bplz\b", "please"),
)


def now_local(timezone_name: str) -> datetime:
    return datetime.now(ZoneInfo(timezone_name))


def normalize_natural_text(text: str) -> str:
    normalized = text or ""
    for pattern, replacement in SLANG_REPLACEMENTS:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def naive_now_local(timezone_name: str) -> datetime:
    return now_local(timezone_name).replace(tzinfo=None)


def today_local(timezone_name: str) -> str:
    return now_local(timezone_name).date().isoformat()


def timestamp_local(timezone_name: str) -> str:
    return now_local(timezone_name).strftime("%Y-%m-%d %H:%M:%S")


def format_date(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


def extract_datetime(text: str, timezone_name: str, prefer_future: bool = True) -> tuple[datetime | None, str]:
    text = normalize_natural_text(text)
    base = now_local(timezone_name)
    lower = text.lower()
    explicit_time = bool(TIME_AT_RE.search(text) or TIME_COLON_RE.search(text) or TIME_MERIDIEM_RE.search(text))
    if "day after tomorrow" in lower:
        parsed = base + timedelta(days=2)
        if not explicit_time:
            parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
        return parsed, "day after tomorrow"
    settings = {
        "PREFER_DATES_FROM": "future" if prefer_future else "current_period",
        "RELATIVE_BASE": base,
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": timezone_name,
        "TO_TIMEZONE": timezone_name,
    }
    results = search_dates(text, languages=["en"], settings=settings)
    if results:
        phrase, parsed = results[0]
        if not explicit_time:
            parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
        return parsed, phrase
    parsed = dateparser.parse(text, languages=["en"], settings=settings)
    if parsed and not explicit_time:
        parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed, text if parsed else ""


def extract_follow_up_date(text: str, timezone_name: str) -> str:
    lower = text.lower()
    if "follow up" not in lower and "review" not in lower and "next step" not in lower:
        return ""
    parsed, _ = extract_datetime(text, timezone_name)
    return format_date(parsed)


def extract_time_only(text: str) -> str:
    match = TIME_AT_RE.search(text) or TIME_COLON_RE.search(text) or TIME_MERIDIEM_RE.search(text)
    if not match:
        return ""
    groups = match.groups("")
    if match.re is TIME_MERIDIEM_RE:
        hours = int(groups[0])
        minutes = 0
        meridiem = groups[1].lower()
    else:
        hours = int(groups[0])
        minutes = int(groups[1] or 0)
        meridiem = (groups[2] or "").lower()
    if meridiem == "pm" and hours < 12:
        hours += 12
    if meridiem == "am" and hours == 12:
        hours = 0
    if 0 <= hours <= 23 and 0 <= minutes <= 59:
        return f"{hours:02d}:{minutes:02d}"
    return ""


def parse_range_hint(text: str, timezone_name: str) -> tuple[date, date]:
    base = now_local(timezone_name)
    lower = text.lower()
    if "today" in lower:
        start = base.date()
        return start, start + timedelta(days=1)
    if "yesterday" in lower:
        start = (base - timedelta(days=1)).date()
        return start, start + timedelta(days=1)
    if "tomorrow" in lower:
        start = (base + timedelta(days=1)).date()
        return start, start + timedelta(days=1)
    if "last week" in lower:
        end = base.date()
        return end - timedelta(days=7), end
    if "this week" in lower or "next 7 days" in lower:
        start = base.date()
        return start, start + timedelta(days=7)
    parsed, _ = extract_datetime(text, timezone_name)
    if parsed:
        start = parsed.date()
        return start, start + timedelta(days=1)
    start = base.date()
    return start, start + timedelta(days=7)


def parse_sheet_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_recurring_rule(text: str) -> str:
    lower = text.lower()
    if "every day" in lower or "daily" in lower:
        return "daily"
    if "every weekday" in lower or "weekdays" in lower:
        return "weekdays"
    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    for day in weekdays:
        if f"every {day}" in lower or f"each {day}" in lower:
            return f"weekly:{day}"
    if "every week" in lower or "weekly" in lower:
        return "weekly"
    if "every month" in lower or "monthly" in lower:
        return "monthly"
    return ""


def next_recurring_datetime(current_dt: datetime, recurring: str) -> datetime:
    recurring = recurring.strip().lower()
    if recurring == "daily":
        return current_dt + timedelta(days=1)
    if recurring == "weekdays":
        next_dt = current_dt + timedelta(days=1)
        while next_dt.weekday() > 4:
            next_dt += timedelta(days=1)
        return next_dt
    if recurring.startswith("weekly:"):
        day_name = recurring.split(":", 1)[1]
        weekday_map = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        target = weekday_map.index(day_name)
        delta = (target - current_dt.weekday()) % 7 or 7
        return current_dt + timedelta(days=delta)
    if recurring == "weekly":
        return current_dt + timedelta(days=7)
    if recurring == "monthly":
        month = current_dt.month + 1
        year = current_dt.year
        if month == 13:
            month = 1
            year += 1
        return current_dt.replace(year=year, month=month)
    return current_dt
