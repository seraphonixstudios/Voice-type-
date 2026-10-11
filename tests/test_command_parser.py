"""Command-parser tests: classification, JSON schema, malformed input, fallback."""
from __future__ import annotations

import pytest

from voicetype.ollama_parser import (
    COMMAND_RE,
    SYSTEM_PROMPT,
    CommandParser,
    build_prompt,
    parse_llm_output,
)

COMMAND_SCHEMA_KEYS = {"action", "target", "value"}


def _assert_command_shape(result: dict):
    assert result["kind"] == "command"
    assert COMMAND_SCHEMA_KEYS <= set(result.keys())
    for key in ("action", "target", "value"):
        assert isinstance(result[key], str)


# ---- prompt contract ----
def test_prompt_contains_spec_format():
    p = build_prompt("scroll down")
    assert "DICTATION" in p and "COMMAND" in p and '"action"' in p
    assert SYSTEM_PROMPT in p


# ---- dictation classification ----
@pytest.mark.parametrize("raw,expected", [
    ("DICTATION: Hello, world!", "Hello, world!"),
    ("dictation: lower case prefix works", "lower case prefix works"),
    ("DICTATION:Type this email", "Type this email"),
    ("just some words", "just some words"),  # no prefix -> safe dictation default
])
def test_parse_dictation(raw, expected):
    r = parse_llm_output(raw)
    assert r["kind"] == "dictation" and r["text"] == expected


# ---- command classification + schema ----
@pytest.mark.parametrize("raw,action,target,value", [
    ('COMMAND: {"action": "scroll", "target": "down", "value": "3"}',
     "scroll", "down", "3"),
    ('COMMAND: {"action": "delete", "target": "word", "value": "2"}',
     "delete", "word", "2"),
    ('COMMAND: {"action": "new_paragraph", "target": "", "value": ""}',
     "new_paragraph", "", ""),
    ('COMMAND: {"action": "switch_app", "target": "Firefox", "value": ""}',
     "switch_app", "Firefox", ""),
    ('COMMAND: {"action": "undo", "target": "", "value": ""}',
     "undo", "", ""),
    ('{"action": "redo", "target": "", "value": ""}', "redo", "", ""),  # bare JSON
])
def test_parse_command_schema(raw, action, target, value):
    r = parse_llm_output(raw)
    _assert_command_shape(r)
    assert (r["action"], r["target"], r["value"]) == (action, target, value)


# ---- malformed responses never raise, degrade to dictation ----
@pytest.mark.parametrize("raw", [
    'COMMAND: {"action": "scroll", "target":',   # truncated JSON
    "COMMAND: not json at all",
    'COMMAND: {"no_action": true}',              # valid JSON, no action key
    "",
    "   ",
])
def test_malformed_responses_become_dictation(raw):
    r = parse_llm_output(raw)
    assert r["kind"] == "dictation"
    assert isinstance(r["text"], str)


# ---- live classify() with mocked Ollama ----
def test_classify_dictation_via_ollama(mock_ollama):
    import requests
    # Override default reply with a dictation response.
    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "DICTATION: Buy milk tomorrow"}
    import voicetype.ollama_parser as m
    orig = requests.post
    try:
        m.requests.post = lambda *a, **k: R()
        out = CommandParser().classify("buy milk tomorrow")
    finally:
        m.requests.post = orig
    assert out["kind"] == "dictation" and out["text"] == "Buy milk tomorrow"


def test_classify_sends_spec_payload(mock_ollama):
    CommandParser(host="http://x:11434", model="m", temperature=0.2,
                  num_predict=64).classify("scroll down")
    req = mock_ollama["post"][0]
    assert req["url"] == "http://x:11434/api/generate"
    body = req["json"]
    assert body["model"] == "m"
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.2, "num_predict": 64}
    assert "scroll down" in body["prompt"]


def test_classify_command_result_shape(mock_ollama):
    out = CommandParser().classify("scroll down")
    _assert_command_shape(out)
    assert out["model"] == "dolphin-llama3:8b"


def test_classify_empty_is_empty_dictation(mock_ollama):
    assert CommandParser().classify("   ") == {"kind": "dictation", "text": "", "raw": ""}
    assert mock_ollama["post"] == []  # no HTTP for empty input


def test_ollama_error_status_falls_back(mock_ollama_down):
    out = CommandParser(host="http://127.0.0.1:1", timeout=1).classify("scroll down two pages")
    assert out["action"] == "scroll" and out.get("fallback") is True


def test_ollama_down_heuristics(mock_ollama_down):
    p = CommandParser(host="http://127.0.0.1:1", timeout=1)
    assert p.classify("delete last 3 words")["action"] == "delete"
    assert p.classify("delete the whole line")["target"] == "line"
    assert p.classify("delete it")["target"] == "selection"
    assert p.classify("new paragraph")["action"] == "new_paragraph"
    assert p.classify("switch to Firefox")["target"] == "firefox"
    assert p.classify("scroll up")["target"] == "up"
    assert p.classify("undo")["action"] == "undo"
    d = p.classify("Hello there friend")
    assert d["kind"] == "dictation" and "Hello" in d["text"]


def test_disabled_parser_never_calls_ollama(mock_ollama):
    out = CommandParser(enabled=False).classify("scroll down")
    assert out["action"] == "scroll"
    assert mock_ollama["post"] == []


def test_ollama_available_probe(mock_ollama):
    assert CommandParser().ollama_available() is True
    assert mock_ollama["get"][0]["url"].endswith("/api/tags")


def test_ollama_available_false_when_down(mock_ollama_down):
    assert CommandParser().ollama_available() is False


def test_command_regex_tolerates_spacing():
    assert COMMAND_RE.search('COMMAND:{"action":"x","target":"","value":""}')
