"""Extra coverage: permissions, store branches, UI dialogs, platform paths."""
from __future__ import annotations

import subprocess
import sys
import types

import numpy as np
import pytest

pytestmark_ui = pytest.mark.skipif(
    __import__("importlib").util.find_spec("PySide6") is None,
    reason="PySide6 not installed",
)


# ================= permissions =================
def test_mac_accessibility_trusted_and_prompt(monkeypatch):
    import ctypes
    from voicetype import permissions as perm

    class _TrustedFunc:
        def __init__(self, ret):
            self.ret = ret
            self.restype = None
            self.argtypes = None

        def __call__(self, *a, **k):
            return self.ret

    class FakeAX:
        def __init__(self):
            self.AXIsProcessTrusted = _TrustedFunc(True)
            self.AXIsProcessTrustedWithOptions = _TrustedFunc(True)

    monkeypatch.setattr(ctypes, "cdll",
                        types.SimpleNamespace(LoadLibrary=lambda *a, **k: FakeAX()))
    st = perm.check_mac_accessibility()
    assert st.ok is True and "trusted" in st.detail
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0))
    ok, msg = perm.fix_mac_accessibility()
    assert ok is True and "Accessibility" in msg


def test_mac_accessibility_untrusted_no_prompt_fn(monkeypatch):
    import ctypes
    from voicetype import permissions as perm

    class _UntrustedFunc:
        def __init__(self, ret=False):
            self.ret = ret
            self.restype = None

        def __call__(self, *a, **k):
            return self.ret

    class FakeAXNoPrompt:
        def __init__(self):
            self.AXIsProcessTrusted = _UntrustedFunc(False)

    monkeypatch.setattr(ctypes, "cdll",
                        types.SimpleNamespace(LoadLibrary=lambda *a, **k: FakeAXNoPrompt()))
    monkeypatch.setitem(sys.modules, "AppKit", None)
    st = perm.check_mac_accessibility()
    assert st.ok is False
    assert "Accessibility" in st.fix_hint


def test_mac_fix_failure(monkeypatch):
    from voicetype import permissions as perm
    import ctypes
    monkeypatch.setattr(ctypes, "cdll",
                        types.SimpleNamespace(LoadLibrary=lambda *a, **k: (_ for _ in ()).throw(
                            OSError("no framework"))))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no open")))
    ok, msg = perm.fix_mac_accessibility()
    assert ok is False


def test_mac_appkit_success_path(monkeypatch):
    import ctypes
    from voicetype import permissions as perm
    monkeypatch.setattr(ctypes, "cdll",
                        types.SimpleNamespace(LoadLibrary=lambda *a, **k: (_ for _ in ()).throw(
                            OSError("nope"))))
    app = types.SimpleNamespace(localizedName=lambda: "Safari",
                                bundleIdentifier=lambda: "com.apple.Safari")
    ws = types.SimpleNamespace(frontmostApplication=lambda: app,
                               sharedWorkspace=lambda: ws)
    mod = types.ModuleType("AppKit")
    mod.NSWorkspace = ws
    monkeypatch.setitem(sys.modules, "AppKit", mod)
    st = perm.check_mac_accessibility()
    assert "AppKit reachable" in st.detail


def test_windows_uia_success_and_fix(monkeypatch):
    import ctypes
    from ctypes import wintypes
    from voicetype import permissions as perm

    class FakeUser32:
        def GetForegroundWindow(self):
            return 123

        def GetWindowTextLengthW(self, hwnd):
            return 4

        def GetWindowTextW(self, hwnd, buf, n):
            buf.value = "Note"

        def GetWindowThreadProcessId(self, hwnd, pid):
            return 1

    class FakeShell:
        def IsUserAnAdmin(self):
            return 0

    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=FakeUser32(), shell32=FakeShell()),
                        raising=False)
    st = perm.check_windows_uia()
    assert st.ok is True and "foreground window handle=ok" in st.detail
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0))
    assert perm.fix_windows_uia()[0] is True
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert perm.fix_windows_uia()[0] is False


