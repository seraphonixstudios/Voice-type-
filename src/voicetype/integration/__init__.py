"""Universal integration layer: OS-level text/command injection for every app.

VoiceType never talks to apps directly — no per-app plugins, no exceptions.
Text and commands go through the OS input layer (CGEvent / SendInput /
uinput), so any app that accepts keyboard input works automatically.
"""
from voicetype.integration.categories import (
    BROWSER,
    GAME,
    GENERIC,
    IDE,
    OFFICE,
    PASSWORD_MANAGER,
    TERMINAL,
    classify_app,
    classify_field,
    is_secure_context,
)
from voicetype.integration.detectors import (
    detect_anticheat,
    get_focused_role,
    list_processes,
    should_block_injection,
)
from voicetype.integration.router import (
    InjectionResult,
    IntegrationRouter,
    Strategy,
)
from voicetype.integration.strategies import (
    ime_events,
    normalize_newlines,
    preserve_indentation,
    terminal_chunks,
    to_plain_text,
    to_rtf,
)

__all__ = [
    "BROWSER", "GAME", "GENERIC", "IDE", "OFFICE", "PASSWORD_MANAGER", "TERMINAL",
    "InjectionResult", "IntegrationRouter", "Strategy",
    "classify_app", "classify_field", "is_secure_context",
    "detect_anticheat", "get_focused_role", "list_processes",
    "should_block_injection",
    "ime_events", "normalize_newlines", "preserve_indentation",
    "terminal_chunks", "to_plain_text", "to_rtf",
]
