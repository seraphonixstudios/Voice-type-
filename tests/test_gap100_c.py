"""100% push III: injector/ui/pairing/server/context/permissions leftovers."""
from __future__ import annotations

import subprocess
import sys
import types

import numpy as np
import pytest


# ---------- injector leftovers ----------
def test_console_unicode_raise_tracked():
    from voicetype.injector import FallbackInjector

    class BadUni(FallbackInjector):
        def _type_unicode_char(self, ch):
            raise RuntimeError("no uni")

    inj = BadUni()
    inj.type_console("a\u00e9")
    assert inj.last_error and "type_console" in inj.last_error


def test_type_with_delay():
    from voicetype.injector import FallbackInjector
    inj = FallbackInjector(delay_ms=1)
    inj.type_text("hi")
    assert inj.typed_log == ["hi"]


def test_clipboard_impl_raise_tracked():
    from voicetype.injector import BaseInjector, FallbackInjector

    class Minimal(BaseInjector):
        name = "minimal"

        def _type_ascii_chunk(self, chunk):
            pass

        def _press_combo(self, *keys):
            pass

        def _scroll_backend(self, direction, amount):
            pass

    assert Minimal()._clipboard_type("x") is False  # NotImplementedError -> False


def test_delete_exotic_digit():
    from voicetype.injector import FallbackInjector
    inj = FallbackInjector()
    inj.delete("word", "\u00b2")  # isdigit() True, int() fails -> n=1
    assert ("delete", "word", 1) in inj.events_log


def test_cgevent_alt_ctrl_flags_and_clipboard_fail(monkeypatch, mock_platform_mac):
    import tests.test_injection as ti
    fq = ti._install_fake_quartz(monkeypatch)
    from voicetype.injector import CGEventInjector
    inj = CGEventInjector()
    inj.press("alt", "x")
    inj.press("ctrl", "x")
    assert any(fq.flags)
    pb_mod = types.ModuleType("AppKit")
    pb_mod.NSPasteboard = types.SimpleNamespace(
        generalPasteboard=lambda: (_ for _ in ()).throw(RuntimeError("locked")))
    pb_mod.NSStringPboardType = "t"
    monkeypatch.setitem(sys.modules, "AppKit", pb_mod)
    assert inj._clipboard_paste_impl("x") is False
    assert inj.last_error is None  # clipboard impl handles its own failure


def test_sendinput_alloc_zero_continues(monkeypatch, mock_platform_windows):
    import ctypes
    from tests.test_injection import _install_fake_windll
    from voicetype.injector import SendInputInjector
    calls = {"n": 0}

    class K32ZeroThen:
        def GlobalAlloc(self, f, n):
            calls["n"] += 1
            return 0 if calls["n"] == 1 else 999

        def GlobalLock(self, h):
            return h

        def GlobalUnlock(self, h):
            return True

    u32 = _install_fake_windll(monkeypatch)
    mem: dict = {}
    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=u32, kernel32=K32ZeroThen()),
                        raising=False)
    monkeypatch.setattr(ctypes, "memmove",
                        lambda dst, src, n: mem.__setitem__(dst, bytes(src)[:n]))
    u32.EmptyClipboard = lambda: None
    u32.SetClipboardData = lambda f, h: None
    u32.CloseClipboard = lambda: None
    u32.GetForegroundWindow = lambda: 1
    u32.OpenClipboard = lambda h: 1
    u32.RegisterClipboardFormatW = lambda name: 0xC001
    assert SendInputInjector().paste_rich("{\\rtf x}", "x") is True