def test_linux_uinput_tool_matrix(monkeypatch):
    from voicetype import permissions as perm
    import os
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "access", lambda p, m: True)
    st = perm.check_linux_uinput()
    assert st.ok is True and "rw=True" in st.detail
    monkeypatch.setattr(os, "access", lambda p, m: False)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    st = perm.check_linux_uinput()
    assert st.ok is False


def test_linux_fix_branches(monkeypatch):
    from voicetype import permissions as perm
    import os
    monkeypatch.setattr(os, "access", lambda p, m: True)
    assert perm.fix_linux_uinput()[0] is True
    monkeypatch.setattr(os, "access", lambda p, m: False)
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/pkexec" if n == "pkexec" else None)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0))
    ok, msg = perm.fix_linux_uinput()
    assert "udev" in msg.lower() or "relogin" in msg.lower()
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    ok, msg = perm.fix_linux_uinput()
    assert ok is False and "sudo" in msg


def test_microphone_branches(monkeypatch):
    from voicetype import permissions as perm
    fake = types.SimpleNamespace(
        query_devices=lambda: [{"max_input_channels": 2}, {"max_input_channels": 0}])
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    assert perm.check_microphone().ok is True
    monkeypatch.setitem(sys.modules, "sounddevice",
                        types.SimpleNamespace(query_devices=lambda: []))
    assert perm.check_microphone().ok is False
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    st = perm.check_microphone()
    assert st.ok is True and "assuming ok" in st.detail


def test_check_all_per_os(monkeypatch):
    import platform
    from voicetype import permissions as perm
    for system in ("Darwin", "Windows", "Linux"):
        monkeypatch.setattr(platform, "system", lambda s=system: s)
        names = [s.name for s in perm.check_all()]
        assert "microphone" in names and len(names) == 2


# ================= settings store branches =================
def _store_with_fakes(tmp_path):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore

    class T:
        model_name = "m"
        vad_min_silence_ms = 1
        language = "en"
        batch_size = 1
        beam_size = 1
        vad_filter = True

        def set_vocabulary(self, words):
            if words == ["boom"]:
                raise RuntimeError("vocab")
            self.words = words

    class P:
        host = ""
        model = ""
        temperature = 0.0
        num_predict = 0
        enabled = True

    class I:
        delay_ms = 0
        chunk_size = 1
        use_clipboard_for_unicode = True
        undo_behavior = "single"

    class Pipe:
        def __init__(self):
            self.wake = types.SimpleNamespace(threshold=0.5)
            self.transcriber = T()
            self.parser = P()
            self.injector = I()
            self.end_silence_ms = 0
            self.config = {}

    mgr = ConfigManager(user_path=tmp_path / "s.yaml")
    pipe = Pipe()
    return SettingsStore(mgr, pipe), pipe


def test_store_listener_errors_swallowed(tmp_path):
    store, _ = _store_with_fakes(tmp_path)

    def bad(k, v):
        raise RuntimeError()

    seen: list = []
    store.on_change(bad)
    store.on_change(lambda k, v: seen.append((k, v)))
    store.set("stt.language", "de", save=False)
    store.set_many({"stt.beam_size": 7})
    assert ("stt.language", "de") in seen


def test_store_apply_all_branches(tmp_path):
    store, pipe = _store_with_fakes(tmp_path)
    store.apply_live("wake_word.phrase", "x")
    store.apply_live("wake_word.model_path", "y")
    for dotted, attr in [("stt.model", "model_name"), ("stt.vad_min_silence_ms", None),
                         ("stt.language", None), ("stt.batch_size", None),
                         ("stt.beam_size", None), ("stt.vad_filter", None)]:
        store.apply_live(dotted, 3 if "batch" in dotted or "beam" in dotted else "v")
    store.apply_live("stt.vad_aggressiveness", 1)
    assert pipe.transcriber.vad_min_silence_ms == 500
    store.apply_live("llm.host", "http://h:1/")
    assert pipe.parser.host == "http://h:1"
    store.apply_live("llm.model", "mm")
    store.apply_live("llm.temperature", 0.9)
    store.apply_live("llm.num_predict", 9)
    store.apply_live("llm.enabled", False)
    assert pipe.parser.enabled is False
    store.apply_live("injection.type_delay_ms", 5)
    store.apply_live("injection.type_chunk_size", 9)
    store.apply_live("injection.use_clipboard_for_unicode", False)
    store.apply_live("injection.undo_behavior", "weird")
    assert pipe.injector.undo_behavior == "single"
    store.apply_live("injection.auto_punctuation", True)
    store.apply_live("utterance.end_silence_ms", 700)
    assert pipe.end_silence_ms == 700
    store.apply_live("vocabulary", ["a"])
    assert pipe.transcriber.words == ["a"]
    store.apply_live("vocabulary", ["boom"])  # exception swallowed
    store.apply_live("unknown.branch", 1)  # no-op
    store2, _ = _store_with_fakes(tmp_path)
    store2.pipeline = None
    store2.apply_live("wake_word.threshold", 0.5)  # no pipeline -> no-op


