"""Injection tests per platform with mocked OS APIs — runs on any OS.

macOS CGEvent (fake Quartz), Windows SendInput (fake ctypes.windll), Linux
uinput (fake evdev) plus tool fallbacks (fake xdotool). Also covers special
characters, newlines, undo behaviors, and clipboard fallback on/off.
"""
from __future__ import annotations

import sys
import types

import pytest

from voicetype.injector import (
    BaseInjector,
    CGEventInjector,
    FallbackInjector,
    SendInputInjector,
    UInputInjector,
    get_injector,
)


# ---------- fakes ----------
class FakeQuartz:
    kCGHIDEventTap = 0
    kCGEventFlagMaskCommand = 1 << 0
    kCGEventFlagMaskShift = 1 << 1
    kCGEventFlagMaskAlternate = 1 << 2
    kCGEventFlagMaskControl = 1 << 3
    kCGScrollEventUnitLine = 0

    def __init__(self):
        self.posts: list = []
        self.flags: list = []

    def CGEventCreateKeyboardEvent(self, _, code, down):
        return ("key", code, down)

    def CGEventPost(self, tap, ev):
        self.posts.append((tap, ev))

    def CGEventSetFlags(self, ev, flags):
        self.flags.append((ev, flags))

    def CGEventCreateScrollWheelEvent(self, *a):
        return ("scroll", a)


class FakePasteboard:
    def __init__(self):
        self.strings: list = []

    def clearContents(self):
        pass

    def setString_forType_(self, s, t):
        self.strings.append(s)
        return True


class FakeWindllUser32:
    def __init__(self):
        self.batches: list = []

    def SendInput(self, n, arr, size):
        self.batches.append(n)
        return n


class FakeEcodes:
    EV_KEY = 1
    EV_REL = 2
    REL_WHEEL = 11
    KEY_SPACE = 57
    KEY_TAB = 15
    KEY_MINUS = 12
    KEY_EQUAL = 13
    KEY_LEFTBRACE = 26
    KEY_RIGHTBRACE = 27
    KEY_SEMICOLON = 39
    KEY_APOSTROPHE = 40
    KEY_COMMA = 51
    KEY_DOT = 52
    KEY_SLASH = 53
    KEY_GRAVE = 41
    KEY_BACKSLASH = 43
    KEY_ENTER = 28
    KEY_BACKSPACE = 14
    KEY_LEFTSHIFT = 42
    KEY_LEFTCTRL = 29
    KEY_LEFTALT = 56
    KEY_LEFTMETA = 125
    KEY_HOME = 102
    KEY_END = 103
    KEY_UP = 104
    KEY_DOWN = 105
    KEY_LEFT = 106
    KEY_RIGHT = 107
    KEY_TAB2 = 15
    KEY_ESCAPE = 1


for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    setattr(FakeEcodes, f"KEY_{_ch}", 1000 + ord(_ch))


class FakeUInput:
    def __init__(self, *a, **k):
        self.writes: list = []
        self.syns = 0

    def write(self, *a):
        self.writes.append(a)

    def syn(self):
        self.syns += 1


def _install_fake_quartz(monkeypatch):
    fq = FakeQuartz()
    mod = types.ModuleType("Quartz")
    for k in [a for a in dir(fq) if not a.startswith("_")]:
        setattr(mod, k, getattr(fq, k))
    for name in ("CGEventCreateKeyboardEvent", "CGEventPost",
                 "CGEventSetFlags", "CGEventCreateScrollWheelEvent"):
        setattr(mod, name, getattr(fq, name))
    monkeypatch.setitem(sys.modules, "Quartz", mod)
    return fq


def _install_fake_appkit(monkeypatch):
    pb = FakePasteboard()
    mod = types.ModuleType("AppKit")
    mod.NSPasteboard = types.SimpleNamespace(generalPasteboard=lambda: pb)
    mod.NSStringPboardType = "NSStringPboardType"
    monkeypatch.setitem(sys.modules, "AppKit", mod)
    return pb


def _install_fake_windll(monkeypatch):
    import ctypes
    fake = types.SimpleNamespace(user32=FakeWindllUser32())
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)
    return fake.user32


def _install_fake_evdev(monkeypatch, fail_init=False):
    instances: list = []

    class UInput(FakeUInput):
        def __init__(self, *a, **k):
            if fail_init:
                raise OSError("/dev/uinput missing")
            super().__init__(*a, **k)
            instances.append(self)

    mod = types.ModuleType("evdev")
    mod.UInput = UInput
    mod.ecodes = FakeEcodes
    monkeypatch.setitem(sys.modules, "evdev", mod)
    return instances


