from __future__ import annotations

import re

from dental_os.constants import (
    MATERIAL_KEYWORDS,
    PRIORITY_KEYWORDS,
    QUESTION_PREFIXES,
    SCHEDULE_KEYWORDS,
    SUBJECT_ALIASES,
    TASK_DONE_KEYWORDS,
)
from dental_os.date_utils import extract_datetime, extract_follow_up_date, extract_time_only, format_date, parse_recurring_rule
from dental_os.models import ParsedIntent


SCORE_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*/\s*(\d{1,3}(?:\.\d+)?)\b")
CASE_RE = re.compile(r"\b([A-Za-z]\d{2,})\b")
PHONE_RE = re.compile(r"(\+?\d[\d\s-]{7,}\d)")
TOOTH_RE = re.compile(r"\b(?:tooth|teeth|ul|ur|ll|lr|#)\s*[A-Za-z0-9-]+\b", re.IGNORECASE)
COUNT_RE = re.compile(r"\b(\d{1,3})\b")


class DentalParser:
    def __init__(self, timezone_name: str) -> None:
        self.timezone_name = timezone_name
        self._sorted_aliases = sorted(SUBJECT_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)

    def parse(self, text: str, is_photo: bool = False) -> ParsedIntent:
        normalized = " ".join((text or "").strip().split())
        if not normalized:
            return ParsedIntent(route="inbox", confidence=0.2, raw_text=text, parsed_type="Inbox", status="New")
        if self._is_query(normalized):
            return ParsedIntent(route="query", confidence=0.98, raw_text=normalized, query_hint=normalized)
        if self._looks_like_study_progress(normalized):
            return self._parse_study_progress(normalized)
        if self._looks_like_task_done(normalized):
            task_name = re.sub(r"^(done|completed|finished|mark done)\s+", "", normalized, flags=re.IGNORECASE).strip()
            return ParsedIntent(route="task_done", confidence=0.95, raw_text=normalized, task=task_name)
        if is_photo or self._looks_like_patient(normalized):
            return self._parse_patient(normalized, is_photo=is_photo)
        if self._looks_like_assessment(normalized):
            return self._parse_assessment(normalized)
        if self._looks_like_material(normalized):
            return self._parse_material(normalized)
        if self._looks_like_schedule(normalized):
            return self._parse_schedule(normalized)
        if self._looks_like_course(normalized):
            return self._parse_course(normalized)
        if self._looks_like_task(normalized):
            return self._parse_task(normalized)
        return ParsedIntent(route="inbox", confidence=0.45, raw_text=normalized, parsed_type="Inbox", status="New", notes="Low confidence fallback.")

    def _normalize_subject(self, text: str) -> str:
        lower = text.lower()
        for alias, subject in self._sorted_aliases:
            if re.search(rf"\b{re.escape(alias)}\b", lower):
                return subject
        return ""

    def _infer_subject_from_clinical_terms(self, text: str) -> str:
        lower = text.lower()
        if any(term in lower for term in ("crown", "bridge", "duralay", "prep")):
            return "Fixed Prosthodontics"
        if any(term in lower for term in ("denture", "rpd", "flange", "record block")):
            return "Removable Prosthodontics"
        if any(term in lower for term in ("endo", "rct", "root canal", "obturation", "access cavity")):
            return "Endodontics"
        if any(term in lower for term in ("perio", "scaling", "curettage", "periodontal")):
            return "Periodontics"
        if any(term in lower for term in ("extraction", "forceps", "surgical", "impaction", "anesthesia")):
            return "Exodontia / Oral Surgery"
        if any(term in lower for term in ("lesion", "ulcer", "oral medicine")):
            return "Oral Medicine"
        if any(term in lower for term in ("ortho", "bracket", "wire")):
            return "Orthodontics"
        if any(term in lower for term in ("restoration", "composite", "class ii", "filling")):
            return "Restorative"
        return ""

    def _extract_priority(self, text: str) -> str:
        lower = text.lower()
        for keyword, priority in PRIORITY_KEYWORDS.items():
            if keyword in lower:
                return priority
        return "Medium"

    def _is_query(self, text: str) -> bool:
        lower = text.lower()
        progress_question = any(phrase in lower for phrase in ("how much", "how many lectures", "what am i missing", "what lectures", "how much left", "what's left", "whats left"))
        return lower.startswith(QUESTION_PREFIXES) or lower.endswith("?") or progress_question

    def _looks_like_task_done(self, text: str) -> bool:
        lower = text.lower()
        return any(lower.startswith(keyword) for keyword in TASK_DONE_KEYWORDS)

    def _looks_like_patient(self, text: str) -> bool:
        lower = text.lower()
        return any(token in lower for token in ("patient", "case", "follow up", "follow-up", "crown prep", "extraction", "rct", "session")) or bool(CASE_RE.search(text))

    def _looks_like_assessment(self, text: str) -> bool:
        lower = text.lower()
        return (bool(SCORE_RE.search(text)) and any(token in lower for token in ("quiz", "exam", "practical", "discussion", "assessment", "mark"))) or bool(SCORE_RE.search(text) and self._normalize_subject(text))

    def _looks_like_material(self, text: str) -> bool:
        lower = text.lower()
        return any(keyword in lower for keyword in MATERIAL_KEYWORDS)

    def _looks_like_schedule(self, text: str) -> bool:
        lower = text.lower()
        if any(keyword in lower for keyword in ("assignment", "study", "revise", "finish", "complete", "submit")) and not any(keyword in lower for keyword in ("quiz", "exam", "clinic", "discussion", "deadline")):
            return False
        parsed_dt, phrase = extract_datetime(text, self.timezone_name)
        has_date = bool(parsed_dt and phrase and len(phrase.strip()) <= len(text))
        return has_date or any(keyword in lower for keyword in SCHEDULE_KEYWORDS)

    def _looks_like_task(self, text: str) -> bool:
        lower = text.lower()
        starters = ("study", "revise", "finish", "submit", "call", "check", "prepare", "review", "complete", "send", "do ")
        return lower.startswith(starters) or "assignment" in lower

    def _looks_like_course(self, text: str) -> bool:
        lower = text.lower()
        return any(keyword in lower for keyword in ("lecture", "topic", "lec ", "chapter")) and not self._looks_like_schedule(text)

    def _looks_like_study_progress(self, text: str) -> bool:
        lower = text.lower()
        has_subject = bool(self._normalize_subject(text))
        progress_words = any(phrase in lower for phrase in ("lectures", "lecture", "finished", "studied", "done", "left", "remaining", "covered", "completed"))
        count_words = len(COUNT_RE.findall(text)) >= 1
        return has_subject and progress_words and count_words and "quiz" not in lower and "exam" not in lower

    def _parse_assessment(self, text: str) -> ParsedIntent:
        match = SCORE_RE.search(text)
        score = match.group(1) if match else ""
        total = match.group(2) if match else ""
        percentage = ""
        if score and total:
            percentage = f"{(float(score) / float(total)) * 100:.0f}%"
        lower = text.lower()
        assessment_type = "Quiz" if "quiz" in lower else "Exam" if "exam" in lower else "Practical" if "practical" in lower else "Discussion" if "discussion" in lower else "Other"
        subject = self._normalize_subject(text)
        parsed_dt, _ = extract_datetime(text, self.timezone_name, prefer_future=False)
        return ParsedIntent(route="assessments", confidence=0.96, raw_text=text, parsed_type="Assessment", subject=subject, date=format_date(parsed_dt), assessment_type=assessment_type, score=score, total=total, percentage=percentage, notes=text)

    def _parse_material(self, text: str) -> ParsedIntent:
        lower = text.lower()
        item = re.sub(r"^(buy|get|order)\s+", "", text, flags=re.IGNORECASE).strip()
        category = "Tool" if "tool" in lower or "lens" in lower or "bur" in lower else "Sterilization" if "steril" in lower else "Other"
        store = ""
        floor_match = re.search(r"\bfloor\s+\d+\b", lower)
        if floor_match:
            store = floor_match.group(0).title()
        return ParsedIntent(route="materials", confidence=0.92, raw_text=text, parsed_type="Material", subject=self._normalize_subject(text), item=item[:220], category=category, priority=self._extract_priority(text), status="Pending", store_or_source=store, notes=text)

    def _parse_schedule(self, text: str) -> ParsedIntent:
        lower = text.lower()
        parsed_dt, phrase = extract_datetime(text, self.timezone_name)
        event_type = "Reminder"
        for candidate in ("quiz", "exam", "deadline", "clinic", "discussion", "reminder"):
            if candidate in lower:
                event_type = "Reminder" if candidate == "reminder" else candidate.title()
                break
        if "remind me" in lower or lower.startswith("remind"):
            event_type = "Reminder"
        event = text
        if phrase:
            event = re.sub(re.escape(phrase), "", event, flags=re.IGNORECASE).strip(" ,.-")
        event = re.sub(r"^remind me to\s+", "", event, flags=re.IGNORECASE).strip()
        event = event or text
        recurring = parse_recurring_rule(text)
        reminder_date = format_date(parsed_dt) if parsed_dt else ""
        time_value = extract_time_only(text) or (parsed_dt.strftime("%H:%M") if parsed_dt and (parsed_dt.hour or parsed_dt.minute) else "")
        if not parsed_dt and event_type == "Reminder":
            return ParsedIntent(route="schedule", confidence=0.56, raw_text=text, parsed_type="Schedule", requires_follow_up=True, follow_up_question="When should I remind you?")
        return ParsedIntent(route="schedule", confidence=0.9 if parsed_dt else 0.72, raw_text=text, parsed_type="Schedule", subject=self._normalize_subject(text), date=format_date(parsed_dt), time=time_value, event=event[:250], priority=self._extract_priority(text), follow_up_date=reminder_date, recurring=recurring, status="Scheduled", notes=text, metadata={"event_type": event_type})

    def _parse_task(self, text: str) -> ParsedIntent:
        parsed_dt, phrase = extract_datetime(text, self.timezone_name)
        task = text
        if phrase:
            task = re.sub(re.escape(phrase), "", task, flags=re.IGNORECASE).strip(" ,.-")
        return ParsedIntent(route="tasks", confidence=0.86, raw_text=text, parsed_type="Task", subject=self._normalize_subject(text), task=task[:250], priority=self._extract_priority(text), date=format_date(parsed_dt), recurring=parse_recurring_rule(text), status="Open", notes=text)

    def _parse_course(self, text: str) -> ParsedIntent:
        return ParsedIntent(route="courses", confidence=0.74, raw_text=text, subject=self._normalize_subject(text), course_topic=text[:250], course_category="Lecture", status="Active", notes=text)

    def _parse_study_progress(self, text: str) -> ParsedIntent:
        lower = text.lower()
        numbers = [match.group(1) for match in COUNT_RE.finditer(text)]
        subject = self._normalize_subject(text)
        total_count = ""
        completed_count = ""
        if any(phrase in lower for phrase in ("have", "has", "total", "we took", "we have", "there are")) and numbers:
            total_count = numbers[0]
        if any(phrase in lower for phrase in ("finished", "studied", "done", "completed", "covered")) and numbers:
            completed_count = numbers[-1]
        if "haven't studied" in lower or "havent studied" in lower or "didn't study" in lower:
            completed_count = "0"
        if not total_count and len(numbers) >= 2:
            total_count = numbers[0]
            completed_count = numbers[1]
        notes = text
        return ParsedIntent(
            route="study_progress",
            confidence=0.88,
            raw_text=text,
            parsed_type="Study_Progress",
            subject=subject,
            course_topic="Lecture Progress",
            course_category="Progress",
            status="Active",
            total_count=total_count,
            completed_count=completed_count,
            notes=notes,
        )

    def _parse_patient(self, text: str, is_photo: bool = False) -> ParsedIntent:
        subject = self._normalize_subject(text) or self._infer_subject_from_clinical_terms(text)
        case_match = CASE_RE.search(text)
        case_id = case_match.group(1).upper() if case_match else ""
        phone_match = PHONE_RE.search(text)
        phone = phone_match.group(1).strip() if phone_match else ""
        tooth_match = TOOTH_RE.search(text)
        tooth = tooth_match.group(0) if tooth_match else ""
        name = ""
        if case_id:
            after_case = text.split(case_id, 1)[1].strip()
            name_tokens = []
            for token in after_case.split():
                if token.lower() in {"fixed", "removable", "endo", "resto", "restorative", "perio", "patient"}:
                    break
                if PHONE_RE.search(token):
                    break
                name_tokens.append(token)
                if len(name_tokens) >= 2:
                    break
            name = " ".join(name_tokens).strip(",.-")
        procedure = text
        for fragment in filter(None, [case_id, name, phone]):
            procedure = re.sub(re.escape(fragment), "", procedure, flags=re.IGNORECASE).strip(" ,.-")
        procedure = re.sub(r"^patient\s+", "", procedure, flags=re.IGNORECASE).strip()
        follow_up_date = extract_follow_up_date(text, self.timezone_name)
        next_step = text if "next" in text.lower() else ""
        if not case_id or not name:
            return ParsedIntent(route="patients", confidence=0.58, raw_text=text, parsed_type="Patient", requires_follow_up=True, follow_up_question="Case ID and patient name?", subject=subject, case_id=case_id, patient_name=name, phone_number=phone, procedure=procedure)
        return ParsedIntent(route="patients", confidence=0.91, raw_text=text, parsed_type="Patient", subject=subject, case_id=case_id, linked_case_id=case_id, patient_name=name, phone_number=phone, procedure=procedure[:180], tooth_or_area=tooth, session_notes=text, next_step=next_step[:180], follow_up_date=follow_up_date, status="Logged")