def test_store_get_set_path_helpers_and_vocab_files(tmp_path):
    from voicetype.settings_store import _get_path, _set_path
    assert _get_path({"a": 1}, "a.b", "d") == "d"
    assert _get_path({"a": 1}, "a", "d") == 1
    cfg: dict = {"a": 5}
    _set_path(cfg, "a.b", 1)  # replaces non-dict mid with dict
    assert cfg == {"a": {"b": 1}}
    store, _ = _store_with_fakes(tmp_path)
    store.set("vocabulary", ["x", "y"])
    jp = store.export_vocabulary(tmp_path / "v.json")
    import json
    assert json.loads(jp.read_text()) == ["x", "y"]
    src = tmp_path / "in.json"
    src.write_text(json.dumps(["z"]), encoding="utf-8")
    assert store.import_vocabulary(src, merge=False) == ["z"]


# ================= UI dialog flows =================
@pytest.mark.skipif(__import__("importlib").util.find_spec("PySide6") is None,
                    reason="PySide6 not installed")
def test_vocab_dialog_crud(tmp_path, qapp, monkeypatch):
    import voicetype.ui_main as u
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    store = SettingsStore(ConfigManager(user_path=tmp_path / "c.yaml"), pipe)
    win = MainWindow(store, pipe)
    answers = iter([("newword", True)])
    monkeypatch.setattr(u.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: next(answers)))
    win._vocab_add()
    assert "newword" in store.get("vocabulary")
    win.vocab_list.setCurrentRow(0)
    answers = iter([("edited", True)])
    monkeypatch.setattr(u.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: next(answers)))
    win._vocab_edit()
    assert "edited" in store.get("vocabulary")
    win.vocab_list.setCurrentRow(0)
    win._vocab_delete()
    assert "edited" not in store.get("vocabulary")
    win._vocab_edit()  # no selection -> no-op
    win._vocab_delete()  # no selection -> no-op
    answers = iter([("", False)])
    monkeypatch.setattr(u.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: next(answers)))
    win._vocab_add()  # cancelled
    # import / export via file dialogs
    src = tmp_path / "words.txt"
    src.write_text("alpha\nbeta\n", encoding="utf-8")
    monkeypatch.setattr(u.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(src), "")))
    win._vocab_import()
    assert "alpha" in store.get("vocabulary")
    monkeypatch.setattr(u.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    win._vocab_import()  # cancelled
    monkeypatch.setattr(u.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "missing.txt"), "")))
    msgs: list = []
    monkeypatch.setattr(u.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: msgs.append(a)))
    win._vocab_import()  # read failure -> warning
    assert msgs
    monkeypatch.setattr(u.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "out.txt"), "")))
    win._vocab_export()
    assert (tmp_path / "out.txt").exists()
    monkeypatch.setattr(u.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    win._vocab_export()  # cancelled
    win.close()


@pytest.mark.skipif(__import__("importlib").util.find_spec("PySide6") is None,
                    reason="PySide6 not installed")
def test_ui_misc_handlers(tmp_path, qapp, monkeypatch):
    import voicetype.ui_main as u
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    store = SettingsStore(ConfigManager(user_path=tmp_path / "c.yaml"), pipe)
    win = MainWindow(store, pipe)
    win._tick_meter()
    win._tick_context()
    win._cmd_vocab_saved()
    assert store.get("llm.command_vocabulary") == []
    win.cmd_vocab.setText("scroll down, delete line")
    win._cmd_vocab_saved()
    assert store.get("llm.command_vocabulary") == ["scroll down", "delete line"]
    win.hot_edits["push_to_talk"].setKeySequence(__import__("PySide6.QtGui", fromlist=["QKeySequence"]).QKeySequence("Ctrl+A"))
    win._hotkey_saved("push_to_talk", win.hot_edits["push_to_talk"])
    infos: list = []
    monkeypatch.setattr(u.QMessageBox, "information",
                        staticmethod(lambda *a, **k: infos.append(a)))
    win._check_ollama()  # parser is a FakeParser in _silent_pipeline...
    win._perm_fix("microphone")
    assert infos
    win.log_search.setText("")
    win._filter_logs()
    monkeypatch.setattr(u.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "logs.csv"), "")))
    win._add_log("dictation", "row1")
    win._export_logs()
    assert (tmp_path / "logs.csv").exists()
    monkeypatch.setattr(u.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    win._export_logs()  # cancelled
    win._on_event("transcribed", {"text": "t"})
    win._on_event("started", {})
    win._on_event("bogus-event", {})
    win.close()


# ================= context success paths =================
def test_mac_appkit_query_success(monkeypatch):
    import platform
    from voicetype import context_layer as cl
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    app = types.SimpleNamespace(localizedName=lambda: "Term",
                                bundleIdentifier=lambda: "com.x")
    ws = types.SimpleNamespace(frontmostApplication=lambda: app,
                               sharedWorkspace=lambda: ws)
    mod = types.ModuleType("AppKit")
    mod.NSWorkspace = ws
    monkeypatch.setitem(sys.modules, "AppKit", mod)
    out = cl._mac_active_app()
    assert out == {"app": "Term", "bundle": "com.x", "title": "Term", "platform": "mac"}


def test_windows_query_success(monkeypatch):
    import ctypes
    import platform
    from voicetype import context_layer as cl
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    class FakeUser32:
        def GetForegroundWindow(self):
            return 9

        def GetWindowTextLengthW(self, h):
            return 4

        def GetWindowTextW(self, h, buf, n):
            buf.value = "Edit"

        def GetWindowThreadProcessId(self, h, pid):
            return 2

    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=FakeUser32()), raising=False)
    out = cl._windows_active_app()
    assert out["title"] == "Edit" and out["platform"] == "windows"


