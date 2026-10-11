"""Coverage-gap tests: backend internals, OS role readers, strategies, wiring."""
from __future__ import annotations

import json
import subprocess
import sys
import types

import numpy as np
import pytest

from voicetype.injector import (
    BaseInjector,
    CGEventInjector,
    FallbackInjector,
    SendInputInjector,
    UInputInjector,
)


class ExplodingInjector(FallbackInjector):
    def _type_ascii_chunk(self, chunk):
        raise RuntimeError("os refused")

    def _press_combo(self, *keys):
        raise RuntimeError("os refused")

    def _scroll_backend(self, direction, amount):
        raise RuntimeError("os refused")


# ---------- BaseInjector new-method branches ----------
def test_type_raw_all_branches():
    inj = FallbackInjector()
    inj.type_raw("")
    assert inj.typed_log == []
    inj.type_raw("a\tb\x1bc\nd")
    assert inj.typed_log == ["a\tb\x1bc\nd"]
    kinds = [e[0] for e in inj.events_log]
    assert "raw" in kinds and ("key", "tab") in inj.events_log
    assert ("key", "escape") in inj.events_log
    assert ("key", "enter") in inj.events_log
    assert inj.use_clipboard_for_unicode is True  # flag restored
    assert inj.last_error is None


def test_type_raw_unicode_and_crlf():
    inj = FallbackInjector(use_clipboard_for_unicode=True)
    inj.type_raw("caf\u00e9\r\nx")
    assert inj.typed_log == ["caf\u00e9\r\nx"]
    assert ("key", "enter") in inj.events_log


def test_type_raw_exception_tracked():
    inj = ExplodingInjector()
    inj.type_raw("ab")
    assert inj.last_error and "type_raw" in inj.last_error


def test_type_text_exception_tracked(monkeypatch):
    inj = FallbackInjector()
    monkeypatch.setattr(inj, "_type_line",
                        lambda line: (_ for _ in ()).throw(RuntimeError("x")))
    inj.type_text("hi")
    assert inj.last_error and "type_text" in inj.last_error


def test_press_scroll_exceptions_tracked():
    inj = ExplodingInjector()
    inj.press("a")
    assert inj.last_error and "press" in inj.last_error
    inj.scroll("down", 1)
    assert inj.last_error and "scroll" in inj.last_error


def test_compose_text_branches():
    inj = FallbackInjector(chunk_size=2)
    assert inj.compose_text("") is True
    assert inj.compose_text("abcd") is True
    kinds = [e[0] for e in inj.events_log]
    assert kinds.count("preedit") == 2 and kinds.count("commit") == 2


def test_compose_text_exception():
    inj = ExplodingInjector()
    assert inj.compose_text("ab") is False
    assert inj.last_error and "compose" in inj.last_error


def test_paste_text_branches():
    inj = FallbackInjector()
    assert inj.paste_text("") is True
    assert inj.paste_text("x") is False  # fallback has no clipboard
    assert inj.last_error is None  # clean False, not an error

    class Clip(FallbackInjector):
        def _clipboard_paste_impl(self, text):
            return True

    assert Clip().paste_text("hello") is True

    class Boom(FallbackInjector):
        def _clipboard_type(self, text):
            raise RuntimeError("clip")

    assert Boom().paste_text("x") is False


def test_paste_text_darwin_modifier(mock_platform_mac):
    class Clip(FallbackInjector):
        def _clipboard_paste_impl(self, text):
            return True

    assert Clip().paste_text("x") is True


def test_type_console_branches():
    inj = FallbackInjector()
    inj.type_console("")
    assert inj.typed_log == []
    inj.type_console("Ab\tc\nd\u00e9")
    assert inj.typed_log == ["Ab\tc\nd\u00e9"]
    combos = [e[1] for e in inj.events_log if e[0] == "combo"]
    assert ("shift", "a") in combos and ("tab",) in combos
    assert ("key", "enter") in inj.events_log


def test_type_console_exception():
    inj = ExplodingInjector()
    inj.type_console("ab")
    assert inj.last_error  # press() records the refusal first


def test_paste_rich_base_delegates():
    class Clip(FallbackInjector):
        def _clipboard_paste_impl(self, text):
            return True

    assert Clip().paste_rich("{\\rtf x}", "x") is True
    assert FallbackInjector().paste_rich("{\\rtf x}", "x") is False


