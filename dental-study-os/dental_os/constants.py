from __future__ import annotations

SUBJECTS = [
    "Restorative",
    "Fixed Prosthodontics",
    "Removable Prosthodontics",
    "Endodontics",
    "Periodontics",
    "Oral Medicine",
    "Exodontia / Oral Surgery",
    "Orthodontics",
    "General Surgery",
]

SUBJECT_ALIASES = {
    "restorative": "Restorative",
    "resto": "Restorative",
    "fixed prosthodontics": "Fixed Prosthodontics",
    "fixed prostho": "Fixed Prosthodontics",
    "fixed": "Fixed Prosthodontics",
    "fpd": "Fixed Prosthodontics",
    "removable prosthodontics": "Removable Prosthodontics",
    "removable prostho": "Removable Prosthodontics",
    "removable": "Removable Prosthodontics",
    "rpd": "Removable Prosthodontics",
    "endo": "Endodontics",
    "endodontics": "Endodontics",
    "perio": "Periodontics",
    "periodontics": "Periodontics",
    "oral medicine": "Oral Medicine",
    "oral med": "Oral Medicine",
    "exodontia": "Exodontia / Oral Surgery",
    "oral surgery": "Exodontia / Oral Surgery",
    "oral surg": "Exodontia / Oral Surgery",
    "exo": "Exodontia / Oral Surgery",
    "local anesthesia": "Exodontia / Oral Surgery",
    "la": "Exodontia / Oral Surgery",
    "orthodontics": "Orthodontics",
    "ortho": "Orthodontics",
    "general surgery": "General Surgery",
    "gen surgery": "General Surgery",
    "gen surg": "General Surgery",
}

TASK_STATUSES = ["Open", "In Progress", "Done", "Cancelled"]
SCHEDULE_STATUSES = ["Scheduled", "Reminded", "Done", "Cancelled"]
INBOX_STATUSES = ["New", "Needs Follow-Up", "Parsed", "Archived"]
MATERIAL_STATUSES = ["Pending", "Bought", "Received", "Done", "Cancelled"]
COURSE_STATUSES = ["Not Started", "Active", "Done", "Archived"]
PRIORITIES = ["Low", "Medium", "High", "Urgent"]
EVENT_TYPES = ["Quiz", "Exam", "Deadline", "Clinic", "Discussion", "Reminder"]
MATERIAL_CATEGORIES = ["Tool", "Consumable", "Device", "Sterilization", "Other"]

SHEET_HEADERS = {
    "Dashboard": ["Section", "Value", "Detail_1", "Detail_2", "Detail_3", "Detail_4", "Detail_5", "Detail_6"],
    "Inbox": ["Timestamp", "Raw_Text", "Parsed_Type", "Subject", "Linked_Case_ID", "Status", "Follow_Up_Date", "Notes"],
    "Tasks": ["Created_Date", "Task", "Subject", "Priority", "Due_Date", "Status", "Recurring", "Notes"],
    "Schedule": ["Date", "Time", "Type", "Subject", "Event", "Priority", "Reminder_Date", "Status", "Notes"],
    "Assessments": ["Date", "Subject", "Assessment_Type", "Score", "Total", "Percentage", "Notes"],
    "Patients": ["Date", "Subject", "Case_ID", "Patient_Name", "Phone_Number", "Procedure", "Tooth_or_Area", "Supervisor", "Session_Notes", "Next_Step", "Follow_Up_Date", "Photo_Links"],
    "Materials": ["Date_Added", "Item", "Category", "Subject", "Priority", "Status", "Store_or_Source", "Notes"],
    "Courses": ["Subject", "Lecture_or_Topic", "Category", "Status", "Notes"],
    "Settings": ["Category", "Key", "Value", "Notes"],
}

SHEET_ORDER = [
    "Dashboard",
    "Inbox",
    "Tasks",
    "Schedule",
    "Assessments",
    "Patients",
    "Materials",
    "Courses",
    "Settings",
]

