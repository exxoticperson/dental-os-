from __future__ import annotations

import logging
import os
import re
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from dental_os.config import AppConfig
from dental_os.date_utils import timestamp_local, today_local
from dental_os.models import PendingClarification
from dental_os.parser import DentalParser
from dental_os.query_engine import QueryEngine
from dental_os.services.drive import DriveService
from dental_os.services.sheets import SheetService


logger = logging.getLogger(__name__)

CORRECTION_PREFIXES = (
    "no",
    "wrong",
    "that shouldn't",
    "shouldn't",
    "not schedule",
    "not task",
    "go back",
    "remove that",
    "undo that",
)

CHAT_PREFIXES = ("hi", "hello", "hey", "help", "thanks", "thank you")
PURE_CHAT_MESSAGES = {
    "okay": "Noted.",
    "ok": "Noted.",
    "cool": "Noted.",
    "great": "Good.",
}


def is_authorized(update: Update, config: AppConfig) -> bool:
    if config.telegram_allowed_user_id is None:
        return True
    user = update.effective_user
    return bool(user and user.id == config.telegram_allowed_user_id)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    if not is_authorized(update, config):
        return
    await update.message.reply_text("Dental Study OS is ready.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    if not is_authorized(update, config):
        return
    await update.message.reply_text("Send natural messages. Use /init once after Google sharing. Use /summary for the Friday summary on demand.")


async def init_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    if not is_authorized(update, config):
        return
    sheets: SheetService = context.application.bot_data["sheets"]
    try:
        sheets.initialize()
        await update.message.reply_text("Sheets initialized.")
    except Exception:
        logger.exception("Sheet initialization failed.")
        await update.message.reply_text("Init failed. Check Google sharing and spreadsheet ID.")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    if not is_authorized(update, config):
        return
    engine: QueryEngine = context.application.bot_data["query_engine"]
    await update.message.reply_text(engine.weekly_summary())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    if not is_authorized(update, config):
        return
    message = update.effective_message
    if not message or not message.text:
        return
    pending = _get_pending_clarification(context, update.effective_user.id)
    if pending:
        await _handle_clarification(update, context, pending)
        return
    await _process_text(update, context, message.text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    if not is_authorized(update, config):
        return
    message = update.effective_message
    if not message or not message.photo:
        return
    parser: DentalParser = context.application.bot_data["parser"]
    caption = message.caption or ""
    intent = parser.parse(caption, is_photo=True)
    if intent.requires_follow_up:
        _set_pending_clarification(
            context,
            update.effective_user.id,
            PendingClarification(
                original_text=caption,
                route="photo_patient",
                question=intent.follow_up_question,
                photo_file_id=message.photo[-1].file_id,
            ),
        )
        await message.reply_text(intent.follow_up_question)
        return
    await _store_patient_photo(update, context, intent, message.photo[-1].file_id)


async def _handle_clarification(update: Update, context: ContextTypes.DEFAULT_TYPE, pending: PendingClarification) -> None:
    message = update.effective_message
    reply_text = message.text if message else ""
    combined = f"{pending.original_text} {reply_text}".strip()
    parser: DentalParser = context.application.bot_data["parser"]
    sheets: SheetService = context.application.bot_data["sheets"]
    if pending.route == "photo_patient":
        intent = parser.parse(combined, is_photo=True)
        if intent.requires_follow_up:
            _clear_pending_clarification(context, update.effective_user.id)
            await _save_to_inbox(sheets, combined, parsed_type="Inbox", notes="Photo lacked enough patient context.")
            await message.reply_text("Saved to Inbox.")
            return
        _clear_pending_clarification(context, update.effective_user.id)
        await _store_patient_photo(update, context, intent, pending.photo_file_id)
        return
    _clear_pending_clarification(context, update.effective_user.id)
    intent = parser.parse(combined)
    if intent.requires_follow_up:
        await _save_to_inbox(sheets, combined, parsed_type=intent.parsed_type or "Inbox", subject=intent.subject, linked_case_id=intent.linked_case_id, notes="Clarification still unclear.")
        await message.reply_text("Saved to Inbox.")
        return
    await _process_text(update, context, combined)


async def _process_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    parser: DentalParser = context.application.bot_data["parser"]
    sheets: SheetService = context.application.bot_data["sheets"]
    query_engine: QueryEngine = context.application.bot_data["query_engine"]
    chat_reply = _chat_reply(text)
    if chat_reply:
        await update.effective_message.reply_text(chat_reply)
        return
    if _looks_like_correction(text):
        await _handle_correction(update, context, text)
        return
    intent = parser.parse(text)
    try:
        sheets.initialize()
    except Exception:
        logger.exception("Sheets unavailable.")
        await update.effective_message.reply_text("Google connection failed. Check sharing and credentials.")
        return
    if intent.requires_follow_up:
        _set_pending_clarification(context, update.effective_user.id, PendingClarification(original_text=text, route=intent.route, question=intent.follow_up_question))
        await update.effective_message.reply_text(intent.follow_up_question)
        return
    if _is_probably_chat(text, intent):
        await update.effective_message.reply_text("Here. Send anything to log, ask, or fix.")
        return
    if intent.route == "query":
        await update.effective_message.reply_text(query_engine.answer(text))
        return
    if intent.route == "task_done":
        task_name = sheets.mark_task_done(intent.task)
        await update.effective_message.reply_text(f"Done: {task_name}." if task_name else "No matching open task.")
        return
    if intent.route == "tasks":
        today = today_local(parser.timezone_name)
        if _is_duplicate_recent(context, update.effective_user.id, "Tasks", text):
            await update.effective_message.reply_text("Looks like the last task. Skipped.")
            return
        row_number = sheets.append_row("Tasks", [today, intent.task, intent.subject, intent.priority, intent.date, "Open", intent.recurring, intent.notes])
        _remember_last_action(context, update.effective_user.id, "Tasks", row_number, text)
        await update.effective_message.reply_text(f"Logged: {intent.task}.")
        return
    if intent.route == "schedule":
        recurrence_note = f" recurring:{intent.recurring}" if intent.recurring else ""
        if _is_duplicate_recent(context, update.effective_user.id, "Schedule", text):
            await update.effective_message.reply_text("Looks like the last schedule item. Skipped.")
            return
        row_number = sheets.append_row("Schedule", [intent.date, intent.time, intent.metadata.get("event_type", "Reminder"), intent.subject, intent.event, intent.priority, intent.follow_up_date or intent.date, intent.status or "Scheduled", f"{intent.notes}{recurrence_note}".strip()])
        _remember_last_action(context, update.effective_user.id, "Schedule", row_number, text)
        await update.effective_message.reply_text(f"Added: {intent.event}.")
        return
    if intent.route == "assessments":
        today = today_local(parser.timezone_name)
        row_number = sheets.append_row("Assessments", [intent.date or today, intent.subject, intent.assessment_type, intent.score, intent.total, intent.percentage, intent.notes])
        _remember_last_action(context, update.effective_user.id, "Assessments", row_number, text)
        await update.effective_message.reply_text(f"Logged: {intent.subject or 'Assessment'} {intent.score}/{intent.total}.")
        return
    if intent.route == "patients":
        today = today_local(parser.timezone_name)
        if _is_duplicate_recent(context, update.effective_user.id, "Patients", text):
            await update.effective_message.reply_text("Looks like the last patient log. Skipped.")
            return
        row_number = sheets.append_row("Patients", [intent.date or today, intent.subject, intent.case_id, intent.patient_name, intent.phone_number, intent.procedure, intent.tooth_or_area, intent.supervisor, intent.session_notes, intent.next_step, intent.follow_up_date, intent.photo_links])
        _remember_last_action(context, update.effective_user.id, "Patients", row_number, text)
        await update.effective_message.reply_text(f"Saved: Patient session for {intent.case_id} {intent.patient_name}.")
        return
    if intent.route == "materials":
        today = today_local(parser.timezone_name)
        row_number = sheets.append_row("Materials", [today, intent.item, intent.category, intent.subject, intent.priority, intent.status or "Pending", intent.store_or_source, intent.notes])
        _remember_last_action(context, update.effective_user.id, "Materials", row_number, text)
        await update.effective_message.reply_text(f"Added: {intent.item} to Materials.")
        return
    if intent.route == "courses":
        row_number = sheets.append_row("Courses", [intent.subject, intent.course_topic, intent.course_category, intent.status or "Active", intent.notes])
        _remember_last_action(context, update.effective_user.id, "Courses", row_number, text)
        await update.effective_message.reply_text(f"Saved: {intent.course_topic}.")
        return
    if intent.route == "study_progress":
        row_number, progress = sheets.upsert_study_progress(intent.subject, total_count=intent.total_count, completed_count=intent.completed_count, notes=intent.notes)
        _remember_last_action(context, update.effective_user.id, "Courses", row_number, text)
        total_text = progress.get("total", 0)
        done_text = progress.get("done", 0)
        remaining = max(total_text - done_text, 0) if total_text else ""
        suffix = f", {remaining} left." if total_text else "."
        await update.effective_message.reply_text(f"Saved: {intent.subject} study progress {done_text}/{total_text}{suffix}")
        return
    await _save_to_inbox(sheets, text, parsed_type=intent.parsed_type or "Inbox", subject=intent.subject, linked_case_id=intent.linked_case_id, notes=intent.notes)
    await update.effective_message.reply_text("Saved to Inbox.")


async def _store_patient_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, intent, photo_file_id: str) -> None:
    sheets: SheetService = context.application.bot_data["sheets"]
    drive: DriveService = context.application.bot_data["drive"]
    tmp_path = ""
    try:
        telegram_file = await context.bot.get_file(photo_file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_handle:
            tmp_path = tmp_handle.name
        await telegram_file.download_to_drive(custom_path=tmp_path)
        photo_link = drive.upload_patient_photo(tmp_path, intent.subject, intent.case_id, intent.patient_name)
        if not sheets.attach_photo_link(intent.case_id, photo_link):
            today = today_local(context.application.bot_data["parser"].timezone_name)
            row_number = sheets.append_row("Patients", [today, intent.subject, intent.case_id, intent.patient_name, intent.phone_number, intent.procedure, intent.tooth_or_area, intent.supervisor, intent.session_notes, intent.next_step, intent.follow_up_date, photo_link])
            _remember_last_action(context, update.effective_user.id, "Patients", row_number, intent.raw_text)
        await update.effective_message.reply_text(f"Saved: Patient photo for {intent.case_id} {intent.patient_name}.")
    except Exception:
        logger.exception("Patient photo upload failed.")
        await _save_to_inbox(sheets, intent.raw_text, parsed_type="Patient", subject=intent.subject, linked_case_id=intent.case_id, notes="Photo upload failed.")
        await update.effective_message.reply_text("Saved to Inbox.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


async def _save_to_inbox(
    sheets: SheetService,
    raw_text: str,
    parsed_type: str,
    subject: str = "",
    linked_case_id: str = "",
    notes: str = "",
) -> None:
    timestamp = timestamp_local(sheets.config.default_timezone)
    sheets.append_row("Inbox", [timestamp, raw_text, parsed_type, subject, linked_case_id, "New", "", notes])


def _set_pending_clarification(context: ContextTypes.DEFAULT_TYPE, user_id: int, clarification: PendingClarification) -> None:
    pending_map = context.application.bot_data.setdefault("pending_clarifications", {})
    pending_map[user_id] = clarification


def _get_pending_clarification(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> PendingClarification | None:
    pending_map = context.application.bot_data.setdefault("pending_clarifications", {})
    return pending_map.get(user_id)


def _clear_pending_clarification(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    pending_map = context.application.bot_data.setdefault("pending_clarifications", {})
    pending_map.pop(user_id, None)


def _looks_like_correction(text: str) -> bool:
    lower = text.strip().lower()
    return any(lower.startswith(prefix) for prefix in CORRECTION_PREFIXES)


async def _handle_correction(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    sheets: SheetService = context.application.bot_data["sheets"]
    parser: DentalParser = context.application.bot_data["parser"]
    action = _find_action_for_correction(context, update.effective_user.id, text, parser)
    if not action:
        await _save_to_inbox(sheets, text, parsed_type="Correction", notes="No recent action to reverse.")
        await update.effective_message.reply_text("Saved your correction to Inbox.")
        return
    sheet_name = action.get("sheet_name", "")
    row_number = action.get("row_number", 0)
    original_text = action.get("raw_text", "")
    if sheet_name and row_number:
        try:
            sheets.delete_row(sheet_name, row_number)
        except Exception:
            logger.exception("Failed to roll back last action.")
    correction_note = f"Correction for {sheet_name}: {original_text}"
    reroute_text = _build_reroute_text(original_text, text)
    rerouted = await _reroute_correction(update, context, reroute_text)
    if not rerouted:
        await _save_to_inbox(sheets, text, parsed_type="Correction", notes=correction_note)
    _remove_action(context, update.effective_user.id, action)
    if rerouted:
        await update.effective_message.reply_text(f"Moved from {sheet_name} to {rerouted}.")
    else:
        await update.effective_message.reply_text(f"Removed from {sheet_name}. Saved your correction to Inbox.")


def _remember_last_action(context: ContextTypes.DEFAULT_TYPE, user_id: int, sheet_name: str, row_number: int, raw_text: str) -> None:
    action_map = context.application.bot_data.setdefault("last_actions", {})
    history = action_map.setdefault(user_id, [])
    history.append(
        {
        "sheet_name": sheet_name,
        "row_number": row_number,
        "raw_text": raw_text,
        "summary": _summarize_action(sheet_name, raw_text),
        "created_at": timestamp_local(context.application.bot_data["config"].default_timezone),
        }
    )
    action_map[user_id] = history[-12:]


def _get_last_action(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict | None:
    action_map = context.application.bot_data.setdefault("last_actions", {})
    history = action_map.get(user_id, [])
    return history[-1] if history else None


def _clear_last_action(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    action_map = context.application.bot_data.setdefault("last_actions", {})
    action_map.pop(user_id, None)


def _remove_action(context: ContextTypes.DEFAULT_TYPE, user_id: int, action: dict) -> None:
    action_map = context.application.bot_data.setdefault("last_actions", {})
    history = action_map.get(user_id, [])
    history = [item for item in history if item is not action]
    if history:
        action_map[user_id] = history
    else:
        action_map.pop(user_id, None)


def _find_action_for_correction(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str, parser: DentalParser) -> dict | None:
    action_map = context.application.bot_data.setdefault("last_actions", {})
    history = list(reversed(action_map.get(user_id, [])))
    if not history:
        return None
    lower = text.lower()
    mentioned_sheet = _extract_sheet_hint(lower)
    mentioned_subject = parser._normalize_subject(text)
    keywords = set(re.findall(r"[a-z]{4,}", lower))
    best_score = -1
    best_action = None
    for action in history:
        score = 0
        sheet_name = action.get("sheet_name", "").lower()
        raw_text = action.get("raw_text", "").lower()
        if mentioned_sheet and mentioned_sheet.lower() == sheet_name:
            score += 5
        elif mentioned_sheet:
            score -= 1
        if mentioned_subject and mentioned_subject.lower() in raw_text:
            score += 3
        overlap = len(keywords.intersection(set(re.findall(r"[a-z]{4,}", raw_text))))
        score += overlap
        if action is history[0]:
            score += 1
        if score > best_score:
            best_score = score
            best_action = action
    return best_action


def _extract_sheet_hint(lower_text: str) -> str:
    hint_map = {
        "schedule": "Schedule",
        "task": "Tasks",
        "assignment": "Tasks",
        "mark": "Assessments",
        "assessment": "Assessments",
        "patient": "Patients",
        "material": "Materials",
        "course": "Courses",
    }
    for keyword, sheet_name in hint_map.items():
        if keyword in lower_text:
            return sheet_name
    return ""


def _summarize_action(sheet_name: str, raw_text: str) -> str:
    clean = " ".join(raw_text.split())
    return f"{sheet_name}: {clean[:80]}"


def _chat_reply(text: str) -> str:
    lower = text.strip().lower()
    if not lower:
        return ""
    if lower.startswith(("hi", "hello", "hey")):
        return "Here. Send anything to log, ask, or fix."
    if lower in {"help", "/help"}:
        return "Send natural messages. If I misfile something, say: no, that's wrong."
    if lower.startswith(("thanks", "thank you")):
        return "Anytime."
    if lower in {"how are you", "how are you?"}:
        return "Ready."
    if lower in PURE_CHAT_MESSAGES:
        return PURE_CHAT_MESSAGES[lower]
    return ""


def _is_probably_chat(text: str, intent) -> bool:
    lower = text.strip().lower()
    if not lower:
        return False
    if lower in PURE_CHAT_MESSAGES or lower.startswith(CHAT_PREFIXES):
        return True
    return intent.route == "inbox" and len(lower.split()) <= 6 and not any(char.isdigit() for char in lower)


def _is_duplicate_recent(context: ContextTypes.DEFAULT_TYPE, user_id: int, sheet_name: str, raw_text: str) -> bool:
    action_map = context.application.bot_data.setdefault("last_actions", {})
    history = list(reversed(action_map.get(user_id, [])))
    normalized = " ".join(raw_text.lower().split())
    for action in history[:3]:
        if action.get("sheet_name") != sheet_name:
            continue
        if " ".join(action.get("raw_text", "").lower().split()) == normalized:
            return True
    return False


def _build_reroute_text(original_text: str, correction_text: str) -> str:
    cleaned = re.sub(r"^(no|wrong|undo that|remove that|go back)\s*,?\s*", "", correction_text, flags=re.IGNORECASE).strip()
    if "assignment" in correction_text.lower() and "task" not in cleaned.lower():
        cleaned = f"{cleaned} task".strip()
    return f"{original_text} {cleaned}".strip()


async def _reroute_correction(update: Update, context: ContextTypes.DEFAULT_TYPE, reroute_text: str) -> str:
    parser: DentalParser = context.application.bot_data["parser"]
    sheets: SheetService = context.application.bot_data["sheets"]
    intent = parser.parse(reroute_text)
    user_id = update.effective_user.id
    today = today_local(parser.timezone_name)
    if intent.route in {"query", "inbox"} or intent.requires_follow_up:
        return ""
    if intent.route == "tasks":
        row_number = sheets.append_row("Tasks", [today, intent.task, intent.subject, intent.priority, intent.date, "Open", intent.recurring, f"Correction: {reroute_text}"])
        _remember_last_action(context, user_id, "Tasks", row_number, reroute_text)
        return "Tasks"
    if intent.route == "schedule":
        row_number = sheets.append_row("Schedule", [intent.date, intent.time, intent.metadata.get("event_type", "Reminder"), intent.subject, intent.event, intent.priority, intent.follow_up_date or intent.date, intent.status or "Scheduled", f"Correction: {reroute_text}"])
        _remember_last_action(context, user_id, "Schedule", row_number, reroute_text)
        return "Schedule"
    if intent.route == "patients":
        row_number = sheets.append_row("Patients", [intent.date or today, intent.subject, intent.case_id, intent.patient_name, intent.phone_number, intent.procedure, intent.tooth_or_area, intent.supervisor, intent.session_notes, intent.next_step, intent.follow_up_date, intent.photo_links])
        _remember_last_action(context, user_id, "Patients", row_number, reroute_text)
        return "Patients"
    if intent.route == "assessments":
        row_number = sheets.append_row("Assessments", [intent.date or today, intent.subject, intent.assessment_type, intent.score, intent.total, intent.percentage, f"Correction: {reroute_text}"])
        _remember_last_action(context, user_id, "Assessments", row_number, reroute_text)
        return "Assessments"
    if intent.route == "materials":
        row_number = sheets.append_row("Materials", [today, intent.item, intent.category, intent.subject, intent.priority, intent.status or "Pending", intent.store_or_source, f"Correction: {reroute_text}"])
        _remember_last_action(context, user_id, "Materials", row_number, reroute_text)
        return "Materials"
    if intent.route == "study_progress":
        row_number, _ = sheets.upsert_study_progress(intent.subject, total_count=intent.total_count, completed_count=intent.completed_count, notes=f"Correction: {reroute_text}")
        _remember_last_action(context, user_id, "Courses", row_number, reroute_text)
        return "Courses"
    return ""
