from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedIntent:
    route: str
    confidence: float
    raw_text: str
    subject: str = ""
    notes: str = ""
    status: str = ""
    priority: str = "Medium"
    date: str = ""
    time: str = ""
    follow_up_date: str = ""
    recurring: str = ""
    parsed_type: str = ""
    linked_case_id: str = ""
    task: str = ""
    event: str = ""
    assessment_type: str = ""
    score: str = ""
    total: str = ""
    percentage: str = ""
    case_id: str = ""
    patient_name: str = ""
    phone_number: str = ""
    procedure: str = ""
    tooth_or_area: str = ""
    supervisor: str = ""
    session_notes: str = ""
    next_step: str = ""
    photo_links: str = ""
    item: str = ""
    category: str = ""
    store_or_source: str = ""
    course_topic: str = ""
    course_category: str = ""
    query_hint: str = ""
    requires_follow_up: bool = False
    follow_up_question: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingClarification:
    original_text: str
    route: str
    question: str
    attempts: int = 1
    photo_file_id: str = ""