def test_linux_swaymsg_branch(monkeypatch):
    import platform
    from voicetype import context_layer as cl
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    def fake_run(cmd, **k):
        if cmd[0] == "xdotool":
            return types.SimpleNamespace(stdout="", returncode=1)
        return types.SimpleNamespace(stdout="sway-tree", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cl._linux_active_app()["title"] == "sway-tree"


def test_cursor_mac_and_windows(monkeypatch):
    import ctypes
    import platform
    from voicetype import context_layer as cl
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    mod = types.ModuleType("AppKit")
    mod.NSEvent = types.SimpleNamespace(mouseLocation=lambda: types.SimpleNamespace(x=3.7, y=4.2))
    monkeypatch.setitem(sys.modules, "AppKit", mod)
    assert cl.query_cursor_position() == {"x": 3, "y": 4}
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    class FakeUser32:
        def GetCursorPos(self, ref):
            ref._obj.x, ref._obj.y = 11, 22
            return True

    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=FakeUser32()), raising=False)
    assert cl.query_cursor_position() == {"x": 11, "y": 22}


# ================= injector extras =================
def test_injector_darwin_branches(monkeypatch):
    import platform
    from voicetype.injector import BaseInjector, FallbackInjector, get_injector
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    inj = FallbackInjector()
    inj.delete("line")
    inj.delete("word", "2")
    inj.undo()
    inj.redo()
    combos = [e[1] for e in inj.events_log if e[0] == "combo"]
    assert ("command", "z") in combos
    assert ("command", "shift", "z") in combos
    assert get_injector("uinput").name == "fallback"  # wrong OS
    # abstract bodies execute (Ellipsis) / raise as designed
    BaseInjector._type_ascii_chunk(inj, "x")
    BaseInjector._press_combo(inj, "x")
    BaseInjector._scroll_backend(inj, "down", 1)
    with pytest.raises(NotImplementedError):
        BaseInjector._clipboard_paste_impl(inj, "x")
    with pytest.raises(TypeError):
        BaseInjector()  # type: ignore


