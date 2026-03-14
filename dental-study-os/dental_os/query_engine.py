from __future__ import annotations

from datetime import datetime

from dental_os.date_utils import parse_range_hint
from dental_os.parser import DentalParser
from dental_os.services.sheets import SheetService


class QueryEngine:
    def __init__(self, sheets: SheetService, parser: DentalParser, timezone_name: str) -> None:
        self.sheets = sheets
        self.parser = parser
        self.timezone_name = timezone_name

    def answer(self, text: str) -> str:
        lower = text.lower()
        if "what's next" in lower or "whats next" in lower or lower == "next":
            return self._next_item()
        if "tomorrow" in lower or "today" in lower or "this week" in lower:
            return self._schedule_window(text)
        if "follow-up" in lower or "follow up" in lower:
            return self._pending_followups()
        if "patient" in lower and ("last week" in lower or "yesterday" in lower or "today" in lower):
            return self._patient_history(text)
        if "marks" in lower or "score" in lower:
            return self._marks(text)
        if "materials" in lower or "missing" in lower:
            return self._missing_materials()
        if "task" in lower or "open" in lower:
            return self._open_tasks()
        if "what did i do" in lower or "did i do" in lower:
            return self._activity_window(text)
        return self._schedule_window(text)

    def weekly_summary(self) -> str:
        sections = [
            "Friday summary",
            self._schedule_window("next 7 days"),
            self._open_tasks(),
            self._pending_followups(),
            self._patient_history("last week"),
            self._marks("recent marks"),
            self._missing_materials(),
        ]
        return "\n\n".join(sections)

    def _next_item(self) -> str:
        schedule = self.sheets.get_records("Schedule")
        today = datetime.now().date().isoformat()
        candidates = [row for row in schedule if row.get("Date") and row.get("Status") != "Done" and row.get("Date") >= today]
        candidates.sort(key=lambda row: (row.get("Date"), row.get("Time") or "23:59"))
        if not candidates:
            return "Nothing upcoming."
        row = candidates[0]
        time_text = row.get("Time", "").strip()
        return f"Next: {row['Event']} on {row['Date']} {time_text}.".strip()

    def _schedule_window(self, text: str) -> str:
        start, end = parse_range_hint(text, self.timezone_name)
        schedule = self.sheets.get_records("Schedule")
        rows = []
        for row in schedule:
            date_value = row.get("Date", "")
            if not date_value:
                continue
            try:
                date_obj = datetime.fromisoformat(date_value)
            except ValueError:
                continue
            if start <= date_obj < end and row.get("Status") != "Cancelled":
                rows.append(row)
        rows.sort(key=lambda row: (row.get("Date"), row.get("Time") or "23:59"))
        if not rows:
            return "No schedule items in that window."
        sample = [f"{row['Date']} {row.get('Time', '').strip()} {row.get('Event', '').strip()}".strip() for row in rows[:6]]
        return "Schedule:\n" + "\n".join(sample)

    def _open_tasks(self) -> str:
        tasks = self.sheets.get_records("Tasks")
        rows = [row for row in tasks if row.get("Task") and row.get("Status") != "Done"]
        rows.sort(key=lambda row: (row.get("Due_Date") or "9999-12-31", row.get("Priority") or "Medium"))
        if not rows:
            return "Open tasks: none."
        sample = [f"{row.get('Task')} [{row.get('Priority', 'Medium')}]" for row in rows[:6]]
        return "Open tasks:\n" + "\n".join(sample)

    def _pending_followups(self) -> str:
        patients = self.sheets.get_records("Patients")
        rows = [row for row in patients if row.get("Case_ID") and row.get("Follow_Up_Date")]
        rows.sort(key=lambda row: row.get("Follow_Up_Date") or "9999-12-31")
        if not rows:
            return "Pending follow-ups: none."
        sample = [f"{row.get('Follow_Up_Date')} {row.get('Case_ID')} {row.get('Patient_Name')} {row.get('Next_Step')}".strip() for row in rows[:6]]
        return "Pending follow-ups:\n" + "\n".join(sample)

    def _patient_history(self, text: str) -> str:
        start, end = parse_range_hint(text, self.timezone_name)
        patients = self.sheets.get_records("Patients")
        rows = []
        for row in patients:
            date_value = row.get("Date")
            if not date_value:
                continue
            try:
                date_obj = datetime.fromisoformat(date_value)
            except ValueError:
                continue
            if start <= date_obj < end:
                rows.append(row)
        rows.sort(key=lambda row: row.get("Date"), reverse=True)
        if not rows:
            return "No patient sessions in that window."
        sample = [f"{row.get('Date')} {row.get('Case_ID')} {row.get('Patient_Name')} {row.get('Procedure')}" for row in rows[:6]]
        return "Patient sessions:\n" + "\n".join(sample)

    def _marks(self, text: str) -> str:
        subject = self.parser._normalize_subject(text)
        assessments = self.sheets.get_records("Assessments")
        rows = [row for row in assessments if row.get("Subject") and (not subject or row.get("Subject") == subject)]
        rows.sort(key=lambda row: row.get("Date") or "", reverse=True)
        if not rows:
            return "No marks found."
        sample = [f"{row.get('Date')} {row.get('Subject')} {row.get('Score')}/{row.get('Total')} ({row.get('Percentage')})" for row in rows[:6]]
        return "Marks:\n" + "\n".join(sample)

    def _missing_materials(self) -> str:
        materials = self.sheets.get_records("Materials")
        done_statuses = {"Bought", "Done", "Received"}
        rows = [row for row in materials if row.get("Item") and row.get("Status") not in done_statuses]
        if not rows:
            return "Materials pending: none."
        sample = [f"{row.get('Item')} [{row.get('Status', 'Pending')}]" for row in rows[:6]]
        return "Materials pending:\n" + "\n".join(sample)

    def _activity_window(self, text: str) -> str:
        start, end = parse_range_hint(text, self.timezone_name)
        lines = []
        for sheet_name, label, field in (
            ("Tasks", "Tasks", "Task"),
            ("Schedule", "Schedule", "Event"),
            ("Patients", "Patients", "Procedure"),
            ("Assessments", "Assessments", "Percentage"),
        ):
            rows = []
            for row in self.sheets.get_records(sheet_name):
                date_value = row.get("Date") or row.get("Created_Date")
                if not date_value:
                    continue
                try:
                    date_obj = datetime.fromisoformat(date_value)
                except ValueError:
                    continue
                if start <= date_obj < end:
                    rows.append(row)
            if rows:
                lines.append(f"{label}: " + ", ".join(row.get(field, "") for row in rows[:5] if row.get(field)))
        return "\n".join(lines) if lines else "Nothing logged in that window."
