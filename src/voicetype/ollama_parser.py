"""Dolphin Llama 3 via Ollama: DICTATION vs COMMAND classification."""
from __future__ import annotations

import json
import re

import requests

SYSTEM_PROMPT = """You are VoiceType's command parser. Classify the user's speech as DICTATION or COMMAND.

Rules:
- DICTATION: normal text to type. Output the cleaned text (fix punctuation/casing, keep meaning).
- COMMAND: an editing/OS instruction (delete, new paragraph, switch app, scroll, undo, copy, paste, etc.).
  Output exactly one line of JSON: {"action": "<action>", "target": "<target>", "value": "<value>"}.

Valid actions: delete, new_paragraph, switch_app, scroll, undo, redo, copy, paste,
select_all, press_key, open_app, type_text.
Targets depend on action, e.g. delete -> word|line|selection|last_sentence,
scroll -> up|down, switch_app/open_app -> app name, press_key -> key name.
Value holds amounts/app names/directions, e.g. {"action":"scroll","target":"down","value":"3"}.

Format:
- If dictation, reply: DICTATION: <cleaned text>
- If command, reply: COMMAND: {"action": ..., "target": ..., "value": ...}
- Output nothing else. Never explain.
"""

COMMAND_RE = re.compile(r"COMMAND\s*:\s*(\{.*\})", re.DOTALL | re.IGNORECASE)
DICTATION_RE = re.compile(r"DICTATION\s*:\s*(.*)", re.DOTALL | re.IGNORECASE)


def build_prompt(transcript: str) -> str:
    return f"{SYSTEM_PROMPT}\nUser speech: {transcript.strip()}\n"


def parse_llm_output(raw: str) -> dict:
    """Parse model output into {'kind': 'dictation'|'command', ...}. Never raises."""
    text = (raw or "").strip()
    m = COMMAND_RE.search(text)
    if m:
        try:
            payload = json.loads(m.group(1))
            if isinstance(payload, dict) and str(payload.get("action", "")).strip():
                return {
                    "kind": "command",
                    "action": str(payload.get("action", "")).strip(),
                    "target": str(payload.get("target", "")).strip(),
                    "value": str(payload.get("value", "")).strip(),
                    "raw": raw,
                }
            # Valid JSON but no executable action -> fall through to dictation.
        except (json.JSONDecodeError, AttributeError):
            pass
    m = DICTATION_RE.search(text)
    if m:
        return {"kind": "dictation", "text": m.group(1).strip(), "raw": raw}
    # Bare JSON without COMMAND: prefix.
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict) and "action" in payload and str(payload.get("action") or "").strip():
                return {
                    "kind": "command",
                    "action": str(payload.get("action", "")),
                    "target": str(payload.get("target", "")),
                    "value": str(payload.get("value", "")),
                    "raw": raw,
                }
        except (json.JSONDecodeError, AttributeError):
            pass
    # Default: treat as dictation (safe — types text instead of running actions).
    cleaned = text
    if not cleaned:
        return {"kind": "dictation", "text": "", "raw": raw}
    return {"kind": "dictation", "text": cleaned, "raw": raw}


def _heuristic_parse(transcript: str) -> dict:
    """Offline fallback when Ollama is unreachable. Covers the core commands."""
    t = transcript.strip().lower()
    if m := re.match(r"delete (?:the )?last (\d+) words?", t):
        return {"kind": "command", "action": "delete", "target": "word",
                "value": m.group(1), "raw": transcript, "fallback": True}
    if t.startswith("delete") and "line" in t:
        return {"kind": "command", "action": "delete", "target": "line",
                "value": "", "raw": transcript, "fallback": True}
    if t.startswith("delete"):
        return {"kind": "command", "action": "delete", "target": "selection",
                "value": "", "raw": transcript, "fallback": True}
    if "new paragraph" in t or "new line" in t:
        return {"kind": "command", "action": "new_paragraph", "target": "",
                "value": "", "raw": transcript, "fallback": True}
    if m := re.match(r"switch (?:to|to app|app)?\s*(.+)", t):
        return {"kind": "command", "action": "switch_app", "target": m.group(1).strip(),
                "value": "", "raw": transcript, "fallback": True}
    if "scroll" in t:
        direction = "down" if "down" in t else "up"
        n = re.search(r"(\d+)", t)
        return {"kind": "command", "action": "scroll", "target": direction,
                "value": n.group(1) if n else "3", "raw": transcript, "fallback": True}
    if t in ("undo", "undo that"):
        return {"kind": "command", "action": "undo", "target": "", "value": "",
                "raw": transcript, "fallback": True}
    return {"kind": "dictation", "text": transcript.strip(), "raw": transcript, "fallback": True}


def apply_auto_punctuation(text: str) -> str:
    """Capitalize first letter + trailing period for bare sentences."""
    t = (text or "").strip()
    if not t:
        return t
    if t[-1] in ".!?;:":
        return t[0].upper() + t[1:] if t[0].isalpha() else t
    return (t[0].upper() + t[1:] if t[0].isalpha() else t) + "."


class CommandParser:
    def __init__(self, host: str = "http://localhost:11434",
                 model: str = "dolphin-llama3:8b", timeout: int = 30,
                 temperature: float = 0.1, num_predict: int = 256,
                 enabled: bool = True):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.num_predict = num_predict
        self.enabled = bool(enabled)

    def ollama_available(self) -> bool:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def classify(self, transcript: str) -> dict:
        transcript = (transcript or "").strip()
        if not transcript:
            return {"kind": "dictation", "text": "", "raw": ""}
        if not self.enabled:
            return _heuristic_parse(transcript)
        try:
            r = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model,
                    "prompt": build_prompt(transcript),
                    "stream": False,
                    "options": {"temperature": self.temperature,
                                "num_predict": self.num_predict},
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            raw = r.json().get("response", "")
            result = parse_llm_output(raw)
            result["model"] = self.model
            return result
        except Exception:
            return _heuristic_parse(transcript)
