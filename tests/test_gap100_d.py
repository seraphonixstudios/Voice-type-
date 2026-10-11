"""100% mop-up: the last uncovered lines and arcs."""
from __future__ import annotations

import subprocess
import sys
import types

import pytest


def test_cgevent_shift_flag(monkeypatch, mock_platform_mac):
    import tests.test_injection as ti
    ti._install_fake_quartz(monkeypatch)
    from voicetype.injector import CGEventInjector
    CGEventInjector().press("shift", "x")  # line 325


def test_sendinput_alloc_always_zero(monkeypatch, mock_platform_windows):
    import ctypes
    from tests.test_injection import _install_fake_windll
    from voicetype.injector import SendInputInjector

    class K32Zero:
        def GlobalAlloc(self, f, n):
            return 0

    u32 = _install_fake_windll(monkeypatch)
    mem: dict = {}
    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=u32, kernel32=K32Zero()),
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


def test_uinput_type_tool_raises(monkeypatch, mock_platform_linux):
    import subprocess as sp
    import tests.test_injection as ti
    from voicetype.injector import UInputInjector
    ti._install_fake_evdev(monkeypatch, fail_init=True)
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/xdotool" if n == "xdotool" else None)
    monkeypatch.setattr(sp, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("dead")))
    UInputInjector().type_text("hi")  # _type_via_tool raises -> tracked
    from voicetype.injector import UInputInjector as U
    inj = U()
    inj.type_text("hi")
    assert inj.last_error and "uinput.tool" in inj.last_error


def test_uinput_press_single_char_continues(monkeypatch, mock_platform_linux):
    import tests.test_injection as ti
    from voicetype.injector import UInputInjector
    ti._install_fake_evdev(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    UInputInjector().press("5", "ctrl")  # single-char branch, loop continues
    UInputInjector().press("F1", "ctrl")  # unmapped key: no branch, loop continues


def test_ui_vocab_edit_cancel_with_selection(tmp_path, qapp, monkeypatch):
    import voicetype.ui_main as u
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    store = SettingsStore(ConfigManager(user_path=tmp_path / "c.yaml"), pipe)
    win = MainWindow(store, pipe)
    store.set("vocabulary", ["keep"])
    win._reload_vocab_list()
    win.vocab_list.setCurrentRow(0)
    answers = iter([("", False)])
    monkeypatch.setattr(u.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: next(answers)))
    win._vocab_edit()  # cancelled with selection: exits quietly
    assert store.get("vocabulary") == ["keep"]
    win.close()


def test_ui_event_real_exception(tmp_path, qapp):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    store = SettingsStore(ConfigManager(user_path=tmp_path / "c.yaml"), pipe)
    win = MainWindow(store, pipe)
    win._on_event("wake", None)  # payload None -> AttributeError -> swallowed
    win._on_event("listening", None)
    win.close()


def test_settings_all_injection_keys_and_unknown(tmp_path):
    from voicetype.config_manager import ConfigManager, load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from voicetype.settings_store import SettingsStore
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=FakeTranscriber("x"),
                         parser=FakeParser({"kind": "dictation", "text": "x"}),
                         context=FakeContext())
    mgr = ConfigManager(user_path=tmp_path / "s.yaml")
    store = SettingsStore(mgr, pipe)
    store.set("injection.type_delay_ms", 3, save=False)
    store.set("injection.type_chunk_size", 9, save=False)
    store.set("injection.use_clipboard_for_unicode", False, save=False)
    store.set("injection.undo_behavior", "bogus", save=False)
    assert pipe.injector.undo_behavior == "single"
    store.set("injection.auto_punctuation", True, save=False)
    store.set("injection.unknown_key", 1, save=False)
    store.set("stt.unknown_key", 1, save=False)
    store.set("llm.unknown_key", 1, save=False)
    store.set("utterance.end_silence_ms", 800, save=False)
    store.set("utterance.min_speech_fraction", 0.2, save=False)
    store.set("wake_word.threshold", 0.6, save=False)
    store.set("wake_word.trigger_frames", 4, save=False)
