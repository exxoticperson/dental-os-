from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass

from telegram import Update
from telegram.ext import ContextTypes

from dental_os.config import AppConfig
from dental_os.constants import MATERIAL_STATUSES, PRIORITIES, SCHEDULE_STATUSES, SUBJECTS, TASK_STATUSES
from dental_os.date_utils import extract_datetime, extract_time_only, timestamp_local, today_local
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
EDITABLE_SHEETS = ("Tasks", "Schedule", "Assessments", "Patients", "Materials", "Courses")
STOPWORDS = {
    "change", "update", "edit", "move", "delete", "remove", "last", "that", "this", "entry", "record", "item",
    "to", "from", "for", "the", "a", "an", "my", "please", "set",
}
FIELD_ALIASES = {
    "date": "Date",
    "due date": "Due_Date",
    "due": "Due_Date",
    "follow up date": "Follow_Up_Date",
    "follow-up date": "Follow_Up_Date",
    "follow up": "Follow_Up_Date",
    "follow-up": "Follow_Up_Date",
    "time": "Time",
    "phone": "Phone_Number",
    "phone number": "Phone_Number",
    "number": "Phone_Number",
    "subject": "Subject",
    "priority": "Priority",
    "status": "Status",
    "score": "Score",
    "mark": "Score",
    "total": "Total",
    "task": "Task",
    "event": "Event",
    "item": "Item",
    "name": "Patient_Name",
    "patient name": "Patient_Name",
    "procedure": "Procedure",
    "next step": "Next_Step",
}


@dataclass
class RecordMatch:
    sheet_name: str
    row_number: int
    record: dict
    score: int


@dataclass
class PendingActionTarget:
    mode: str
    sheet_name: str
    row_number: int
    record: dict
    turns_left: int = 1