def test_uinput_evdev_full_path(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    from tests.test_injection import _install_fake_evdev, FakeEcodes
    from voicetype.injector import UInputInjector
    inst = _install_fake_evdev(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    inj = UInputInjector()
    inj.type_text("a~?")  # letters + shift chars + unmappable ->
    assert inst[0].syns > 0
    inj.press("ctrl", "a")
    inj.press("enter")
    inj.scroll("up", 2)  # no xdotool -> uinput REL_WHEEL
    assert any(w[1] == FakeEcodes.REL_WHEEL for w in inst[0].writes)
    assert UInputInjector._char_keycode("5", FakeEcodes) == FakeEcodes.KEY_5
    assert UInputInjector._char_keycode("@", FakeEcodes) is None


def test_uinput_press_wtype_branch(monkeypatch):
    import subprocess
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    from tests.test_injection import _install_fake_evdev
    from voicetype.injector import UInputInjector
    _install_fake_evdev(monkeypatch, fail_init=True)
    calls: list = []
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/wtype" if n == "wtype" else None)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or types.SimpleNamespace(returncode=0))
    UInputInjector().press("ctrl", "c")
    assert calls and calls[0][0] == "wtype"


def test_uinput_ydotool_and_wlcopy(monkeypatch):
    import subprocess
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    from tests.test_injection import _install_fake_evdev
    from voicetype.injector import UInputInjector
    _install_fake_evdev(monkeypatch, fail_init=True)
    calls: list = []
    monkeypatch.setattr("shutil.which",
                        lambda n: {"ydotool": "/y", "wl-copy": "/w"}.get(n))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or types.SimpleNamespace(returncode=0))
    UInputInjector().type_text("hi")
    assert calls[0][0] == "ydotool"
    assert UInputInjector()._clipboard_paste_impl("x") is True


def test_sendinput_press_single_char_key(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    from tests.test_injection import _install_fake_windll
    from voicetype.injector import SendInputInjector
    u32 = _install_fake_windll(monkeypatch)
    SendInputInjector().press("x")
    assert u32.batches == [2]


# ================= transcriber / wake / ring / theme / capture / pipeline =================
def test_transcriber_real_model_path_and_resample_edge(monkeypatch, speech_audio):
    import sys
    from voicetype.transcriber import Transcriber
    from tests.test_transcription import FakeWhisperModel
    made: list = []

    class WM(FakeWhisperModel):
        def __init__(self, name, device=None, compute_type=None):
            super().__init__(("real",))
            made.append((name, device, compute_type))

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WM))
    tr = Transcriber(model_name="small", device="cpu", compute_type="int8")
    assert tr.transcribe(speech_audio)["text"] == "real"
    assert tr.backend == "faster-whisper"
    assert made[0][0] == "small"
    tr2 = Transcriber(model_override=FakeWhisperModel(("e",)))
    assert tr2.transcribe(np.ones(1, dtype=np.float32), sample_rate=8000)["text"] == "e"


def test_wake_model_path_and_idle_trim(monkeypatch, tmp_path):
    import time
    from voicetype.wake import WakeWordDetector
    import sys
    seen: list = []
    mod = types.ModuleType("openwakeword.model")
    mod.Model = lambda **kw: seen.append(kw) or types.SimpleNamespace(
        predict=lambda f: {"w": 0.0})
    pkg = types.ModuleType("openwakeword")
    pkg.model = mod
    monkeypatch.setitem(sys.modules, "openwakeword", pkg)
    monkeypatch.setitem(sys.modules, "openwakeword.model", mod)
    model_file = tmp_path / "wake.onnx"
    model_file.write_bytes(b"fake")
    det = WakeWordDetector(model_path=str(model_file))
    assert det.backend == "openwakeword"
    assert seen[0]["wakeword_models"] == [str(model_file)]
    det2 = WakeWordDetector(model_name="")
    assert det2.backend == "fallback"
    # idle-loop trim: huge backlog trimmed on the single iteration
    det3 = WakeWordDetector(threshold=0.99)
    det3._pending = np.ones(16000 * 10, dtype=np.float32)
    monkeypatch.setattr(time, "sleep", lambda s: det3._stop.set())
    det3._stop.clear()
    det3._idle_loop()
    assert det3._pending.size == 16000 * 2


