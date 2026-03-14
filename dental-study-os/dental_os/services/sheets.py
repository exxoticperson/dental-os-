from __future__ import annotations

from datetime import datetime
import re
from typing import Iterable

import gspread
from gspread.utils import rowcol_to_a1

from dental_os.config import AppConfig
from dental_os.constants import (
    ACCENT_BG,
    ACCENT_TEXT,
    COURSE_STATUSES,
    DEFAULT_SETTINGS_ROWS,
    DEFAULT_SHEET_COLS,
    DEFAULT_SHEET_ROWS,
    EVENT_TYPES,
    INBOX_STATUSES,
    MATERIAL_CATEGORIES,
    MATERIAL_STATUSES,
    NEUTRAL_HEADER_BG,
    PRIORITIES,
    SCHEDULE_STATUSES,
    SECTION_BG,
    SHEET_COLUMN_WIDTHS,
    SHEET_HEADERS,
    SHEET_ORDER,
    STRIPE_BG,
    SUBJECTS,
    SUMMARY_BG,
    TASK_STATUSES,
    WHITE_BG,
)
from dental_os.services.google import GoogleClients


class SheetService:
    def __init__(self, config: AppConfig, google: GoogleClients) -> None:
        self.config = config
        self.google = google
        self.spreadsheet = self.google.gspread_client.open_by_key(config.google_spreadsheet_id)
        self._initialized = False
        self._formatting_attempted = False

    def initialize(self) -> None:
        if self._initialized:
            if not self._formatting_attempted:
                self._apply_formatting_safe()
            return
        for title in SHEET_ORDER:
            worksheet = self._get_or_create_worksheet(title)
            self._ensure_headers(worksheet, SHEET_HEADERS[title])
        self._ensure_settings_seeded()
        self._setup_dashboard()
        self._initialized = True
        self._apply_formatting_safe()

    def append_row(self, sheet_name: str, values: list[str]) -> int:
        self.initialize()
        worksheet = self.spreadsheet.worksheet(sheet_name)
        row_number = len(worksheet.get_all_values()) + 1
        worksheet.append_row(values, value_input_option="USER_ENTERED")
        return row_number

    def get_records(self, sheet_name: str) -> list[dict]:
        self.initialize()
        worksheet = self.spreadsheet.worksheet(sheet_name)
        values = worksheet.get_all_values()
        if not values:
            return []
        headers = values[0]
        records = []
        for row in values[1:]:
            if not any(cell.strip() for cell in row):
                continue
            padded = row + [""] * (len(headers) - len(row))
            records.append(dict(zip(headers, padded)))
        return records

    def update_row(self, sheet_name: str, row_number: int, values: list[str]) -> None:
        self.initialize()
        worksheet = self.spreadsheet.worksheet(sheet_name)
        start = rowcol_to_a1(row_number, 1)
        end = rowcol_to_a1(row_number, len(values))
        worksheet.update(f"{start}:{end}", [values], value_input_option="USER_ENTERED")

    def mark_task_done(self, task_fragment: str) -> str | None:
        self.initialize()
        worksheet = self.spreadsheet.worksheet("Tasks")
        values = worksheet.get_all_values()
        if len(values) <= 1:
            return None
        headers = values[0]
        task_idx = headers.index("Task")
        status_idx = headers.index("Status")
        for row_number in range(len(values) - 1, 0, -1):
            row = values[row_number]
            padded = row + [""] * (len(headers) - len(row))
            task_value = padded[task_idx]
            status_value = padded[status_idx]
            if status_value == "Done":
                continue
            if not task_fragment or task_fragment.lower() in task_value.lower():
                padded[status_idx] = "Done"
                self.update_row("Tasks", row_number + 1, padded[: len(headers)])
                return task_value
        return None

    def attach_photo_link(self, case_id: str, photo_link: str) -> bool:
        self.initialize()
        worksheet = self.spreadsheet.worksheet("Patients")
        values = worksheet.get_all_values()
        if len(values) <= 1:
            return False
        headers = values[0]
        case_idx = headers.index("Case_ID")
        photo_idx = headers.index("Photo_Links")
        for row_number in range(len(values) - 1, 0, -1):
            row = values[row_number]
            padded = row + [""] * (len(headers) - len(row))
            if padded[case_idx].strip().upper() != case_id.upper():
                continue
            existing = padded[photo_idx].strip()
            padded[photo_idx] = f"{existing}\n{photo_link}".strip()
            self.update_row("Patients", row_number + 1, padded[: len(headers)])
            return True
        return False

    def get_due_schedule_rows(self, now_value: datetime) -> list[tuple[int, dict]]:
        self.initialize()
        worksheet = self.spreadsheet.worksheet("Schedule")
        values = worksheet.get_all_values()
        if len(values) <= 1:
            return []
        headers = values[0]
        output: list[tuple[int, dict]] = []
        for row_number, row in enumerate(values[1:], start=2):
            if not any(cell.strip() for cell in row):
                continue
            padded = row + [""] * (len(headers) - len(row))
            record = dict(zip(headers, padded))
            reminder_date = record.get("Reminder_Date", "").strip()
            if not reminder_date:
                continue
            time_value = record.get("Time", "").strip() or "09:00"
            try:
                due_dt = datetime.fromisoformat(f"{reminder_date}T{time_value}")
            except ValueError:
                continue
            if due_dt <= now_value:
                output.append((row_number, record))
        return output

    def update_schedule_record(self, row_number: int, record: dict) -> None:
        headers = SHEET_HEADERS["Schedule"]
        self.update_row("Schedule", row_number, [record.get(header, "") for header in headers])

    def get_recent_rows(self, sheet_name: str, limit: int = 20) -> list[tuple[int, dict]]:
        self.initialize()
        worksheet = self.spreadsheet.worksheet(sheet_name)
        values = worksheet.get_all_values()
        if len(values) <= 1:
            return []
        headers = values[0]
        output: list[tuple[int, dict]] = []
        for row_number in range(len(values), 1, -1):
            row = values[row_number - 1]
            if not any(cell.strip() for cell in row):
                continue
            padded = row + [""] * (len(headers) - len(row))
            output.append((row_number, dict(zip(headers, padded))))
            if len(output) >= limit:
                break
        return output

    def update_record_fields(self, sheet_name: str, row_number: int, updates: dict[str, str]) -> dict:
        self.initialize()
        worksheet = self.spreadsheet.worksheet(sheet_name)
        values = worksheet.get_all_values()
        if row_number <= 1 or row_number > len(values):
            raise ValueError("Row not found.")
        headers = values[0]
        row = values[row_number - 1]
        padded = row + [""] * (len(headers) - len(row))
        record = dict(zip(headers, padded))
        for key, value in updates.items():
            if key in record:
                record[key] = value
        self.update_row(sheet_name, row_number, [record.get(header, "") for header in headers])
        return record

    def delete_row(self, sheet_name: str, row_number: int) -> None:
        self.initialize()
        worksheet = self.spreadsheet.worksheet(sheet_name)
        if row_number > 1:
            worksheet.delete_rows(row_number)

    def clear_operational_data(self) -> None:
        self.initialize()
        for sheet_name in ("Inbox", "Tasks", "Schedule", "Assessments", "Patients", "Materials", "Courses"):
            worksheet = self.spreadsheet.worksheet(sheet_name)
            if worksheet.row_count > 1:
                worksheet.batch_clear([f"A2:{rowcol_to_a1(worksheet.row_count, worksheet.col_count)}"])
        self._setup_dashboard()

    def upsert_study_progress(self, subject: str, total_count: str = "", completed_count: str = "", notes: str = "") -> tuple[int, dict]:
        self.initialize()
        worksheet = self.spreadsheet.worksheet("Courses")
        values = worksheet.get_all_values()
        headers = values[0] if values else SHEET_HEADERS["Courses"]
        match_row = None
        existing = {"Subject": subject, "Lecture_or_Topic": "Lecture Progress", "Category": "Progress", "Status": "Active", "Notes": ""}
        if len(values) > 1:
            for row_number, row in enumerate(values[1:], start=2):
                padded = row + [""] * (len(headers) - len(row))
                record = dict(zip(headers, padded))
                if record.get("Subject") == subject and record.get("Category") == "Progress":
                    match_row = row_number
                    existing = record
                    break
        current_total, current_done = self._extract_progress_counts(existing.get("Notes", ""))
        if total_count:
            current_total = int(total_count)
        if completed_count:
            current_done = int(completed_count)
        current_done = max(0, current_done)
        if current_total and current_done > current_total:
            current_done = current_total
        notes_value = self._build_progress_notes(current_total, current_done, notes)
        row_values = [subject, "Lecture Progress", "Progress", "Active", notes_value]
        if match_row:
            self.update_row("Courses", match_row, row_values)
            return match_row, {"subject": subject, "total": current_total, "done": current_done}
        row_number = self.append_row("Courses", row_values)
        return row_number, {"subject": subject, "total": current_total, "done": current_done}

    def get_study_progress(self, subject: str = "") -> list[dict]:
        rows = []
        for row in self.get_records("Courses"):
            if row.get("Category") != "Progress":
                continue
            if subject and row.get("Subject") != subject:
                continue
            total_count, completed_count = self._extract_progress_counts(row.get("Notes", ""))
            row = row.copy()
            row["Total_Count"] = total_count
            row["Completed_Count"] = completed_count
            row["Remaining_Count"] = max(total_count - completed_count, 0) if total_count else ""
            rows.append(row)
        return rows

    def _extract_progress_counts(self, notes: str) -> tuple[int, int]:
        total_match = re.search(r"total=(\d+)", notes or "")
        done_match = re.search(r"done=(\d+)", notes or "")
        return int(total_match.group(1)) if total_match else 0, int(done_match.group(1)) if done_match else 0

    def _build_progress_notes(self, total_count: int, completed_count: int, notes: str) -> str:
        clean = re.sub(r"\b(total|done)=\d+\b", "", notes or "").strip(" ;")
        prefix = f"total={total_count}; done={completed_count}"
        return f"{prefix}; {clean}".strip(" ;")

    def _get_or_create_worksheet(self, title: str) -> gspread.Worksheet:
        try:
            return self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return self.spreadsheet.add_worksheet(title=title, rows=DEFAULT_SHEET_ROWS[title], cols=DEFAULT_SHEET_COLS[title])

    def _ensure_headers(self, worksheet: gspread.Worksheet, headers: list[str]) -> None:
        current = worksheet.row_values(1)
        if current != headers:
            start = rowcol_to_a1(1, 1)
            end = rowcol_to_a1(1, len(headers))
            worksheet.update(f"{start}:{end}", [headers], value_input_option="RAW")

    def _ensure_settings_seeded(self) -> None:
        worksheet = self.spreadsheet.worksheet("Settings")
        values = worksheet.get_all_values()
        if len(values) > 1:
            return
        rows = [list(row) for row in DEFAULT_SETTINGS_ROWS]
        if rows:
            end = rowcol_to_a1(len(rows) + 1, 4)
            worksheet.update(f"A2:{end}", rows, value_input_option="RAW")

    def _setup_dashboard(self) -> None:
        worksheet = self.spreadsheet.worksheet("Dashboard")
        worksheet.clear()
        layout = [
            ["Dental Study OS", "", "", "Cairo", '=TEXT(NOW(),"ddd d mmm, h:mm AM/PM")', "", "", ""],
            ["Today View", "", "", "", "", "", "", ""],
            ["Open Tasks", '=COUNTIFS(Tasks!B2:B,"<>",Tasks!F2:F,"<>Done")', "Next 7 Days", '=COUNTIFS(Schedule!A2:A,">="&TODAY(),Schedule!A2:A,"<="&TODAY()+7,Schedule!H2:H,"<>Cancelled")', "Follow-Ups", '=COUNTIFS(Patients!C2:C,"<>",Patients!K2:K,"<>")', "Pending Materials", '=COUNTIFS(Materials!B2:B,"<>",Materials!F2:F,"<>Done",Materials!F2:F,"<>Bought")'],
            [],
            ["Upcoming"],
            ["Date", "Time", "Type", "Subject", "Event", "Priority", "Status"],
            ['=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({IF(Schedule!A2:A<>"",TEXT(Schedule!A2:A,"ddd d mmm"),""),IF(Schedule!B2:B<>"",TEXT(Schedule!B2:B,"h:mm AM/PM"),""),Schedule!C2:C,Schedule!D2:D,Schedule!E2:E,Schedule!F2:F,Schedule!H2:H},Schedule!A2:A<>"",Schedule!A2:A>=TODAY(),Schedule!A2:A<=TODAY()+7,Schedule!H2:H<>"Cancelled"),1,TRUE,2,TRUE),6,7),"")'],
            [],
            ["Patient Follow-Ups"],
            ["Date", "Subject", "Case_ID", "Patient", "Next Step", "Follow-Up"],
            ['=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({IF(Patients!A2:A<>"",TEXT(Patients!A2:A,"ddd d mmm"),""),Patients!B2:B,Patients!C2:C,Patients!D2:D,Patients!J2:J,IF(Patients!K2:K<>"",TEXT(Patients!K2:K,"ddd d mmm"),"")},Patients!C2:C<>"",Patients!K2:K<>""),6,TRUE),5,6),"")'],
            [],
            ["Recent Patient Sessions"],
            ["Date", "Subject", "Case_ID", "Patient", "Procedure"],
            ['=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({IF(Patients!A2:A<>"",TEXT(Patients!A2:A,"ddd d mmm"),""),Patients!B2:B,Patients!C2:C,Patients!D2:D,Patients!F2:F},Patients!C2:C<>""),1,FALSE),5,5),"")'],
            [],
            ["Open Tasks"],
            ["Created", "Task", "Subject", "Priority", "Due", "Status"],
            ['=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({IF(Tasks!A2:A<>"",TEXT(Tasks!A2:A,"ddd d mmm"),""),Tasks!B2:B,Tasks!C2:C,Tasks!D2:D,IF(Tasks!E2:E<>"",TEXT(Tasks!E2:E,"ddd d mmm"),""),Tasks!F2:F},Tasks!B2:B<>"",Tasks!F2:F<>"Done"),4,FALSE,5,TRUE),5,6),"")'],
            [],
            ["Recent Marks"],
            ["Date", "Subject", "Type", "Score", "Total", "Percentage"],
            ['=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({IF(Assessments!A2:A<>"",TEXT(Assessments!A2:A,"ddd d mmm"),""),Assessments!B2:B,Assessments!C2:C,Assessments!D2:D,Assessments!E2:E,Assessments!F2:F},Assessments!B2:B<>""),1,FALSE),5,6),"")'],
            [],
            ["Pending Materials"],
            ["Date", "Item", "Category", "Subject", "Priority", "Status"],
            ['=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({IF(Materials!A2:A<>"",TEXT(Materials!A2:A,"ddd d mmm"),""),Materials!B2:B,Materials!C2:C,Materials!D2:D,Materials!E2:E,Materials!F2:F},Materials!B2:B<>"",Materials!F2:F<>"Done",Materials!F2:F<>"Bought"),1,FALSE),4,6),"")'],
            [],
            ["Study Progress"],
            ["Subject", "Studied", "Left", "Notes"],
            ['=IFERROR(ARRAY_CONSTRAIN(FILTER({Courses!A2:A,REGEXEXTRACT(Courses!E2:E,"done=(\\d+)"),IFERROR(REGEXEXTRACT(Courses!E2:E,"total=(\\d+)")-REGEXEXTRACT(Courses!E2:E,"done=(\\d+)"),""),Courses!E2:E},Courses!C2:C="Progress"),6,4),"")'],
        ]
        worksheet.update("A1", layout, value_input_option="USER_ENTERED")

    def _apply_formatting(self) -> None:
        requests: list[dict] = [self._spreadsheet_properties_request()]
        requests.extend(self._delete_conditional_rule_requests())
        for title in SHEET_ORDER:
            worksheet = self.spreadsheet.worksheet(title)
            sheet_id = worksheet.id
            requests.extend(self._sheet_style_requests(sheet_id, title, worksheet.row_count, worksheet.col_count))
            requests.extend(self._column_width_requests(sheet_id, title))
            requests.extend(self._number_format_requests(sheet_id, title))
            requests.extend(self._validation_requests(sheet_id, title))
            requests.extend(self._conditional_requests(sheet_id, title))
        self.spreadsheet.batch_update({"requests": requests})

    def _apply_formatting_safe(self) -> None:
        if self._formatting_attempted:
            return
        try:
            self._apply_formatting()
        except Exception:
            # Formatting should never block logging or query access.
            pass
        finally:
            self._formatting_attempted = True

    def _spreadsheet_properties_request(self) -> dict:
        return {
            "updateSpreadsheetProperties": {
                "properties": {
                    "locale": "en_GB",
                    "timeZone": self.config.default_timezone,
                    "autoRecalc": "ON_CHANGE",
                },
                "fields": "locale,timeZone,autoRecalc",
            }
        }

    def _delete_conditional_rule_requests(self) -> list[dict]:
        metadata = self.spreadsheet.fetch_sheet_metadata()
        requests: list[dict] = []
        for sheet in metadata.get("sheets", []):
            rules = sheet.get("conditionalFormats", [])
            for index in range(len(rules) - 1, -1, -1):
                requests.append(
                    {
                        "deleteConditionalFormatRule": {
                            "sheetId": sheet["properties"]["sheetId"],
                            "index": index,
                        }
                    }
                )
        return requests

    def _sheet_style_requests(self, sheet_id: int, title: str, row_count: int, col_count: int) -> list[dict]:
        requests = [
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": NEUTRAL_HEADER_BG,
                            "textFormat": {"bold": True, "foregroundColor": ACCENT_TEXT, "fontSize": 10, "fontFamily": "Aptos"},
                            "horizontalAlignment": "LEFT",
                            "borders": {"bottom": {"style": "SOLID", "color": {"red": 0.84, "green": 0.87, "blue": 0.9}}},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,borders)",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": 0, "endColumnIndex": col_count},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": WHITE_BG,
                            "textFormat": {"foregroundColor": {"red": 0.12, "green": 0.12, "blue": 0.12}, "fontSize": 10, "fontFamily": "Aptos"},
                            "wrapStrategy": "WRAP",
                            "verticalAlignment": "MIDDLE",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)",
                }
            },
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1, "hideGridlines": title == "Dashboard"}},
                    "fields": "gridProperties.frozenRowCount,gridProperties.hideGridlines",
                }
            },
        ]
        if title == "Dashboard":
            requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4},
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": ACCENT_BG,
                                "textFormat": {"bold": True, "fontSize": 15, "foregroundColor": ACCENT_TEXT, "fontFamily": "Aptos"},
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }
                }
            )
            requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": 4},
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": SUMMARY_BG,
                                "textFormat": {"bold": True, "foregroundColor": ACCENT_TEXT, "fontFamily": "Aptos"},
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }
                }
            )
            for row_index in (4, 8, 12, 16, 20, 24, 28):
                requests.append(
                    {
                        "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": row_index, "endRowIndex": row_index + 1, "startColumnIndex": 0, "endColumnIndex": 7},
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": SECTION_BG,
                                "textFormat": {"bold": True, "foregroundColor": ACCENT_TEXT, "fontFamily": "Aptos"},
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                        }
                    }
                )
            requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": 8},
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": SUMMARY_BG,
                                "textFormat": {"bold": True, "foregroundColor": ACCENT_TEXT, "fontFamily": "Aptos", "fontSize": 11},
                                "horizontalAlignment": "CENTER",
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                    }
                }
            )
        return requests

    def _column_width_requests(self, sheet_id: int, title: str) -> Iterable[dict]:
        widths = SHEET_COLUMN_WIDTHS[title]
        for index, width in enumerate(widths):
            yield {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": index,
                        "endIndex": index + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }

    def _validation_requests(self, sheet_id: int, title: str) -> list[dict]:
        validation_map = {
            "Inbox": [(3, SUBJECTS), (5, INBOX_STATUSES)],
            "Tasks": [(2, SUBJECTS), (3, PRIORITIES), (5, TASK_STATUSES)],
            "Schedule": [(2, EVENT_TYPES), (3, SUBJECTS), (5, PRIORITIES), (7, SCHEDULE_STATUSES)],
            "Assessments": [(1, SUBJECTS)],
            "Patients": [(1, SUBJECTS)],
            "Materials": [(2, MATERIAL_CATEGORIES), (3, SUBJECTS), (4, PRIORITIES), (5, MATERIAL_STATUSES)],
            "Courses": [(0, SUBJECTS), (3, COURSE_STATUSES)],
        }
        requests = []
        for column_index, values in validation_map.get(title, []):
            requests.append(
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": 1000,
                            "startColumnIndex": column_index,
                            "endColumnIndex": column_index + 1,
                        },
                        "rule": {
                            "condition": {
                                "type": "ONE_OF_LIST",
                                "values": [{"userEnteredValue": value} for value in values],
                            },
                            "strict": False,
                            "showCustomUi": True,
                        },
                    }
                }
            )
        return requests

    def _number_format_requests(self, sheet_id: int, title: str) -> list[dict]:
        ranges = {
            "Inbox": [(0, "DATE_TIME", "yyyy-mm-dd hh:mm:ss"), (6, "DATE", "yyyy-mm-dd")],
            "Tasks": [(0, "DATE", "yyyy-mm-dd"), (4, "DATE", "yyyy-mm-dd")],
            "Schedule": [(0, "DATE", "yyyy-mm-dd"), (1, "TIME", "h:mm AM/PM"), (6, "DATE", "yyyy-mm-dd")],
            "Assessments": [(0, "DATE", "yyyy-mm-dd")],
            "Patients": [(0, "DATE", "yyyy-mm-dd"), (10, "DATE", "yyyy-mm-dd")],
            "Materials": [(0, "DATE", "yyyy-mm-dd")],
        }
        requests: list[dict] = []
        for column_index, number_type, pattern in ranges.get(title, []):
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": column_index,
                            "endColumnIndex": column_index + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {"type": number_type, "pattern": pattern}
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )
        return requests

    def _conditional_requests(self, sheet_id: int, title: str) -> list[dict]:
        if title not in {"Tasks", "Schedule", "Materials"}:
            return []
        status_column = {"Tasks": 5, "Schedule": 7, "Materials": 5}[title]
        priority_column = {"Tasks": 3, "Schedule": 5, "Materials": 4}[title]
        return [
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": status_column, "endColumnIndex": status_column + 1}],
                        "booleanRule": {
                            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Done"}]},
                            "format": {"backgroundColor": {"red": 0.9, "green": 0.96, "blue": 0.9}},
                        },
                    },
                    "index": 0,
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": status_column, "endColumnIndex": status_column + 1}],
                        "booleanRule": {
                            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Pending"}]},
                            "format": {"backgroundColor": {"red": 0.98, "green": 0.95, "blue": 0.86}},
                        },
                    },
                    "index": 0,
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": priority_column, "endColumnIndex": priority_column + 1}],
                        "booleanRule": {
                            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Urgent"}]},
                            "format": {"backgroundColor": {"red": 0.98, "green": 0.88, "blue": 0.88}},
                        },
                    },
                    "index": 0,
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": priority_column, "endColumnIndex": priority_column + 1}],
                        "booleanRule": {
                            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "High"}]},
                            "format": {"backgroundColor": {"red": 0.98, "green": 0.93, "blue": 0.86}},
                        },
                    },
                    "index": 0,
                }
            },
        ]