def test_uinput_type_write_failure(monkeypatch, mock_platform_linux):
    import tests.test_injection as ti
    inst = ti._install_fake_evdev(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    from voicetype.injector import UInputInjector
    inj = UInputInjector()
    assert inj._uinput is not None
    inj._uinput.write = lambda *a: (_ for _ in ()).throw(RuntimeError("uinput gone"))
    inj.type_text("hi")
    assert inj.last_error and "uinput" in inj.last_error


def test_uinput_combo_branches_and_failure(monkeypatch, mock_platform_linux):
    import tests.test_injection as ti
    ti._install_fake_evdev(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    from voicetype.injector import UInputInjector
    inj = UInputInjector()
    inj.press("shift", "a")
    inj.press("alt", "b")
    inj.press("command", "c")
    inj.press("backspace")
    inj.press("space")
    inj.press("home")
    inj.press("5")
    inj.press("enter")
    assert inj.last_error is None
    inj._uinput.write = lambda *a: (_ for _ in ()).throw(RuntimeError("gone"))
    inj.press("a")
    assert inj.last_error and "uinput.combo" in inj.last_error


def test_uinput_tool_failures(monkeypatch, mock_platform_linux):
    import subprocess as sp
    import tests.test_injection as ti
    from voicetype.injector import UInputInjector
    ti._install_fake_evdev(monkeypatch, fail_init=True)
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/wtype" if n == "wtype" else None)
    monkeypatch.setattr(sp, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("dead")))
    inj = UInputInjector()
    inj.press("a")  # wtype path raises -> tracked
    assert inj.last_error and "uinput.key-tool" in inj.last_error
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/xdotool" if n == "xdotool" else None)
    inj.scroll("down", 1)
    assert inj.last_error and "uinput.scroll" in inj.last_error


def test_uinput_scroll_tool_and_relwheel(monkeypatch, mock_platform_linux):
    import subprocess as sp
    import tests.test_injection as ti
    from voicetype.injector import UInputInjector
    ti._install_fake_evdev(monkeypatch, fail_init=True)
    calls: list = []
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/xdotool" if n == "xdotool" else None)
    monkeypatch.setattr(sp, "run",
                        lambda *a, **k: calls.append(a[0]) or types.SimpleNamespace(returncode=0))
    UInputInjector().scroll("up", 2)
    assert calls and calls[0][:2] == ["xdotool", "click"]
    # no tools + no uinput: silent fall-through
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    UInputInjector().scroll("down", 1)


def test_uinput_compose_unicode_and_failure(monkeypatch, mock_platform_linux):
    import tests.test_injection as ti
    inst = ti._install_fake_evdev(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    from voicetype.injector import UInputInjector
    inj = UInputInjector()
    assert inj.compose_text("a\u00e9") is True  # unicode falls back per-char
    inst[0].write = lambda *a: (_ for _ in ()).throw(RuntimeError("gone"))
    assert inj.compose_text("ab") is False


def test_uinput_clipboard_run_failure(monkeypatch, mock_platform_linux):
    import subprocess as sp
    import tests.test_injection as ti
    from voicetype.injector import UInputInjector
    ti._install_fake_evdev(monkeypatch, fail_init=True)
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/xclip" if n == "xclip" else None)
    monkeypatch.setattr(sp, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("dead")))
    assert UInputInjector()._clipboard_paste_impl("x") is False


def test_fallback_with_stub_pynput(monkeypatch):
    from voicetype.injector import FallbackInjector
    pressed: list = []

    class Ctl:
        def type(self, chunk):
            pressed.append(("type", chunk))

        def press(self, k):
            pressed.append(("press", k))

        def release(self, k):
            pressed.append(("release", k))

        def scroll(self, dx, dy):
            pressed.append(("scroll", dy))

    kb = types.SimpleNamespace(Controller=Ctl, Key=types.SimpleNamespace(
        ctrl="ctrl", shift="shift", enter="enter"))
    mouse = types.SimpleNamespace(Controller=Ctl)
    monkeypatch.setitem(sys.modules, "pynput", types.ModuleType("pynput"))
    monkeypatch.setitem(sys.modules, "pynput.keyboard", kb)
    monkeypatch.setitem(sys.modules, "pynput.mouse", mouse)
    inj = FallbackInjector()
    inj.type_text("ab")
    inj.press("ctrl", "c")
    inj.scroll("down", 2)
    assert ("type", "ab") in pressed
    assert ("scroll", -2) in pressed


# ---------- ui_main leftovers ----------
def _win(tmp_path):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    store = SettingsStore(ConfigManager(user_path=tmp_path / "c.yaml"), pipe)
    return MainWindow(store, pipe), store, pipe


def test_ui_meter_and_context_failures(tmp_path, qapp):
    win, store, pipe = _win(tmp_path)
    real_pipe = win.pipeline

    class BadRing:
        def read_latest(self, n):
            raise RuntimeError("ring gone")

    win.pipeline = types.SimpleNamespace(ring=BadRing(), context=real_pipe.context,
                                         wake=real_pipe.wake, listening=True)
    win._tick_meter()  # meter failure swallowed
    win._wake_flash_until = 1.0  # expired flash with broken label
    win.wake_led = types.SimpleNamespace(
        setText=lambda *a: (_ for _ in ()).throw(RuntimeError("ui gone")))
    win._tick_meter()
    win.pipeline = real_pipe
    monkeypatch_ctx = real_pipe.context
    orig = monkeypatch_ctx.current
    monkeypatch_ctx.current = lambda: (_ for _ in ()).throw(RuntimeError("ctx gone"))
    try:
        win._tick_context()  # swallowed
    finally:
        monkeypatch_ctx.current = orig
    win.close()


def test_ui_wake_test_failure_and_speed(tmp_path, qapp, monkeypatch):
    import voicetype.ui_main as u
    win, store, pipe = _win(tmp_path)
    msgs: list = []
    monkeypatch.setattr(u.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: msgs.append(a)))
    pipe.wake.simulate_wake = lambda score=0.99: (_ for _ in ()).throw(
        RuntimeError("wake gone"))
    win._test_wake()
    assert msgs
    win.speed_slider.setValue(10)
    assert store.get("injection.type_delay_ms") == 10
    win.close()