# ---------- macOS CGEvent ----------
def test_cg_event_types_ascii(monkeypatch, mock_platform_mac):
    fq = _install_fake_quartz(monkeypatch)
    inj = CGEventInjector()
    inj.type_text("hi")
    assert inj.typed_log == ["hi"]
    downs = [e for _, e in fq.posts if e[0] == "key" and e[2] is True]
    assert len(downs) == 2  # h, i key-downs


def test_cg_event_combo_flags(monkeypatch, mock_platform_mac):
    fq = _install_fake_quartz(monkeypatch)
    inj = CGEventInjector()
    inj.press("command", "c")
    assert fq.flags and fq.flags[0][1] & FakeQuartz.kCGEventFlagMaskCommand


def test_cg_event_scroll_and_clipboard(monkeypatch, mock_platform_mac):
    fq = _install_fake_quartz(monkeypatch)
    pb = _install_fake_appkit(monkeypatch)
    inj = CGEventInjector()
    inj.scroll("down", 2)
    assert any(e[0] == "scroll" for _, e in fq.posts)
    inj.type_text("caf\u00e9")
    assert pb.strings == ["\u00e9"]  # unicode via clipboard paste


def test_cg_event_survives_missing_quartz(monkeypatch, mock_platform_mac):
    monkeypatch.setitem(sys.modules, "Quartz", None)
    inj = CGEventInjector()
    inj.type_text("ok")  # swallowed, but still logged
    inj.press("enter")
    inj.scroll("up", 1)
    assert inj.typed_log == ["ok"]


# ---------- Windows SendInput ----------
def test_sendinput_unicode_batches(monkeypatch, mock_platform_windows):
    u32 = _install_fake_windll(monkeypatch)
    inj = SendInputInjector()
    inj.type_text("ab")
    assert inj.typed_log == ["ab"]
    assert u32.batches == [4]  # down+up per char


def test_sendinput_combo_and_scroll(monkeypatch, mock_platform_windows):
    u32 = _install_fake_windll(monkeypatch)
    inj = SendInputInjector()
    inj.press("ctrl", "c")
    assert u32.batches == [4]  # 2 down + 2 up
    inj.scroll("down", 2)
    assert u32.batches[-1] == 1  # one wheel event


def test_sendinput_unicode_clipboard_path(monkeypatch, mock_platform_windows):
    _install_fake_windll(monkeypatch)
    inj = SendInputInjector()
    inj.type_text("\u4e2d")
    assert inj.typed_log == ["\u4e2d"]


def test_sendinput_without_windll_is_safe(monkeypatch):
    import ctypes
    monkeypatch.delattr(ctypes, "windll", raising=False)
    inj = SendInputInjector()
    inj.type_text("safe")
    inj.press("enter")
    inj.scroll("up", 1)
    assert inj.typed_log == ["safe"]


# ---------- Linux uinput ----------
def test_uinput_types_with_shift(monkeypatch, mock_platform_linux):
    inst = _install_fake_evdev(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    inj = UInputInjector()
    assert inj._uinput is not None
    inj.type_text("A")
    writes = inst[0].writes
    codes = [w[1] for w in writes]
    assert FakeEcodes.KEY_LEFTSHIFT in codes and FakeEcodes.KEY_A in codes


def test_uinput_falls_back_to_xdotool(monkeypatch, mock_platform_linux):
    _install_fake_evdev(monkeypatch, fail_init=True)
    calls: list = []
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/xdotool" if n == "xdotool" else None)
    import subprocess
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or types.SimpleNamespace(returncode=0))
    inj = UInputInjector()
    assert inj._uinput is None
    inj.type_text("hello")
    assert calls[0][:2] == ["xdotool", "type"]
    inj.press("ctrl", "c")
    assert calls[1][:2] == ["xdotool", "key"]


def test_uinput_wtype_and_ydotool_order(monkeypatch, mock_platform_linux):
    _install_fake_evdev(monkeypatch, fail_init=True)
    import subprocess
    calls: list = []
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/wtype" if n == "wtype" else None)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or types.SimpleNamespace(returncode=0))
    UInputInjector().type_text("hi")
    assert calls[0][0] == "wtype"


def test_uinput_clipboard_xclip(monkeypatch, mock_platform_linux):
    _install_fake_evdev(monkeypatch, fail_init=True)
    import subprocess
    calls: list = []
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/xclip" if n == "xclip" else None)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or types.SimpleNamespace(returncode=0))
    inj = UInputInjector()
    assert inj._clipboard_paste_impl("h\u00e9llo") is True
    assert calls[0][0] == "xclip"