def test_audio_ring_edges():
    from voicetype.audio_ring import AudioRingBuffer
    with pytest.raises(ValueError):
        AudioRingBuffer(channels=2)
    rb = AudioRingBuffer(sample_rate=10, seconds=1.0)
    rb.write(np.zeros(0, dtype=np.float32))
    assert rb.read_latest(5).size == 0
    assert rb.read_all().size == 0
    rb.write(np.arange(10, dtype=np.float32))
    assert list(rb.read_all()) == [float(x) for x in range(10)]
    rb.write(np.arange(10, 13, dtype=np.float32))  # wrap
    assert list(rb.read_latest(5)) == [8.0, 9.0, 10.0, 11.0, 12.0]
    assert rb.capacity_samples == 10


def test_theme_hover_fallback_and_no_fusion(qapp, monkeypatch):
    from voicetype import theme as th
    assert th._hover("not-a-color") == "not-a-color"
    assert th.build_qss("") and "#4da6ff" in th.build_qss("")
    from PySide6.QtWidgets import QStyleFactory
    monkeypatch.setattr(QStyleFactory, "keys", lambda: ["Windows"])
    th.apply_theme(qapp, "#ff0000")  # no Fusion -> palette+QSS only


def test_capture_empty_callback_and_close_error(monkeypatch):
    import sys
    from voicetype.audio_ring import AudioRingBuffer
    from voicetype.capture import AudioCapture
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    cap = AudioCapture(AudioRingBuffer(seconds=1.0))
    cap._callback(np.zeros((0, 1), dtype=np.float32), 0, None, None)
    assert cap.stats.callbacks == 0

    class BadStream:
        def stop(self):
            raise RuntimeError("stuck")

        def close(self):
            raise RuntimeError("stuck")

    cap._stream = BadStream()
    cap.stop()  # exceptions swallowed
    assert cap._stream is None


def test_pipeline_emit_stop_and_record_edges():
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    cfg = load_config(overrides={"injection": {"backend": "fallback"},
                                 "utterance": {"end_silence_ms": 200, "max_seconds": 5.0}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=FakeTranscriber("hey"),
                         parser=FakeParser({"kind": "dictation", "text": "hey"}),
                         context=FakeContext())
    pipe.add_event_listener(lambda e, p: (_ for _ in ()).throw(RuntimeError()))
    pipe._emit("x", {})  # listener errors swallowed
    # record on empty ring: silence path returns pre-roll-less audio fast
    audio = pipe.record_utterance()
    assert isinstance(audio, np.ndarray)
    # nested stop failures swallowed
    pipe.capture.stop = lambda: (_ for _ in ()).throw(RuntimeError("cap"))
    pipe.wake.stop = lambda: (_ for _ in ()).throw(RuntimeError("wake"))
    pipe.context.stop = lambda: (_ for _ in ()).throw(RuntimeError("ctx"))
    pipe.stop()
    # scroll with exotic digit-like value hits the int() ValueError branch
    assert pipe.executor.execute({"action": "scroll", "target": "down",
                                  "value": "\u00b2"})["ok"] is True


def test_ollama_http_error_status_falls_back(monkeypatch):
    import requests
    from voicetype.ollama_parser import CommandParser

    class Bad:
        status_code = 500

        def raise_for_status(self):
            raise Exception("500")

        def json(self):
            return {}

    monkeypatch.setattr(requests, "post", lambda *a, **k: Bad())
    out = CommandParser().classify("scroll down")
    assert out.get("fallback") is True