@dataclass
class PendingResetAction:
    scope: str
    sheet_names: list[str]


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
    pending_reset = _get_pending_reset_action(context, update.effective_user.id)
    if pending_reset:
        await _handle_reset_confirmation(update, context, message.text, pending_reset)
        return
    pending = _get_pending_clarification(context, update.effective_user.id)
    if pending:
        await _handle_clarification(update, context, pending)
        return
    pending_action = _get_pending_action_target(context, update.effective_user.id)
    if pending_action and _looks_like_followup_edit(message.text):
        await _handle_followup_edit(update, context, message.text, pending_action)
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


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.application.bot_data["config"]
    if not is_authorized(update, config):
        return
    message = update.effective_message
    if not message:
        return
    await message.reply_text("Voice notes aren't supported yet. Send text or a photo caption.")


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
    llm_parser = context.application.bot_data.get("llm_parser")
    user_id = update.effective_user.id
    chat_reply = _chat_reply(text)
    if chat_reply:
        _clear_pending_action_target(context, user_id)
        await update.effective_message.reply_text(chat_reply)
        return
    if _looks_like_correction(text):
        _clear_pending_action_target(context, user_id)
        await _handle_correction(update, context, text)
        return
    if _looks_like_delete_request(text):
        _clear_pending_action_target(context, user_id)
        await _handle_delete_request(update, context, text)
        return
    if _looks_like_edit_request(text):
        _clear_pending_action_target(context, user_id)
        await _handle_edit_request(update, context, text)
        return
    if _looks_like_done_request(text):
        _clear_pending_action_target(context, user_id)
        await _handle_done_request(update, context, text)
        return
    if _looks_like_resume_request(text):
        _clear_pending_action_target(context, user_id)
        await _handle_resume_request(update, context, text)
        return
    if _looks_like_reset_request(text):
        _clear_pending_action_target(context, user_id)
        await _handle_reset_request(update, context, text)
        return
    if llm_parser and getattr(llm_parser, "enabled", False) and _should_try_llm_split(text):
        handled = await _handle_llm_split(update, context, text)
        if handled:
            return
    intent = parser.parse(text)
    try:
        sheets.initialize()
    except Exception:
        logger.exception("Sheets unavailable.")
        await update.effective_message.reply_text("Google connection failed. Check sharing and credentials.")
        return
    if _should_answer_as_query(text, intent):
        _clear_pending_action_target(context, user_id)
        await update.effective_message.reply_text(query_engine.answer(text))
        return
    if intent.requires_follow_up:
        _set_pending_clarification(context, update.effective_user.id, PendingClarification(original_text=text, route=intent.route, question=intent.follow_up_question))
        await update.effective_message.reply_text(intent.follow_up_question)
        return
    if _is_probably_chat(text, intent):
        _clear_pending_action_target(context, user_id)
        await update.effective_message.reply_text("Here. Send anything to log, ask, or fix.")
        return
    if _should_save_to_inbox(text, intent):
        _clear_pending_action_target(context, user_id)
        await _save_to_inbox(sheets, text, parsed_type=intent.parsed_type or "Inbox", subject=intent.subject, linked_case_id=intent.linked_case_id, notes=intent.notes or "Needed safer fallback.")
        await update.effective_message.reply_text("Saved to Inbox.")
        return
    if intent.route == "task_done":
        _clear_pending_action_target(context, user_id)
        task_name = sheets.mark_task_done(intent.task)
        await update.effective_message.reply_text(f"Done: {task_name}." if task_name else "No matching open task.")
        return
    if intent.route == "tasks":
        today = today_local(parser.timezone_name)
        if _is_duplicate_recent(context, user_id, "Tasks", text):
            await update.effective_message.reply_text("Looks like the last task. Skipped.")
            return
        row_number = sheets.append_row("Tasks", [today, intent.task, intent.subject, intent.priority, intent.date, "Open", intent.recurring, intent.notes])
        _remember_last_action(context, user_id, "Tasks", row_number, text)
        _clear_pending_action_target(context, user_id)
        await update.effective_message.reply_text(f"Logged: {intent.task}.")
        return
    if intent.route == "schedule":
        recurrence_note = f" recurring:{intent.recurring}" if intent.recurring else ""
        if _is_duplicate_recent(context, user_id, "Schedule", text):
            await update.effective_message.reply_text("Looks like the last schedule item. Skipped.")
            return
        row_number = sheets.append_row("Schedule", [intent.date, intent.time, intent.metadata.get("event_type", "Reminder"), intent.subject, intent.event, intent.priority, intent.follow_up_date or intent.date, intent.status or "Scheduled", f"{intent.notes}{recurrence_note}".strip()])
        _remember_last_action(context, user_id, "Schedule", row_number, text)
        _clear_pending_action_target(context, user_id)
        await update.effective_message.reply_text(f"Added: {intent.event}.")
        return
    if intent.route == "assessments":
        today = today_local(parser.timezone_name)
        row_number = sheets.append_row("Assessments", [intent.date or today, intent.subject, intent.assessment_type, intent.score, intent.total, intent.percentage, intent.notes])
        _remember_last_action(context, user_id, "Assessments", row_number, text)
        _clear_pending_action_target(context, user_id)
        await update.effective_message.reply_text(f"Logged: {intent.subject or 'Assessment'} {intent.score}/{intent.total}.")
        return
    if intent.route == "patients":
        today = today_local(parser.timezone_name)
        if _is_duplicate_recent(context, user_id, "Patients", text):
            await update.effective_message.reply_text("Looks like the last patient log. Skipped.")
            return
        row_number = sheets.append_row("Patients", [intent.date or today, intent.subject, intent.case_id, intent.patient_name, intent.phone_number, intent.procedure, intent.tooth_or_area, intent.supervisor, intent.session_notes, intent.next_step, intent.follow_up_date, intent.photo_links])
        _remember_last_action(context, user_id, "Patients", row_number, text)
        _clear_pending_action_target(context, user_id)
        await update.effective_message.reply_text(f"Saved: Patient session for {intent.case_id} {intent.patient_name}.")
        return
    if intent.route == "materials":
        today = today_local(parser.timezone_name)
        row_number = sheets.append_row("Materials", [today, intent.item, intent.category, intent.subject, intent.priority, intent.status or "Pending", intent.store_or_source, intent.notes])
        _remember_last_action(context, user_id, "Materials", row_number, text)
        _clear_pending_action_target(context, user_id)
        await update.effective_message.reply_text(f"Added: {intent.item} to Materials.")
        return
    if intent.route == "courses":
        row_number = sheets.append_row("Courses", [intent.subject, intent.course_topic, intent.course_category, intent.status or "Active", intent.notes])
        _remember_last_action(context, user_id, "Courses", row_number, text)
        _clear_pending_action_target(context, user_id)
        await update.effective_message.reply_text(f"Saved: {intent.course_topic}.")
        return
    if intent.route == "study_progress":
        row_number, progress = sheets.upsert_study_progress(intent.subject, total_count=intent.total_count, completed_count=intent.completed_count, notes=intent.notes)
        _remember_last_action(context, user_id, "Courses", row_number, text)
        _clear_pending_action_target(context, user_id)
        total_text = progress.get("total", 0)
        done_text = progress.get("done", 0)
        remaining = max(total_text - done_text, 0) if total_text else ""
        suffix = f", {remaining} left." if total_text else "."
        await update.effective_message.reply_text(f"Saved: {intent.subject} study progress {done_text}/{total_text}{suffix}")
        return
    _clear_pending_action_target(context, user_id)
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