DEFAULT_SETTINGS_ROWS = [
    ("subjects", "allowed_subjects", " | ".join(SUBJECTS), "Normalized subject list used by the bot."),
    ("statuses", "task_statuses", " | ".join(TASK_STATUSES), "Task status dropdown."),
    ("statuses", "schedule_statuses", " | ".join(SCHEDULE_STATUSES), "Schedule status dropdown."),
    ("statuses", "material_statuses", " | ".join(MATERIAL_STATUSES), "Materials status dropdown."),
    ("statuses", "course_statuses", " | ".join(COURSE_STATUSES), "Courses status dropdown."),
    ("statuses", "inbox_statuses", " | ".join(INBOX_STATUSES), "Inbox status dropdown."),
    ("priorities", "priority_levels", " | ".join(PRIORITIES), "Priority dropdown."),
    ("event_types", "schedule_event_types", " | ".join(EVENT_TYPES), "Schedule type dropdown."),
    ("folder_naming_rules", "case_folder", "CaseID_PatientName", "Patient image folder naming rule."),
    ("constants", "default_timezone", "Africa/Cairo", "Default bot timezone."),
    ("constants", "weekly_summary_day", "Friday", "Weekly summary day."),
]

DEFAULT_SHEET_ROWS = {
    "Inbox": 1000,
    "Tasks": 1000,
    "Schedule": 1000,
    "Assessments": 1000,
    "Patients": 1000,
    "Materials": 1000,
    "Courses": 500,
    "Settings": 100,
    "Dashboard": 60,
}

DEFAULT_SHEET_COLS = {
    "Dashboard": 12,
    "Inbox": 8,
    "Tasks": 8,
    "Schedule": 9,
    "Assessments": 7,
    "Patients": 12,
    "Materials": 8,
    "Courses": 5,
    "Settings": 4,
}

SHEET_COLUMN_WIDTHS = {
    "Dashboard": [180, 150, 160, 160, 160, 160, 160, 160],
    "Inbox": [145, 380, 140, 180, 130, 130, 135, 240],
    "Tasks": [120, 280, 180, 100, 115, 110, 120, 220],
    "Schedule": [110, 90, 120, 180, 260, 100, 130, 110, 220],
    "Assessments": [110, 180, 150, 90, 90, 100, 240],
    "Patients": [110, 180, 120, 180, 135, 180, 140, 140, 240, 180, 125, 220],
    "Materials": [110, 220, 130, 180, 100, 110, 140, 220],
    "Courses": [180, 260, 130, 120, 220],
    "Settings": [140, 200, 320, 240],
}

NEUTRAL_HEADER_BG = {"red": 0.92, "green": 0.93, "blue": 0.94}
NEUTRAL_BODY_BG = {"red": 0.98, "green": 0.98, "blue": 0.98}
ACCENT_BG = {"red": 0.88, "green": 0.92, "blue": 0.96}
ACCENT_TEXT = {"red": 0.11, "green": 0.18, "blue": 0.26}
STRIPE_BG = {"red": 0.965, "green": 0.972, "blue": 0.978}
SECTION_BG = {"red": 0.93, "green": 0.95, "blue": 0.97}
SUMMARY_BG = {"red": 0.95, "green": 0.96, "blue": 0.97}
WHITE_BG = {"red": 1.0, "green": 1.0, "blue": 1.0}

PRIORITY_KEYWORDS = {
    "urgent": "Urgent",
    "asap": "Urgent",
    "high": "High",
    "important": "High",
    "low": "Low",
}

SCHEDULE_KEYWORDS = ("quiz", "exam", "deadline", "clinic", "discussion", "remind", "reminder")
MATERIAL_KEYWORDS = ("buy", "purchase", "order", "tool", "tools", "material", "materials", "lens", "bur", "wire", "glove", "gown")
TASK_DONE_KEYWORDS = ("done", "completed", "finished", "mark done")
QUESTION_PREFIXES = ("what", "what's", "whats", "show", "list", "which", "when", "do i have", "did i", "next")
