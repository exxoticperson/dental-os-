from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from dental_os.config import AppConfig
from dental_os.models import StructuredAction


PROMPT_TEMPLATE = """You are a parser for a personal dental study OS Telegram bot.
Convert one message into JSON with this exact schema:
{
  "actions": [
    {
      "kind": "query|edit|delete|done|reset|chat|log",
      "route": "schedule|tasks|assessments|patients|materials|study_progress|courses|inbox|query",
      "text": "standalone cleaned text",
      "subject": "",
      "date": "YYYY-MM-DD or empty",
      "time": "HH:MM or empty",
      "event_type": "",
      "event": "",
      "task": "",
      "assessment_type": "",
      "score": "",
      "total": "",
      "percentage": "",
      "case_id": "",
      "patient_name": "",
      "phone_number": "",
      "procedure": "",
      "tooth_or_area": "",
      "next_step": "",
      "follow_up_date": "YYYY-MM-DD or empty",
      "item": "",
      "category": "",
      "priority": "",
      "status": "",
      "notes": ""
    }
  ]
}

Rules:
- Split multi-action messages into multiple actions.
- Preserve patient names, teeth, areas, and follow-up details.
- If the user mentions multiple patient procedures, teeth, or treated areas in one sentence, create separate patient actions when they are distinct because Patients stores one row per procedure/session.
- Future quiz/exam/clinic/deadline mentions must be schedule actions, not done actions.
- Finished procedures or completed cases must be patient log actions with the clinical details preserved.
- Follow-up or revisit mentions tied to a patient should stay attached to that patient action when possible.
- Keep timing meaning exact. Understand phrases like tomorrow, day after tomorrow, in 3 days, in a week, next Tuesday, this Friday, and explicit dates.
- Do not invent absolute dates for relative timing. If the user says tomorrow, next week, in 3 days, this Friday, or similar, keep that phrasing inside text and leave date/follow_up_date empty unless the message contains an explicit calendar date.
- If the user asks a question, return a query action instead of a log.
- If the user is editing/deleting/marking done/resetting, reflect that in kind and route.
- Use exact subjects when inferable:
  Restorative, Fixed Prosthodontics, Removable Prosthodontics, Endodontics, Periodontics, Oral Medicine, Exodontia / Oral Surgery, Orthodontics, General Surgery
- Use empty strings for unknown fields.
- Output JSON only.
"""


class LLMParser:
    def __init__(self, config: AppConfig) -> None:
        self.provider = (config.llm_provider or "").lower()
        self.model = config.llm_model
        self.api_key = config.gemini_api_key

    @property
    def enabled(self) -> bool:
        return self.provider == "gemini" and bool(self.api_key)

    def extract_actions(self, text: str) -> list[StructuredAction]:
        if not self.enabled:
            return []
        if self.provider == "gemini":
            return self._extract_with_gemini(text)
        return []

    def split_message(self, text: str) -> list[dict]:
        actions = self.extract_actions(text)
        return [{"text": action.text or text, "kind": action.kind or "log"} for action in actions]

    def _extract_with_gemini(self, text: str) -> list[StructuredAction]:
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{urllib.parse.quote(self.model)}:generateContent?key={urllib.parse.quote(self.api_key or '')}"
        )
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": PROMPT_TEMPLATE},
                        {"text": text},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return []
        candidates = payload.get("candidates", [])
        if not candidates:
            return []
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return []
        raw_text = parts[0].get("text", "").strip()
        if not raw_text:
            return []
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            return []
        actions = []
        for item in parsed.get("actions", []):
            if not isinstance(item, dict):
                continue
            text_value = str(item.get("text", "")).strip()
            kind_value = str(item.get("kind", "log")).strip().lower()
            if not text_value and kind_value != "reset":
                continue
            actions.append(
                StructuredAction(
                    kind=kind_value or "log",
                    text=text_value,
                    route=str(item.get("route", "")).strip().lower(),
                    subject=str(item.get("subject", "")).strip(),
                    date=str(item.get("date", "")).strip(),
                    time=str(item.get("time", "")).strip(),
                    event_type=str(item.get("event_type", "")).strip(),
                    event=str(item.get("event", "")).strip(),
                    task=str(item.get("task", "")).strip(),
                    assessment_type=str(item.get("assessment_type", "")).strip(),
                    score=str(item.get("score", "")).strip(),
                    total=str(item.get("total", "")).strip(),
                    percentage=str(item.get("percentage", "")).strip(),
                    case_id=str(item.get("case_id", "")).strip(),
                    patient_name=str(item.get("patient_name", "")).strip(),
                    phone_number=str(item.get("phone_number", "")).strip(),
                    procedure=str(item.get("procedure", "")).strip(),
                    tooth_or_area=str(item.get("tooth_or_area", "")).strip(),
                    next_step=str(item.get("next_step", "")).strip(),
                    follow_up_date=str(item.get("follow_up_date", "")).strip(),
                    item=str(item.get("item", "")).strip(),
                    category=str(item.get("category", "")).strip(),
                    priority=str(item.get("priority", "")).strip(),
                    status=str(item.get("status", "")).strip(),
                    notes=str(item.get("notes", "")).strip(),
                )
            )
        return actions