def _set_pending_action_target(context: ContextTypes.DEFAULT_TYPE, user_id: int, target: PendingActionTarget) -> None:
    action_map = context.application.bot_data.setdefault("pending_action_targets", {})
    action_map[user_id] = target


def _get_pending_action_target(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> PendingActionTarget | None:
    action_map = context.application.bot_data.setdefault("pending_action_targets", {})
    return action_map.get(user_id)


def _clear_pending_action_target(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    action_map = context.application.bot_data.setdefault("pending_action_targets", {})
    action_map.pop(user_id, None)


def _set_pending_reset_action(context: ContextTypes.DEFAULT_TYPE, user_id: int, action: PendingResetAction) -> None:
    reset_map = context.application.bot_data.setdefault("pending_reset_actions", {})
    reset_map[user_id] = action


def _get_pending_reset_action(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> PendingResetAction | None:
    reset_map = context.application.bot_data.setdefault("pending_reset_actions", {})
    return reset_map.get(user_id)


def _clear_pending_reset_action(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    reset_map = context.application.bot_data.setdefault("pending_reset_actions", {})
    reset_map.pop(user_id, None)


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


def _looks_like_delete_request(text: str) -> bool:
    lower = text.strip().lower()
    return lower.startswith(("delete ", "remove ")) and "that" not in lower[:15]


def _looks_like_edit_request(text: str) -> bool:
    lower = text.strip().lower()
    return lower.startswith(("change ", "update ", "edit ", "move ", "set "))


def _looks_like_done_request(text: str) -> bool:
    lower = text.strip().lower()
    return lower.startswith(("done ", "finished ", "completed ", "bought ", "received ", "case completed", "mark done "))


def _looks_like_resume_request(text: str) -> bool:
    lower = text.strip().lower()
    return lower.startswith(("reopen ", "resume ", "still not done", "patient came back", "need revisit", "need to revisit"))


def _looks_like_reset_request(text: str) -> bool:
    lower = text.strip().lower()
    return lower.startswith(("clear ", "reset ", "wipe "))


def _looks_like_followup_edit(text: str) -> bool:
    lower = text.strip().lower()
    if not lower:
        return False
    if _looks_like_delete_request(text) or _looks_like_edit_request(text) or _looks_like_correction(text):
        return False
    followup_starts = (
        "make it ",
        "make that ",
        "put it ",
        "set it ",
        "in ",
        "to ",
        "for ",
        "after ",
    )
    short_date_followup = bool(re.fullmatch(r"(tomorrow|today|friday|saturday|sunday|monday|tuesday|wednesday|thursday|next \w+|after tomorrow)", lower))
    return (
        lower.startswith(followup_starts)
        or short_date_followup
        or "after tomorrow" in lower
        or "in three days" in lower
        or "in two days" in lower
        or "in four days" in lower
        or bool(re.search(r"\bin \d+ days?\b", lower))
    )


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


async def _handle_delete_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    sheets: SheetService = context.application.bot_data["sheets"]
    parser: DentalParser = context.application.bot_data["parser"]
    match = _find_record_match(context, sheets, parser, update.effective_user.id, text)
    if not match:
        await update.effective_message.reply_text("Couldn't find that record.")
        return
    sheets.delete_row(match.sheet_name, match.row_number)
    _forget_action_by_row(context, update.effective_user.id, match.sheet_name, match.row_number)
    _clear_pending_action_target(context, update.effective_user.id)
    await update.effective_message.reply_text(f"Deleted: {_record_label(match.sheet_name, match.record)}.")


async def _handle_edit_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    sheets: SheetService = context.application.bot_data["sheets"]
    parser: DentalParser = context.application.bot_data["parser"]
    match = _find_record_match(context, sheets, parser, update.effective_user.id, text)
    if not match:
        await update.effective_message.reply_text("Couldn't find that record.")
        return
    updates = _extract_updates_for_record(text, match.sheet_name, match.record, parser)
    if not updates:
        _set_pending_action_target(
            context,
            update.effective_user.id,
            PendingActionTarget(mode="edit", sheet_name=match.sheet_name, row_number=match.row_number, record=match.record, turns_left=1),
        )
        await update.effective_message.reply_text("Tell me what to change and the new value.")
        return
    updated = sheets.update_record_fields(match.sheet_name, match.row_number, updates)
    _remember_last_action(context, update.effective_user.id, match.sheet_name, match.row_number, _record_raw_text(match.sheet_name, updated))
    _set_pending_action_target(
        context,
        update.effective_user.id,
        PendingActionTarget(mode="edit", sheet_name=match.sheet_name, row_number=match.row_number, record=updated, turns_left=1),
    )
    await update.effective_message.reply_text(_update_reply(match.sheet_name, updated, updates))


async def _handle_followup_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, target: PendingActionTarget) -> None:
    sheets: SheetService = context.application.bot_data["sheets"]
    parser: DentalParser = context.application.bot_data["parser"]
    updates = _extract_updates_for_record(text, target.sheet_name, target.record, parser)
    if not updates:
        _clear_pending_action_target(context, update.effective_user.id)
        await _save_to_inbox(sheets, text, parsed_type="Inbox", notes="Ambiguous follow-up edit.")
        await update.effective_message.reply_text("Saved to Inbox.")
        return
    updated = sheets.update_record_fields(target.sheet_name, target.row_number, updates)
    _remember_last_action(context, update.effective_user.id, target.sheet_name, target.row_number, _record_raw_text(target.sheet_name, updated))
    _clear_pending_action_target(context, update.effective_user.id)
    await update.effective_message.reply_text(_update_reply(target.sheet_name, updated, updates))


async def _handle_done_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    sheets: SheetService = context.application.bot_data["sheets"]
    parser: DentalParser = context.application.bot_data["parser"]
    match = _find_record_match(context, sheets, parser, update.effective_user.id, text)
    if not match:
        await update.effective_message.reply_text("Couldn't find that record.")
        return
    updates = _done_updates_for_record(match.sheet_name, match.record, text)
    if not updates:
        await update.effective_message.reply_text("Couldn't mark that done.")
        return
    updated = sheets.update_record_fields(match.sheet_name, match.row_number, updates)
    _remember_last_action(context, update.effective_user.id, match.sheet_name, match.row_number, _record_raw_text(match.sheet_name, updated))
    await update.effective_message.reply_text(f"Done: {_record_label(match.sheet_name, updated)}.")


async def _handle_resume_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    sheets: SheetService = context.application.bot_data["sheets"]
    parser: DentalParser = context.application.bot_data["parser"]
    match = _find_record_match(context, sheets, parser, update.effective_user.id, text)
    if not match:
        await update.effective_message.reply_text("Couldn't find that record.")
        return
    updates = _resume_updates_for_record(match.sheet_name, match.record, text)
    if not updates:
        await update.effective_message.reply_text("Couldn't reopen that.")
        return
    updated = sheets.update_record_fields(match.sheet_name, match.row_number, updates)
    _remember_last_action(context, update.effective_user.id, match.sheet_name, match.row_number, _record_raw_text(match.sheet_name, updated))
    await update.effective_message.reply_text(f"Reopened: {_record_label(match.sheet_name, updated)}.")


async def _handle_reset_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    user_id = update.effective_user.id
    lower = text.lower()
    scope_map = {
        "all": ["Inbox", "Tasks", "Schedule", "Assessments", "Patients", "Materials", "Courses"],
        "inbox": ["Inbox"],
        "tasks": ["Tasks"],
        "schedule": ["Schedule"],
        "assessments": ["Assessments"],
        "patients": ["Patients"],
        "materials": ["Materials"],
        "courses": ["Courses"],
    }
    scope = next((key for key in scope_map if key in lower), "")
    if not scope:
        await update.effective_message.reply_text("Say clear inbox, clear schedule, or clear all records.")
        return
    _set_pending_reset_action(context, user_id, PendingResetAction(scope=scope, sheet_names=scope_map[scope]))
    label = "all records" if scope == "all" else scope
    await update.effective_message.reply_text(f"Confirm clear {label}. Reply: confirm clear")


async def _handle_reset_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, pending_reset: PendingResetAction) -> None:
    user_id = update.effective_user.id
    if text.strip().lower() != "confirm clear":
        _clear_pending_reset_action(context, user_id)
        await update.effective_message.reply_text("Clear cancelled.")
        return
    sheets: SheetService = context.application.bot_data["sheets"]
    for sheet_name in pending_reset.sheet_names:
        sheets.clear_sheet_data(sheet_name)
    if pending_reset.scope == "all":
        sheets._refresh_presentation_safe()
    _clear_pending_reset_action(context, user_id)
    _clear_pending_action_target(context, user_id)
    await update.effective_message.reply_text(f"Cleared: {pending_reset.scope}.")


async def _handle_llm_split(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    llm_parser = context.application.bot_data.get("llm_parser")
    if not llm_parser:
        return False
    items = llm_parser.split_message(text)
    if len(items) <= 1:
        return False
    sheets: SheetService = context.application.bot_data["sheets"]
    parser: DentalParser = context.application.bot_data["parser"]
    summaries: list[str] = []
    saved_any = False
    for item in items[:5]:
        item_text = item.get("text", "").strip()
        if not item_text:
            continue
        intent = parser.parse(item_text)
        if _should_answer_as_query(item_text, intent) or _should_save_to_inbox(item_text, intent):
            continue
        saved = await _apply_intent_direct(context, update.effective_user.id, item_text, intent, sheets, parser)
        if saved:
            summaries.append(saved)
            saved_any = True
    if saved_any:
        await update.effective_message.reply_text("Saved:\n" + "\n".join(summaries[:4]))
        return True
    return False


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


def _forget_action_by_row(context: ContextTypes.DEFAULT_TYPE, user_id: int, sheet_name: str, row_number: int) -> None:
    action_map = context.application.bot_data.setdefault("last_actions", {})
    history = action_map.get(user_id, [])
    history = [
        item for item in history
        if not (item.get("sheet_name") == sheet_name and item.get("row_number") == row_number)
    ]
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


def _find_record_match(
    context: ContextTypes.DEFAULT_TYPE,
    sheets: SheetService,
    parser: DentalParser,
    user_id: int,
    text: str,
) -> RecordMatch | None:
    lower = text.lower()
    mentioned_sheet = _extract_sheet_hint(lower)
    mentioned_subject = parser._normalize_subject(text)
    mentioned_case = _extract_case_id(text)
    field_alias = _extract_field_alias(lower)
    target_text = _target_text(text)
    keywords = _search_keywords(target_text)
    last_actions = list(reversed(context.application.bot_data.setdefault("last_actions", {}).get(user_id, [])))
    action_scores = {(item.get("sheet_name"), item.get("row_number")): index for index, item in enumerate(last_actions)}
    candidates: list[RecordMatch] = []
    sheet_names = [mentioned_sheet] if mentioned_sheet else list(EDITABLE_SHEETS)
    for sheet_name in sheet_names:
        for row_number, record in sheets.get_recent_rows(sheet_name, limit=12):
            score = 0
            if mentioned_sheet and sheet_name == mentioned_sheet:
                score += 5
            if mentioned_subject and record.get("Subject") == mentioned_subject:
                score += 4
            if mentioned_case and record.get("Case_ID", "").upper() == mentioned_case:
                score += 7
            haystack = _record_search_text(sheet_name, record)
            overlap = len(keywords.intersection(_search_keywords(haystack)))
            score += overlap
            if field_alias and field_alias in record:
                score += 1
            if "last" in lower and score >= 0:
                score += 2
            if (sheet_name, row_number) in action_scores:
                score += max(0, 4 - action_scores[(sheet_name, row_number)])
            candidates.append(RecordMatch(sheet_name=sheet_name, row_number=row_number, record=record, score=score))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.score, item.row_number), reverse=True)
    best = candidates[0]
    if best.score <= 0 and not ("last" in lower or mentioned_case):
        return None
    return best


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


def _extract_case_id(text: str) -> str:
    match = re.search(r"\b([A-Za-z]\d{2,})\b", text)
    return match.group(1).upper() if match else ""


def _extract_field_alias(lower_text: str) -> str:
    for alias, field in sorted(FIELD_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if alias in lower_text:
            return field
    return ""


def _target_text(text: str) -> str:
    lower = text.lower()
    if " to " in lower:
        return text.rsplit(" to ", 1)[0]
    return text


def _search_keywords(text: str) -> set[str]:
    tokens = {token for token in re.findall(r"[a-z0-9/+-]{3,}", text.lower()) if token not in STOPWORDS}
    return tokens


def _record_search_text(sheet_name: str, record: dict) -> str:
    important_fields = {
        "Tasks": ("Task", "Subject", "Priority", "Due_Date", "Notes"),
        "Schedule": ("Event", "Type", "Subject", "Date", "Time", "Notes"),
        "Assessments": ("Subject", "Assessment_Type", "Score", "Total", "Notes"),
        "Patients": ("Case_ID", "Patient_Name", "Subject", "Procedure", "Phone_Number", "Next_Step", "Session_Notes"),
        "Materials": ("Item", "Category", "Subject", "Store_or_Source", "Notes"),
        "Courses": ("Subject", "Lecture_or_Topic", "Category", "Notes"),
    }
    return " ".join(record.get(field, "") for field in important_fields.get(sheet_name, tuple(record.keys())))


def _record_label(sheet_name: str, record: dict) -> str:
    label_map = {
        "Tasks": record.get("Task", "Task"),
        "Schedule": record.get("Event", "Schedule item"),
        "Assessments": f"{record.get('Subject', 'Assessment')} {record.get('Score', '').strip()}/{record.get('Total', '').strip()}".strip(),
        "Patients": f"{record.get('Case_ID', '').strip()} {record.get('Patient_Name', '').strip()}".strip(),
        "Materials": record.get("Item", "Material"),
        "Courses": f"{record.get('Subject', '').strip()} {record.get('Lecture_or_Topic', '').strip()}".strip(),
    }
    return label_map.get(sheet_name, sheet_name).strip() or sheet_name


def _record_raw_text(sheet_name: str, record: dict) -> str:
    field_map = {
        "Tasks": ("Task", "Subject", "Due_Date"),
        "Schedule": ("Event", "Subject", "Date", "Time"),
        "Assessments": ("Subject", "Assessment_Type", "Score", "Total", "Date"),
        "Patients": ("Case_ID", "Patient_Name", "Subject", "Procedure", "Follow_Up_Date"),
        "Materials": ("Item", "Subject", "Store_or_Source"),
        "Courses": ("Subject", "Lecture_or_Topic", "Category"),
    }
    return " ".join(record.get(field, "") for field in field_map.get(sheet_name, tuple(record.keys()))).strip()


def _update_reply(sheet_name: str, record: dict, updates: dict[str, str]) -> str:
    label = _record_label(sheet_name, record)
    if sheet_name == "Schedule":
        when = " ".join(part for part in (record.get("Date", ""), record.get("Time", "")) if part).strip()
        return f"Updated: {label}. {when}".strip()
    if sheet_name == "Assessments":
        return f"Updated: {label} ({record.get('Percentage', '').strip()}).".replace("  ", " ").strip()
    if sheet_name == "Patients" and any(key in updates for key in ("Date", "Follow_Up_Date")):
        when = updates.get("Follow_Up_Date") or updates.get("Date") or ""
        return f"Updated: {label}. {when}".strip()
    return f"Updated: {label}."


async def _apply_intent_direct(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    text: str,
    intent,
    sheets: SheetService,
    parser: DentalParser,
) -> str:
    today = today_local(parser.timezone_name)
    if intent.route == "tasks":
        row_number = sheets.append_row("Tasks", [today, intent.task, intent.subject, intent.priority, intent.date, "Open", intent.recurring, intent.notes])
        _remember_last_action(context, user_id, "Tasks", row_number, text)
        return f"- Task: {intent.task}"
    if intent.route == "schedule":
        recurrence_note = f" recurring:{intent.recurring}" if intent.recurring else ""
        row_number = sheets.append_row("Schedule", [intent.date, intent.time, intent.metadata.get("event_type", "Reminder"), intent.subject, intent.event, intent.priority, intent.follow_up_date or intent.date, intent.status or "Scheduled", f"{intent.notes}{recurrence_note}".strip()])
        _remember_last_action(context, user_id, "Schedule", row_number, text)
        return f"- Schedule: {intent.event}"
    if intent.route == "assessments":
        row_number = sheets.append_row("Assessments", [intent.date or today, intent.subject, intent.assessment_type, intent.score, intent.total, intent.percentage, intent.notes])
        _remember_last_action(context, user_id, "Assessments", row_number, text)
        return f"- Mark: {intent.subject or 'Assessment'} {intent.score}/{intent.total}"
    if intent.route == "patients":
        row_number = sheets.append_row("Patients", [intent.date or today, intent.subject, intent.case_id, intent.patient_name, intent.phone_number, intent.procedure, intent.tooth_or_area, intent.supervisor, intent.session_notes, intent.next_step, intent.follow_up_date, intent.photo_links])
        _remember_last_action(context, user_id, "Patients", row_number, text)
        return f"- Patient: {_record_label('Patients', {'Case_ID': intent.case_id, 'Patient_Name': intent.patient_name})}"
    if intent.route == "materials":
        row_number = sheets.append_row("Materials", [today, intent.item, intent.category, intent.subject, intent.priority, intent.status or 'Pending', intent.store_or_source, intent.notes])
        _remember_last_action(context, user_id, "Materials", row_number, text)
        return f"- Material: {intent.item}"
    if intent.route == "study_progress":
        row_number, _ = sheets.upsert_study_progress(intent.subject, total_count=intent.total_count, completed_count=intent.completed_count, notes=intent.notes)
        _remember_last_action(context, user_id, "Courses", row_number, text)
        return f"- Study: {intent.subject}"
    return ""


def _extract_updates_for_record(text: str, sheet_name: str, record: dict, parser: DentalParser) -> dict[str, str]:
    lower = text.lower().strip()
    value = _extract_update_value(text)
    field = _extract_field_alias(lower)
    if lower.startswith("move ") and not field:
        field = "Date"
    if not value:
        return {}
    updates = _build_field_updates(field, value, sheet_name, record, parser)
    if updates:
        return updates
    inferred = _infer_updates_from_value(value, sheet_name, record, parser)
    return inferred


def _done_updates_for_record(sheet_name: str, record: dict, text: str) -> dict[str, str]:
    if sheet_name == "Tasks":
        return {"Status": "Done"}
    if sheet_name == "Schedule":
        return {"Status": "Done"}
    if sheet_name == "Materials":
        lower = text.lower()
        if "received" in lower:
            return {"Status": "Received"}
        if "bought" in lower:
            return {"Status": "Bought"}
        return {"Status": "Done"}
    if sheet_name == "Courses":
        return {"Status": "Done"}
    if sheet_name == "Patients":
        return {"Next_Step": "Completed", "Follow_Up_Date": ""}
    return {}


def _resume_updates_for_record(sheet_name: str, record: dict, text: str) -> dict[str, str]:
    if sheet_name == "Tasks":
        return {"Status": "Open"}
    if sheet_name == "Schedule":
        return {"Status": "Scheduled"}
    if sheet_name == "Materials":
        return {"Status": "Pending"}
    if sheet_name == "Courses":
        return {"Status": "Active"}
    if sheet_name == "Patients":
        return {"Next_Step": text.strip()[:180]}
    return {}


def _build_field_updates(field: str, value: str, sheet_name: str, record: dict, parser: DentalParser) -> dict[str, str]:
    if field in {"Date", "Due_Date", "Follow_Up_Date"}:
        parsed_dt, _ = extract_datetime(value, parser.timezone_name)
        if not parsed_dt:
            return {}
        normalized = parsed_dt.strftime("%Y-%m-%d")
        if field == "Date" and sheet_name == "Tasks":
            return {"Due_Date": normalized}
        if field == "Date" and sheet_name == "Patients":
            return {"Date": normalized}
        if field == "Date" and sheet_name == "Assessments":
            return {"Date": normalized}
        if sheet_name == "Schedule" and field == "Date":
            return {"Date": normalized, "Reminder_Date": normalized}
        return {field: normalized}
    if field == "Time":
        time_value = extract_time_only(value)
        return {"Time": time_value} if time_value else {}
    if field == "Phone_Number":
        digits = value.strip()
        return {"Phone_Number": digits}
    if field == "Subject":
        subject = parser._normalize_subject(value)
        return {"Subject": subject} if subject else {}
    if field == "Priority":
        priority = _match_one(value, PRIORITIES)
        return {"Priority": priority} if priority else {}
    if field == "Status":
        statuses = {
            "Tasks": TASK_STATUSES,
            "Schedule": SCHEDULE_STATUSES,
            "Materials": MATERIAL_STATUSES,
            "Courses": ("Not Started", "Active", "Done", "Archived"),
            "Patients": ("Logged",),
            "Assessments": tuple(),
        }.get(sheet_name, tuple())
        status = _match_one(value, statuses)
        return {"Status": status} if status else {}
    if field == "Score":
        score_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*/\s*(\d{1,3}(?:\.\d+)?)", value)
        if score_match:
            score = score_match.group(1)
            total = score_match.group(2)
            percentage = f"{(float(score) / float(total)) * 100:.0f}%"
            return {"Score": score, "Total": total, "Percentage": percentage}
        return {"Score": value.strip()}
    if field == "Total":
        return {"Total": value.strip()}
    if field in {"Task", "Event", "Item", "Patient_Name", "Procedure", "Next_Step"}:
        return {field: value.strip()}
    return {}


def _infer_updates_from_value(value: str, sheet_name: str, record: dict, parser: DentalParser) -> dict[str, str]:
    updates: dict[str, str] = {}
    parsed_dt, _ = extract_datetime(value, parser.timezone_name)
    time_value = extract_time_only(value)
    if sheet_name == "Schedule":
        if parsed_dt:
            normalized = parsed_dt.strftime("%Y-%m-%d")
            updates["Date"] = normalized
            updates["Reminder_Date"] = normalized
        if time_value:
            updates["Time"] = time_value
    elif sheet_name == "Tasks" and parsed_dt:
        updates["Due_Date"] = parsed_dt.strftime("%Y-%m-%d")
    elif sheet_name == "Patients" and parsed_dt:
        target = "Follow_Up_Date" if "follow" in value.lower() or record.get("Follow_Up_Date") else "Date"
        updates[target] = parsed_dt.strftime("%Y-%m-%d")
    score_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*/\s*(\d{1,3}(?:\.\d+)?)", value)
    if sheet_name == "Assessments" and score_match:
        score = score_match.group(1)
        total = score_match.group(2)
        updates.update({"Score": score, "Total": total, "Percentage": f"{(float(score) / float(total)) * 100:.0f}%"})
    if not updates:
        subject = parser._normalize_subject(value)
        if subject:
            updates["Subject"] = subject
        else:
            priority = _match_one(value, PRIORITIES)
            if priority and "Priority" in record:
                updates["Priority"] = priority
    return updates


def _match_one(text: str, choices) -> str:
    lower = text.lower()
    for choice in choices:
        if choice.lower() in lower:
            return choice
    return ""


def _should_try_llm_split(text: str) -> bool:
    lower = text.lower()
    separators = [",", " and ", " also ", ";"]
    signal_count = sum(lower.count(token) for token in separators)
    return signal_count >= 2 or ("patient" in lower and "quiz" in lower)


def _extract_update_value(text: str) -> str:
    lower = text.lower().strip()
    if " to " in lower:
        return text.rsplit(" to ", 1)[1].strip()
    patterns = (
        r"^(?:change|update|edit|move|set)\s+.+?\s+(?:in|for|after|on|to|till|til)\s+(.+)$",
        r"^(?:make|put|set)\s+(?:it|that)\s+(?:in|for|after|on|to|till|til)\s+(.+)$",
        r"^(?:in|for|after|on|to|till|til)\s+(.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text.strip(), flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return text.strip() if _looks_like_followup_edit(text) else ""


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


def _should_answer_as_query(text: str, intent) -> bool:
    lower = text.strip().lower()
    strong_query_markers = (
        "how many",
        "how much",
        "what do i",
        "what's next",
        "whats next",
        "what do i have",
        "what follow-ups",
        "what follow ups",
        "when is",
        "when do i",
        "tell me",
        "show me",
        "list",
        "which",
        "do i have",
        "coming up",
    )
    if lower.endswith("?"):
        return True
    if any(lower.startswith(marker) for marker in strong_query_markers):
        return True
    if any(marker in lower for marker in ("how many", "coming up", "what do i have", "tell me", "show me")):
        return True
    return intent.route == "query"


def _should_save_to_inbox(text: str, intent) -> bool:
    lower = text.strip().lower()
    if intent.route == "inbox":
        return True
    if intent.route == "schedule" and intent.confidence < 0.82:
        return True
    if intent.route == "tasks" and intent.confidence < 0.82:
        return True
    if intent.route == "courses" and intent.confidence < 0.8:
        return True
    if any(fragment in lower for fragment in ("and make it", "and move it", "and delete it", "and tell me")):
        return True
    return False


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
