"""Hotkey tests: parsing + Windows message loop, fully mocked (hermetic)."""
from __future__ import annotations

import sys
import types

import pytest

from voicetype.hotkeys import HotkeyListener, MOD_ALT, MOD_CONTROL, parse_hotkey


def test_parse_combos():
    assert parse_hotkey("ctrl+alt+space") == (MOD_CONTROL | MOD_ALT, 0x20)
    assert parse_hotkey("shift+F5") == (0x4, 0x74)
    assert parse_hotkey("win+x") == (0x8, ord("X"))
    assert parse_hotkey("ctrl+alt+v") == (MOD_CONTROL | MOD_ALT, ord("V"))
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+alt+fancykey")
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+alt+")
    with pytest.raises(ValueError):
        parse_hotkey("")


def test_unsupported_platform_returns_false(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert HotkeyListener("ctrl+alt+space", lambda: None).start() is False


def test_bad_spec_returns_false(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert HotkeyListener("ctrl+alt+boguskey", lambda: None).start() is False


def _fake_ctypes(monkeypatch):
    import ctypes
    calls: list = []

    class MSG:
        def __init__(self):
            self.message = 0
            self.wParam = 0

    class U32:
        def RegisterHotKey(self, hwnd, hid, mods, vk):
            calls.append(("register", hid, mods, vk))
            return True

        def GetMessageW(self, msg, hwnd, a, b):
            return script(msg)

        def TranslateMessage(self, msg):
            calls.append(("translate",))

        def DispatchMessageW(self, msg):
            calls.append(("dispatch",))

        def UnregisterHotKey(self, hwnd, hid):
            calls.append(("unregister", hid))

        def PostThreadMessageW(self, *a):
            calls.append(("quit-posted",))

    u32 = U32()
    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=u32), raising=False)
    monkeypatch.setattr(ctypes, "byref", lambda x: x)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes",
                        types.SimpleNamespace(MSG=MSG))
    return calls, u32


def test_hotkey_press_invokes_callback(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    from voicetype.hotkeys import WM_HOTKEY
    calls, u32 = _fake_ctypes(monkeypatch)

    fired: list = []

    def script(msg, *args):
        if not hasattr(script, "n"):
            script.n = 0
        script.n += 1
        if script.n == 1:
            msg.message = WM_HOTKEY
            msg.wParam = 0xBEEF
            return 1
        return 0

    u32.GetMessageW = script
    listener = HotkeyListener("ctrl+alt+space", lambda: fired.append(1))
    assert listener.start() is True
    listener._thread.join(timeout=5.0)
    assert fired == [1]
    assert ("register", 0xBEEF, MOD_CONTROL | MOD_ALT, 0x20) in calls
    assert ("unregister", 0xBEEF) in calls
    assert not listener.running


def test_callback_exception_swallowed_and_stop(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    from voicetype.hotkeys import WM_HOTKEY
    calls, u32 = _fake_ctypes(monkeypatch)

    def bad():
        raise RuntimeError("callback bug")

    def script(msg, *args):
        if not hasattr(script, "n"):
            script.n = 0
        script.n += 1
        if script.n <= 2:  # two hotkey hits, first callback raises
            msg.message = WM_HOTKEY
            msg.wParam = 0xBEEF
            return 1
        return 0

    u32.GetMessageW = script
    listener = HotkeyListener("ctrl+alt+space", bad)
    listener.start()
    listener._thread.join(timeout=5.0)
    listener.stop()  # safe after thread end
    assert ("unregister", 0xBEEF) in calls
    listener.stop()  # double stop safe


def test_register_denied_ends_thread(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    from voicetype.hotkeys import WM_HOTKEY
    calls, u32 = _fake_ctypes(monkeypatch)
    u32.RegisterHotKey = lambda *a: False
    listener = HotkeyListener("ctrl+alt+space", lambda: None)
    assert listener.start() is True  # thread starts...
    listener._thread.join(timeout=5.0)
    assert not listener.running  # ...then exits on RegisterHotKey failure