def test_abstract_bodies_and_notimplemented():
    inj = FallbackInjector()
    BaseInjector._type_ascii_chunk(inj, "x")
    BaseInjector._press_combo(inj, "x")
    BaseInjector._scroll_backend(inj, "down", 1)
    with pytest.raises(NotImplementedError):
        BaseInjector._clipboard_paste_impl(inj, "x")
    with pytest.raises(TypeError):
        BaseInjector()  # type: ignore


# ---------- backend error tracking ----------
def test_cgevent_error_paths(monkeypatch, mock_platform_mac):
    import tests.test_injection as ti
    ti._install_fake_quartz(monkeypatch)
    import Quartz  # noqa  (fake module)
    monkeypatch.setattr(Quartz, "CGEventPost",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("denied")))
    inj = CGEventInjector()
    inj.type_text("hi")
    assert inj.last_error and "cgevent" in inj.last_error
    inj.press("a")
    inj.scroll("down", 1)


def test_sendinput_paste_rich_paths(monkeypatch, mock_platform_windows):
    import ctypes
    mem: dict = {}

    class K32:
        def GlobalAlloc(self, f, n):
            h = len(mem) + 1
            mem[h] = bytearray(n)
            return h

        def GlobalLock(self, h):
            return h

        def GlobalUnlock(self, h):
            return True

    class U32:
        def __init__(self, open_ok=True, fail_register=False):
            self.batches: list = []
            self._open_ok = open_ok
            self._fail_register = fail_register

        def RegisterClipboardFormatW(self, n):
            if self._fail_register:
                raise RuntimeError("no fmt")
            return 0xC001

        def GetForegroundWindow(self):
            return 7

        def OpenClipboard(self, h):
            return 1 if self._open_ok else 0

        def EmptyClipboard(self):
            pass

        def SetClipboardData(self, f, h):
            pass

        def CloseClipboard(self):
            pass

        def SendInput(self, *a):
            self.batches.append(a)
            return 1

    def fake_memmove(dst, src, n):
        mem[dst][:n] = bytes(src)[:n]
        return n

    u32 = U32()
    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=u32, kernel32=K32()),
                        raising=False)
    monkeypatch.setattr(ctypes, "memmove", fake_memmove)
    inj = SendInputInjector()
    assert inj.paste_rich("{\\rtf hi}", "hi") is True
    assert ("paste-rich", 2) in inj.events_log
    # OpenClipboard denied -> plain paste fallback
    u32b = U32(open_ok=False)
    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=u32b, kernel32=K32()),
                        raising=False)
    assert SendInputInjector().paste_rich("{\\rtf hi}", "hi") is True
    # RegisterClipboardFormatW explodes -> error noted, plain fallback
    u32c = U32(fail_register=True)
    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=u32c, kernel32=K32()),
                        raising=False)
    inj3 = SendInputInjector()
    assert inj3.paste_rich("{\\rtf hi}", "hi") is True
    assert inj3.last_error is None  # fallback cleared it


def test_uinput_compose_paths(monkeypatch, mock_platform_linux):
    import tests.test_injection as ti
    inst = ti._install_fake_evdev(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    inj = UInputInjector()
    assert inj.compose_text("") is True
    assert inj.compose_text("AB") is True
    assert ("preedit", "AB") in inj.events_log
    assert ("commit", "AB") in inj.events_log
    ti._install_fake_evdev(monkeypatch, fail_init=True)
    inj2 = UInputInjector()
    assert inj2.compose_text("hi") is True  # tool fallback path


def test_char_keycode_edges():
    from tests.test_injection import FakeEcodes
    assert UInputInjector._char_keycode("\t", FakeEcodes) == FakeEcodes.KEY_TAB
    assert UInputInjector._char_keycode("\n", FakeEcodes) == FakeEcodes.KEY_ENTER
    assert UInputInjector._char_keycode("z", FakeEcodes) == FakeEcodes.KEY_Z
    assert UInputInjector._char_keycode("7", FakeEcodes) == FakeEcodes.KEY_7
    assert UInputInjector._char_keycode("\u00e9", FakeEcodes) is None
