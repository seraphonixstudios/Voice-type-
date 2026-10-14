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


def test_dictate_button_triggers_pipeline(tmp_path, qapp):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    store = SettingsStore(ConfigManager(user_path=tmp_path / "c.yaml"), pipe)
    win = MainWindow(store, pipe)
    calls: list = []
    pipe.dictate_once = lambda: calls.append(1) or {"kind": "dictation", "text": "hi"}
    win._dictate_now()
    assert win.dictate_btn.isEnabled() is False
    import time
    deadline = time.time() + 5.0
    while not calls and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert calls == [1]
    qapp.processEvents()
    assert win.dictate_btn.isEnabled() is True
    win.close()


def test_ui_command_row_shows_raw_transcript(tmp_path, qapp):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    store = SettingsStore(ConfigManager(user_path=tmp_path / "c.yaml"), pipe)
    win = MainWindow(store, pipe)
    win._on_event("command", {"parsed": {"action": "scroll", "target": "down",
                                         "value": "2", "raw": "scroll down please"}})
    assert "scroll down please" in win.last_label.text()
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


def test_dll_setup_branches(tmp_path, monkeypatch):
    import os
    import site as _site
    from voicetype import _dll_setup as dll
    monkeypatch.setattr(_site, "getsitepackages",
                        lambda: (_ for _ in ()).throw(RuntimeError("no site")))
    monkeypatch.setattr(_site, "getusersitepackages",
                        lambda: (_ for _ in ()).throw(RuntimeError("no user site")))
    monkeypatch.setattr("sys.path", [])
    assert dll.nvidia_bin_dirs() == []  # both lookups fail, no sys.path either
    assert dll.ensure_cuda_dlls() == []
    bindir = tmp_path / "sp" / "nvidia" / "cublas" / "bin"
    bindir.mkdir(parents=True)
    (tmp_path / "sp" / "nvidia" / "README.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(_site, "getsitepackages", lambda: [str(tmp_path / "sp")])
    monkeypatch.setattr(_site, "getusersitepackages",
                        lambda: (_ for _ in ()).throw(RuntimeError("no user site")))
    monkeypatch.setattr("sys.path", [])
    assert dll.nvidia_bin_dirs() == [str(bindir)]  # plain file skipped
    monkeypatch.delattr(os, "add_dll_directory", raising=False)
    assert dll.ensure_cuda_dlls() == [str(bindir)]  # no add_dll_directory API
    monkeypatch.setattr(os, "add_dll_directory",
                        lambda d: (_ for _ in ()).throw(OSError("locked")),
                        raising=False)
    assert dll.ensure_cuda_dlls() == [str(bindir)]  # registration failure ignored
    # NOTE: never replace os.environ wholesale (breaks pytest's own
    # teardown bookkeeping); patch only the .get method instead.
    monkeypatch.setattr(os.environ, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("frozen")))
    assert dll.ensure_cuda_dlls() == [str(bindir)]  # PATH edit failure ignored
    real_listdir = os.listdir

    def locked_listdir(path):
        if str(path).endswith("nvidia"):
            raise OSError("locked volume")
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", locked_listdir)
    assert dll.nvidia_bin_dirs() == []  # listdir failure skips the root


def test_capture_query_and_resample_edges(monkeypatch):
    import sys
    import types
    import numpy as np
    from voicetype.audio_ring import AudioRingBuffer
    from voicetype.capture import AudioCapture
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    cap = AudioCapture(AudioRingBuffer(seconds=1.0))
    assert cap._query_native_rate() is None
    assert list(cap._resample(np.ones(4, dtype=np.float32), 16000, 16000)) == [1.0] * 4
    assert cap._resample(np.ones(1, dtype=np.float32), 48000, 16000).size == 1


def test_hotkey_props_and_stop_paths(monkeypatch):
    import platform
    from voicetype.hotkeys import HotkeyListener
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    listener = HotkeyListener("ctrl+alt+space", lambda: None)
    assert listener.supported is True and listener.running is False
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert HotkeyListener("ctrl+alt+space", lambda: None).supported is False