def test_ui_vocab_add_duplicate_and_edit_cancel(tmp_path, qapp, monkeypatch):
    import voicetype.ui_main as u
    win, store, pipe = _win(tmp_path)
    store.set("vocabulary", ["dup"])
    win._reload_vocab_list()
    answers = iter([("dup", True)])
    monkeypatch.setattr(u.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: next(answers)))
    win._vocab_add()  # duplicate: list unchanged
    assert store.get("vocabulary") == ["dup"]
    answers = iter([("", False)])
    monkeypatch.setattr(u.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: next(answers)))
    win._vocab_edit()  # no selection: no-op
    win.close()


def test_ui_export_failures_and_perm_fix(tmp_path, qapp, monkeypatch):
    import voicetype.ui_main as u
    win, store, pipe = _win(tmp_path)
    msgs: list = []
    monkeypatch.setattr(u.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: msgs.append(a)))
    monkeypatch.setattr(u.QMessageBox, "information",
                        staticmethod(lambda *a, **k: msgs.append(a)))
    bad = str(tmp_path / "no-such-dir" / "v.txt")
    monkeypatch.setattr(u.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (bad, "")))
    win._vocab_export()  # write fails -> warning
    win._add_log("dictation", "x")
    win._export_logs()  # csv write fails -> warning
    assert len(msgs) >= 2
    other = next(k for k in win.perm_rows if k != "microphone")
    import shutil as _shutil
    import subprocess as _sp
    launched: list = []
    # Force the pkexec branch so the fix always shells out, on every OS.
    monkeypatch.setattr(_shutil, "which",
                        lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)
    monkeypatch.setattr(_sp, "run",
                        lambda *a, **k: launched.append(a[0]) or
                        types.SimpleNamespace(returncode=0))
    win._perm_fix(other)  # fix attempted, but subprocess is faked: OS untouched
    assert msgs and launched
    win.close()


def test_ui_event_exception_and_factory(tmp_path, qapp):
    from voicetype.ui_main import create_main_window
    win, store, pipe = _win(tmp_path)
    win._on_event("bogus", None)  # payload None -> swallowed
    win2 = create_main_window(store, pipe)
    assert win2 is not win
    win.close()
    win2.close()


# ---------- pairing/server/context/permissions/settings leftovers ----------
def test_pairing_default_dir_without_env(tmp_path, monkeypatch):
    import os
    from voicetype.companion.pairing import default_config_dir
    monkeypatch.delenv("VOICETYPE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home"))
    from pathlib import Path
    assert default_config_dir() == Path.home() / ".config" / "voicetype"


