from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from dental_os.config import AppConfig


PROMPT_TEMPLATE = """You are a parser for a personal dental study OS.
Convert one Telegram message into JSON with this exact schema:
{"items":[{"text":"short standalone action text","kind":"log|query|edit|delete|done|reset|chat"}]}

Rules:
- Split multi-action messages into separate items.
- Keep each item short and standalone.
- Preserve dental shorthand and names when useful.
- If the message is only a question, return one query item.
- If the message is a completion/update like "done", "bought", "case completed", use kind "done".
- If the message is a reset/clear command, use kind "reset".
- If unsure, still return the best split items.
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

    def split_message(self, text: str) -> list[dict]:
        if not self.enabled:
            return []
        if self.provider == "gemini":
            return self._split_with_gemini(text)
        return []

    def _split_with_gemini(self, text: str) -> list[dict]:
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
            with urllib.request.urlopen(request, timeout=25) as response:
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
        items = parsed.get("items", [])
        cleaned = []
        for item in items:
            item_text = str(item.get("text", "")).strip()
            item_kind = str(item.get("kind", "")).strip().lower()
            if item_text:
                cleaned.append({"text": item_text, "kind": item_kind or "log"})
        return cleaned