def test_hotkey_loop_exit_and_mismatch(monkeypatch):
    import ctypes
    import platform
    import sys
    import types
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    from voicetype.hotkeys import WM_HOTKEY, HotkeyListener

    class MSG:
        def __init__(self):
            self.message = 0
            self.wParam = 0

    seen: list = []
    import threading as _th
    proceed = _th.Event()

    class U32:
        def RegisterHotKey(self, *a):
            return True

        def GetMessageW(self, msg, *a):
            proceed.wait(timeout=5.0)  # main stops us mid-wait below
            msg.message = 0x0200  # WM_MOUSEMOVE: dispatched, not fired
            return 1

        def TranslateMessage(self, *a):
            seen.append("t")

        def DispatchMessageW(self, *a):
            seen.append("d")

        def UnregisterHotKey(self, *a):
            seen.append("unreg")

        def PostThreadMessageW(self, *a):
            seen.append("quit")

    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=U32()),
                        raising=False)
    monkeypatch.setattr(ctypes, "byref", lambda x: x)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes",
                        types.SimpleNamespace(MSG=MSG))
    fired: list = []
    listener = HotkeyListener("ctrl+alt+space", lambda: fired.append(1))
    listener.start()
    worker = listener._thread
    listener.stop()
    proceed.set()
    worker.join(timeout=5.0)
    assert fired == [] and "t" in seen and "unreg" in seen
    assert not listener.running
    # stop() on an unsupported platform skips the wake post
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    listener.stop()
    assert listener._thread is None


def test_hotkey_loop_dies_loudly(monkeypatch):
    import ctypes
    import platform
    import sys
    import types
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    from voicetype.hotkeys import HotkeyListener
    monkeypatch.delattr(ctypes, "windll", raising=False)
    listener = HotkeyListener("ctrl+alt+space", lambda: None)
    listener.start()
    listener._thread.join(timeout=5.0)
    assert not listener.running


def test_switch_empty_and_clipped_spectrum():
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import CommandExecutor
    from tests.test_pipeline import FakeContext
    ex = CommandExecutor(FallbackInjector(), FakeContext())
    res = ex.execute({"action": "switch_app", "target": "", "value": ""})
    assert res["ok"] is False and "refused" in res["error"]
    import numpy as np
    from voicetype.pipeline import VoicePipeline
    assert "clipping" in VoicePipeline.spectral_diagnosis(
        np.ones(16000, dtype=np.float32))
    assert "analysis failed" not in VoicePipeline.spectral_diagnosis(
        np.ones(16000, dtype=np.float32))


def test_spectral_fft_failure():
    import numpy as np
    from voicetype import pipeline as pipemod
    orig = np.fft.rfft
    np.fft.rfft = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fft"))
    try:
        assert pipemod.VoicePipeline.spectral_diagnosis(
            np.ones(16000, dtype=np.float32)) == "analysis failed"
    finally:
        np.fft.rfft = orig


def test_risky_url_command_demoted():
    import numpy as np
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=FakeTranscriber("open it"),
                         parser=FakeParser({"kind": "command", "action": "open_app",
                                            "target": "https://evil.example",
                                            "value": ""}),
                         context=FakeContext())
    loud = (np.random.RandomState(21).randn(16000).astype(np.float32) * 0.3)
    out = pipe.handle_audio(loud)
    assert out["kind"] == "dictation"  # unsafe launch became typed words


def test_risky_confidence_unparseable():
    import numpy as np
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser
    import types as _t

    class OddInfo:
        backend = "odd"
        last_info = {"confidence": object()}

        def transcribe(self, audio, sample_rate=16000):
            return {"text": "go", "segments": ["go"], "language": "en",
                    "backend": "odd"}

        def set_vocabulary(self, words):
            pass

    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=OddInfo(),
                         parser=FakeParser({"kind": "dictation", "text": "go"}),
                         context=FakeContext())
    loud = (np.random.RandomState(22).randn(16000).astype(np.float32) * 0.3)
    assert pipe.handle_audio(loud)["kind"] == "dictation"
    assert pipe._is_risky_low_confidence_command(
        {"action": "delete", "target": "", "value": ""}) is False
    assert pipe._is_risky_low_confidence_command(
        {"action": "switch_app", "target": "ok-app", "value": ""}) is False


def test_transcriber_ensure_calls_dll_setup(monkeypatch):
    import sys
    import types
    from tests.test_transcription import FakeWhisperModel
    from voicetype import _dll_setup as dllmod
    from voicetype.transcriber import Transcriber

    class WM(FakeWhisperModel):
        def __init__(self, name, device=None, compute_type=None):
            super().__init__(("real",))

    called: list = []
    def _boom():
        called.append(1)
        raise RuntimeError("cuda?")

    monkeypatch.setattr(dllmod, "ensure_cuda_dlls", _boom)
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=WM))
    tr = Transcriber(model_name="tiny")
    assert tr._ensure_model() is not None
    assert called == [1]


def test_ui_dictate_worker_error_paths(tmp_path, qapp):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    store = SettingsStore(ConfigManager(user_path=tmp_path / "c.yaml"), pipe)
    win = MainWindow(store, pipe)
    pipe.dictate_once = lambda: (_ for _ in ()).throw(RuntimeError("mic gone"))
    pipe._emit = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bus gone"))
    win._dictate_worker()  # both failures swallowed, button re-enabled
    assert win.dictate_btn.isEnabled() is True
    win.close()