def test_server_break_on_closed_unauth(tmp_path):
    from tests.test_companion import FakePipeline
    from voicetype.companion.pairing import PairingStore
    from voicetype.companion.server import CompanionServer
    import asyncio as _aio
    import json as _json
    srv = CompanionServer(FakePipeline(), PairingStore(config_dir=tmp_path / "c"))

    class ClosedWS:
        closed = True
        sent: list = []

        def __aiter__(self):
            async def gen():
                yield _json.dumps({"type": "pair", "token": "bad"})
            return gen()

        async def send(self, data):
            self.sent.append(data)

        async def close(self):
            pass

    _aio.run(srv.handle(ClosedWS()))  # break path after refused auth


def test_context_mac_both_fail_and_junk_line(monkeypatch):
    import platform
    from voicetype import context_layer as cl
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setitem(sys.modules, "Quartz", None)
    assert cl.query_cursor_position() == {"x": None, "y": None}
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="X=3\nJUNK\nY=4\n", returncode=0))
    assert cl.query_cursor_position() == {"x": 3, "y": 4}


def test_permissions_winreg_raise_and_grp_raise(monkeypatch):
    import ctypes
    from voicetype import permissions as perm

    class U32:
        def GetForegroundWindow(self):
            return 5

    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=U32()),
                        raising=False)
    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER = "hkcu"
    mod.OpenKey = lambda *a, **k: (_ for _ in ()).throw(OSError("denied"))
    monkeypatch.setitem(sys.modules, "winreg", mod)
    assert "mic consent unknown" in perm.check_windows_uia().detail
    fake_grp = types.SimpleNamespace(
        getgrall=lambda: (_ for _ in ()).throw(OSError("nss")))
    monkeypatch.setitem(sys.modules, "grp", fake_grp)
    import os
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "access", lambda p, m: False)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert "groups=unknown" in perm.check_linux_uinput().detail


def test_settings_outer_except(tmp_path):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    mgr = ConfigManager(user_path=tmp_path / "s.yaml")
    store = SettingsStore(mgr, pipeline=types.SimpleNamespace())
    store.apply_live("injection.auto_punctuation", True)  # no .config: swallowed
    store.set("injection.auto_punctuation", True, save=False)


def test_transcriber_failure_surfaces_as_error():
    import numpy as np
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser

    class BrokenSTT:
        backend = "broken"

        def transcribe(self, audio, sample_rate=16000):
            raise OSError("c10_cuda.dll missing")

        def set_vocabulary(self, words):
            pass

    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=BrokenSTT(),
                         parser=FakeParser({"kind": "dictation", "text": "x"}),
                         context=FakeContext())
    events: list = []
    pipe.add_event_listener(lambda e, p: events.append(e))
    loud = (np.random.RandomState(9).randn(16000).astype(np.float32) * 0.3)
    out = pipe.handle_audio(loud)
    assert out["kind"] == "error" and "c10_cuda" in out["error"]
    assert pipe.injector.typed_log == []
    assert "error" in events


def test_ui_shows_error_event(tmp_path, qapp):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    store = SettingsStore(ConfigManager(user_path=tmp_path / "c.yaml"), pipe)
    win = MainWindow(store, pipe)
    win._on_event("error", {"text": "Transcription failed: boom"})
    assert "boom" in win.last_label.text()
    assert win.log_table.rowCount() == 1
    win.close()


def test_cuda_dll_bootstrap_registers_nvidia_bins(tmp_path, monkeypatch):
    import os
    import site as _site
    from voicetype import _dll_setup as dll
    bindir = tmp_path / "sp" / "nvidia" / "cublas" / "bin"
    bindir.mkdir(parents=True)
    monkeypatch.setattr(_site, "getsitepackages", lambda: [str(tmp_path / "sp")])
    monkeypatch.setattr(_site, "getusersitepackages",
                        lambda: (_ for _ in ()).throw(RuntimeError("no user site")))
    registered = dll.ensure_cuda_dlls()
    assert str(bindir) in registered  # plus any real nvidia dirs on sys.path
    assert str(bindir) in os.environ.get("PATH", "")
    assert str(bindir) in dll.nvidia_bin_dirs()