def test_uinput_clipboard_none_available(monkeypatch, mock_platform_linux):
    _install_fake_evdev(monkeypatch, fail_init=True)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert UInputInjector()._clipboard_paste_impl("x") is False


# ---------- shared behavior on every backend ----------
# NOTE: these run against REAL backend classes, so every OS API is faked
# below. A past version of this test fired live SendInput keystrokes into
# whatever window had focus on the dev machine. Never remove the fakes.
@pytest.mark.parametrize("cls", [CGEventInjector, SendInputInjector, UInputInjector, FallbackInjector])
def test_special_chars_newlines_undo(monkeypatch, cls):
    _install_fake_quartz(monkeypatch)
    _install_fake_appkit(monkeypatch)
    _install_fake_windll(monkeypatch)
    _install_fake_evdev(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    inj = cls()
    inj.type_text("caf\u00e9 \u4e2d\nline2\r\nline3")
    assert inj.typed_log == ["caf\u00e9 \u4e2d\nline2\r\nline3"]
    enters = [e for e in inj.events_log if e == ("key", "enter")]
    assert len(enters) == 2
    inj.undo()
    inj.redo()
    inj.new_paragraph()
    assert ("key", "paragraph") in inj.events_log


def test_undo_single_vs_stacked():
    inj = FallbackInjector(chunk_size=1, undo_behavior="stacked")
    inj.type_text("x" * 100)  # 100 text events -> multiple undo bursts
    inj.undo()
    combos = [e for e in inj.events_log if e[0] == "combo"]
    assert len(combos) > 1  # stacked repeats undo for the whole utterance
    inj2 = FallbackInjector(undo_behavior="single")
    inj2.type_text("x" * 100)
    n_before = len(inj2.events_log)
    inj2.undo()
    assert len(inj2.events_log) == n_before + 1


def test_clipboard_toggle_off_falls_back_to_placeholder():
    seen: list = []

    class Rec(FallbackInjector):
        def _type_ascii_chunk(self, chunk: str) -> None:
            seen.append(chunk)

    Rec(use_clipboard_for_unicode=False).type_text("\u00e9")
    assert seen == ["?"]  # no clipboard -> ascii placeholder sent to OS
    inj = FallbackInjector(use_clipboard_for_unicode=False)
    inj.type_text("\u00e9")
    assert inj.typed_log == ["\u00e9"]  # log always keeps the true text


def test_delete_variants_and_scroll_directions():
    inj = FallbackInjector()
    inj.delete("word", "2")
    inj.delete("line")
    inj.delete("selection")
    inj.delete("last_sentence")
    inj.delete("word", "not-a-number")
    assert ("delete", "word", 2) in inj.events_log
    assert any(e[1] == "line" for e in inj.events_log if e[0] == "delete")
    inj.scroll("up", 1)
    inj.scroll("DOWN", "bad")
    assert ("scroll", "up", 1) in inj.events_log
    assert ("scroll", "down", 3) in inj.events_log


def test_chunking_and_empty_text():
    inj = FallbackInjector(chunk_size=2)
    inj.type_text("abcd")
    assert [e[1] for e in inj.events_log if e[0] == "text"] == ["ab", "cd"]
    inj.type_text("")
    assert inj.typed_log == ["abcd"]


def test_os_calls_intercepted_by_fakes(monkeypatch):
    """Tripwire: backend typing must be intercepted by fakes, never the OS.

    If anyone removes the fake installers below, this fails (no receipts)
    instead of typing into the focused window of whoever runs the suite.
    """
    u32 = _install_fake_windll(monkeypatch)
    fq = _install_fake_quartz(monkeypatch)
    _install_fake_appkit(monkeypatch)
    _install_fake_evdev(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    SendInputInjector().type_text("hi")
    CGEventInjector().type_text("hi")
    UInputInjector().type_text("hi")
    FallbackInjector().type_text("hi")
    assert u32.batches  # fake SendInput received them, not Windows
    assert fq.posts  # fake CGEvent received them, not macOS


def test_get_injector_routing(monkeypatch):
    import platform
    for system, name in [("Darwin", "cg_event"), ("Windows", "sendinput"), ("Linux", "uinput")]:
        monkeypatch.setattr(platform, "system", lambda s=system: s)
        inj = get_injector("auto")
        assert inj.name in (name, "fallback")
    assert get_injector("fallback").name == "fallback"
    assert get_injector("bogus-backend").name == "fallback"
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert get_injector("cg_event").name == "fallback"  # wrong OS -> safe fallback
