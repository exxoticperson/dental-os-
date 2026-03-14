from __future__ import annotations

from datetime import datetime
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

    def initialize(self) -> None:
        if self._initialized:
            return
        for title in SHEET_ORDER:
            worksheet = self._get_or_create_worksheet(title)
            self._ensure_headers(worksheet, SHEET_HEADERS[title])
        self._ensure_settings_seeded()
        self._setup_dashboard()
        self._apply_formatting()
        self._initialized = True

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

    def delete_row(self, sheet_name: str, row_number: int) -> None:
        self.initialize()
        worksheet = self.spreadsheet.worksheet(sheet_name)
        if row_number > 1:
            worksheet.delete_rows(row_number)

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
            ["Dental Study OS", "Live"],
            ["Open Tasks", '=COUNTA(FILTER(Tasks!B2:B,Tasks!B2:B<>"",Tasks!F2:F<>"Done"))', "Upcoming 7 Days", '=COUNTA(FILTER(Schedule!A2:A,Schedule!A2:A<>"",Schedule!A2:A>=TODAY(),Schedule!A2:A<=TODAY()+7,Schedule!H2:H<>"Cancelled"))'],
            ["Pending Follow-Ups", '=COUNTA(FILTER(Patients!C2:C,Patients!C2:C<>"",Patients!K2:K<>""))', "Pending Materials", '=COUNTA(FILTER(Materials!B2:B,Materials!B2:B<>"",Materials!F2:F<>"Done",Materials!F2:F<>"Bought"))'],
            [],
            ["Next 7 Days"],
            ["Date", "Time", "Type", "Subject", "Event", "Priority", "Status"],
            ['=IFERROR(SORT(FILTER({Schedule!A2:A,Schedule!B2:B,Schedule!C2:C,Schedule!D2:D,Schedule!E2:E,Schedule!F2:F,Schedule!H2:H},Schedule!A2:A<>"",Schedule!A2:A>=TODAY(),Schedule!A2:A<=TODAY()+7),1,TRUE,2,TRUE),"")'],
            [],
            ["Open Tasks"],
            ["Created", "Task", "Subject", "Priority", "Due", "Status"],
            ['=IFERROR(SORT(FILTER({Tasks!A2:A,Tasks!B2:B,Tasks!C2:C,Tasks!D2:D,Tasks!E2:E,Tasks!F2:F},Tasks!B2:B<>"",Tasks!F2:F<>"Done"),4,FALSE,5,TRUE),"")'],
            [],
            ["Pending Follow-Ups"],
            ["Date", "Subject", "Case_ID", "Patient", "Next_Step", "Follow_Up_Date"],
            ['=IFERROR(SORT(FILTER({Patients!A2:A,Patients!B2:B,Patients!C2:C,Patients!D2:D,Patients!J2:J,Patients!K2:K},Patients!C2:C<>"",Patients!K2:K<>""),6,TRUE),"")'],
            [],
            ["Recent Patient Sessions"],
            ["Date", "Subject", "Case_ID", "Patient", "Procedure"],
            ['=IFERROR(SORT(FILTER({Patients!A2:A,Patients!B2:B,Patients!C2:C,Patients!D2:D,Patients!F2:F},Patients!C2:C<>""),1,FALSE),"")'],
            [],
            ["Recent Marks"],
            ["Date", "Subject", "Type", "Score", "Total", "Percentage"],
            ['=IFERROR(SORT(FILTER({Assessments!A2:A,Assessments!B2:B,Assessments!C2:C,Assessments!D2:D,Assessments!E2:E,Assessments!F2:F},Assessments!B2:B<>""),1,FALSE),"")'],
            [],
            ["Pending Materials"],
            ["Date", "Item", "Category", "Subject", "Priority", "Status"],
            ['=IFERROR(SORT(FILTER({Materials!A2:A,Materials!B2:B,Materials!C2:C,Materials!D2:D,Materials!E2:E,Materials!F2:F},Materials!B2:B<>"",Materials!F2:F<>"Done",Materials!F2:F<>"Bought"),1,FALSE),"")'],
        ]
        worksheet.update("A1", layout, value_input_option="USER_ENTERED")

    def _apply_formatting(self) -> None:
        requests: list[dict] = []
        for title in SHEET_ORDER:
            worksheet = self.spreadsheet.worksheet(title)
            sheet_id = worksheet.id
            requests.extend(self._sheet_style_requests(sheet_id, title, worksheet.row_count, worksheet.col_count))
            requests.extend(self._column_width_requests(sheet_id, title))
            requests.extend(self._validation_requests(sheet_id, title))
            requests.extend(self._conditional_requests(sheet_id, title))
        self.spreadsheet.batch_update({"requests": requests})

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
                    "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "addBanding": {
                    "bandedRange": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": 0, "endColumnIndex": col_count},
                        "rowProperties": {"firstBandColor": WHITE_BG, "secondBandColor": STRIPE_BG},
                    }
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
            for row_index in (4, 8, 12, 16, 20):
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
