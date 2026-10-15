"""100% push: app/audio/capture/discovery/config/context/permissions/pipeline."""
from __future__ import annotations

import subprocess
import sys
import types

import numpy as np
import pytest


# ---------- app ----------
def test_app_show_qr_without_png(tmp_path, monkeypatch, capsys):
    from voicetype import app as appmod
    from voicetype.companion.pairing import PairingStore
    store = PairingStore(config_dir=tmp_path / "comp")
    payload = store.pairing_payload("h", 8765)
    handles = {"payload": payload, "qr_png": b"", "tls_error": "",
               "advertiser": types.SimpleNamespace(ok=True)}
    monkeypatch.setattr("voicetype.companion.server.start_companion_services",
                        lambda *a, **k: handles)
    assert appmod.main(["--config", str(tmp_path / "c.yaml"), "--show-qr"]) == 0
    assert "pip install qrcode" in capsys.readouterr().out


def test_app_companion_no_tray_lifecycle(tmp_path, monkeypatch, capsys):
    from voicetype import app as appmod
    started: list = []
    handles = {"payload": None, "qr_png": b"", "tls_error": "",
               "advertiser": types.SimpleNamespace(ok=False, reason="no mDNS"),
               "server": types.SimpleNamespace(
                   serve_in_background=lambda: started.append(True))}
    monkeypatch.setattr("voicetype.companion.server.start_companion_services",
                        lambda *a, **k: handles)
    monkeypatch.setattr(appmod, "_run_forever", lambda: started.append("loop"))
    assert appmod.main(["--config", str(tmp_path / "c.yaml"),
                        "--companion", "--no-tray"]) == 0
    assert started == [True, "loop"]


# ---------- audio_ring ----------
def test_ring_partial_read_all():
    from voicetype.audio_ring import AudioRingBuffer
    rb = AudioRingBuffer(sample_rate=10, seconds=1.0)
    rb.write(np.arange(5, dtype=np.float32))
    assert list(rb.read_all()) == [0.0, 1.0, 2.0, 3.0, 4.0]


# ---------- capture ----------
def test_capture_preset_and_unshaped_names(monkeypatch):
    import sys
    from voicetype.audio_ring import AudioRingBuffer
    from voicetype.capture import AudioCapture
    cap = AudioCapture(AudioRingBuffer(seconds=1.0))
    cap.device_name = "Pinned Mic"
    assert cap.resolve_device_name() == "Pinned Mic"
    import sounddevice as _real  # noqa  (ensures module importable-or-not path)
    _ = _real
    cap2 = AudioCapture(AudioRingBuffer(seconds=1.0))
    fake = types.SimpleNamespace(query_devices=lambda *a, **k: {"nick": "no-name"})
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    assert cap2.resolve_device_name() == "(system default input)"
    fake2 = types.SimpleNamespace(query_devices=lambda *a, **k: ["not-a-dict"])
    monkeypatch.setitem(sys.modules, "sounddevice", fake2)
    cap3 = AudioCapture(AudioRingBuffer(seconds=1.0))
    assert cap3.resolve_device_name() == "(system default input)"
    assert cap3._query_native_rate() is None  # non-dict answer
    fake3 = types.SimpleNamespace(
        query_devices=lambda *a, **k: {"name": "X", "default_samplerate": "junk"})
    monkeypatch.setitem(sys.modules, "sounddevice", fake3)
    assert AudioCapture(AudioRingBuffer(seconds=1.0))._query_native_rate() is None
    fake4 = types.SimpleNamespace(
        query_devices=lambda *a, **k: {"name": "X", "default_samplerate": 0})
    monkeypatch.setitem(sys.modules, "sounddevice", fake4)
    assert AudioCapture(AudioRingBuffer(seconds=1.0))._query_native_rate() is None
    fake5 = types.SimpleNamespace(
        query_devices=lambda *a, **k: {"name": "X", "default_samplerate": 48000.0})
    monkeypatch.setitem(sys.modules, "sounddevice", fake5)
    assert AudioCapture(AudioRingBuffer(seconds=1.0))._query_native_rate() == 48000


# ---------- discovery ----------
def test_discovery_register_failure(monkeypatch):
    from voicetype.companion.discovery import advertise

    class FakeInfo:
        def __init__(self, *a, **k):
            pass

    class FakeZC:
        def register_service(self, info):
            raise RuntimeError("dbus down")

        def close(self):
            pass

    mod = types.SimpleNamespace(ServiceInfo=FakeInfo, Zeroconf=FakeZC)
    monkeypatch.setitem(sys.modules, "zeroconf", mod)
    adv = advertise(8765, "ab" * 32)
    assert adv.ok is False and "dbus down" in adv.reason


# ---------- config_manager ----------
def test_default_path_second_candidate(monkeypatch):
    from pathlib import Path
    import voicetype.config_manager as cm
    real = cm._default_config_path()
    monkeypatch.setattr(cm, "DEFAULT_CONFIG_PATHS",
                        [Path("/nonexistent-voicetype/x.yaml"), real])
    assert cm._default_config_path() == real
    monkeypatch.setattr(cm, "DEFAULT_CONFIG_PATHS",
                        [Path("/nonexistent-voicetype/x.yaml")] * 2)
    assert cm._default_config_path() == Path("/nonexistent-voicetype/x.yaml")


def test_auto_device_exception_falls_back(monkeypatch):
    import shutil
    import voicetype.config_manager as cm
    monkeypatch.delenv("VOICETYPE_DEVICE", raising=False)
    monkeypatch.setattr(shutil, "which",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no PATH")))
    assert cm._auto_device() == "cpu"


def test_corrupt_backup_write_failure(tmp_path, monkeypatch):
    from pathlib import Path
    from voicetype.config_manager import ConfigManager
    user = tmp_path / "c.yaml"
    user.write_text("wake_word: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(Path, "write_bytes",
                        lambda self, data: (_ for _ in ()).throw(OSError("ro")))
    mgr = ConfigManager(user_path=user)  # backup fails, defaults still win
    assert mgr.get("wake_word.threshold") == 0.5


# ---------- context_layer ----------
def _ctx_win(monkeypatch, open_result=0, text="App"):
    import ctypes

    class U32:
        def GetForegroundWindow(self):
            return 11

        def GetWindowTextLengthW(self, h):
            return len(text)

        def GetWindowTextW(self, h, buf, n):
            buf.value = text

        def GetWindowThreadProcessId(self, h, pid):
            return 3

    class K32:
        def OpenProcess(self, *a):
            return open_result

        def CloseHandle(self, h):
            pass

    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=U32(), kernel32=K32()),
                        raising=False)


def test_windows_exe_unresolved_and_broken(monkeypatch):
    import ctypes
    import platform
    from voicetype import context_layer as cl
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    _ctx_win(monkeypatch, open_result=0, text="Edit")
    out = cl._windows_active_app()
    assert out["title"] == "Edit" and out["bundle"] == "0"
    monkeypatch.delattr(ctypes, "windll", raising=False)
    assert cl._windows_active_app()["app"] == "unknown"


def test_mac_cursor_paths(monkeypatch):
    import platform
    from voicetype import context_layer as cl
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    mod = types.ModuleType("AppKit")
    mod.NSEvent = types.SimpleNamespace(
        mouseLocation=lambda: types.SimpleNamespace(x=9.7, y=2.2))
    monkeypatch.setitem(sys.modules, "AppKit", mod)
    assert cl.query_cursor_position() == {"x": 9, "y": 2}
    mod2 = types.ModuleType("AppKit")
    mod2.NSEvent = types.SimpleNamespace(
        mouseLocation=lambda: (_ for _ in ()).throw(RuntimeError("no event")))
    monkeypatch.setitem(sys.modules, "AppKit", mod2)
    qmod = types.ModuleType("Quartz")
    qmod.CGEventCreate = lambda _: "ev"
    qmod.CGEventGetLocation = lambda ev: types.SimpleNamespace(x=4.0, y=6.0)
    monkeypatch.setitem(sys.modules, "Quartz", qmod)
    assert cl.query_cursor_position() == {"x": 4, "y": 6}


def test_cursor_windows_denied_and_linux_rc(monkeypatch):
    import ctypes
    import platform
    from voicetype import context_layer as cl
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    class U32:
        def GetCursorPos(self, ref):
            return False

    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=U32()), raising=False)
    assert cl.query_cursor_position() == {"x": None, "y": None}
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="X=1\nGARBAGE-LINE\nY=2\n", returncode=1))
    assert cl.query_cursor_position() == {"x": None, "y": None}


def test_context_loop_refresh_raises(monkeypatch):
    import time
    from voicetype.context_layer import ContextLayer
    ctx = ContextLayer(poll_interval=0.01,
                       query_fn=lambda: {"app": "A", "title": "t"},
                       cursor_fn=lambda: {"x": 0, "y": 0})
    calls: list = []

    def boom():
        calls.append(1)
        raise RuntimeError("stale bus")

    monkeypatch.setattr(ctx, "refresh", boom)
    ctx.start()
    time.sleep(0.05)
    ctx.stop()
    assert calls  # loop survived repeated refresh failures


# ---------- permissions ----------
def test_mac_fix_without_prompt_fn(monkeypatch):
    import ctypes
    from voicetype import permissions as perm

    class AX:
        pass  # no AXIsProcessTrustedWithOptions attribute

    monkeypatch.setattr(ctypes, "cdll",
                        types.SimpleNamespace(LoadLibrary=lambda *a, **k: AX()))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0))
    assert perm.fix_mac_accessibility()[0] is True


def test_windows_uia_partial_failures(monkeypatch):
    import ctypes
    from voicetype import permissions as perm

    class U32Boom:
        def GetForegroundWindow(self):
            raise RuntimeError("denied")

    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=U32Boom()),
                        raising=False)
    st = perm.check_windows_uia()
    assert "GetForegroundWindow failed" in st.detail
    assert "mic consent" in st.detail  # "=Allow" on Windows, "unknown" elsewhere


def test_windows_uia_registry_paths(monkeypatch):
    import ctypes
    from voicetype import permissions as perm

    class U32:
        def GetForegroundWindow(self):
            return 5

    class Shell:
        def IsUserAnAdmin(self):
            return 1

    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=U32(), shell32=Shell()),
                        raising=False)

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    for value, ok in (("Allow", True), ("Deny", False)):
        mod = types.ModuleType("winreg")
        mod.HKEY_CURRENT_USER = "hkcu"
        mod.OpenKey = lambda *a, **k: Key()
        mod.QueryValueEx = lambda *a, **k: (value, 1)
        monkeypatch.setitem(sys.modules, "winreg", mod)
        st = perm.check_windows_uia()
        assert st.ok is ok, value
        assert f"mic consent={value}" in st.detail


def test_linux_grp_missing_and_fix_crash(monkeypatch):
    import os
    from voicetype import permissions as perm
    monkeypatch.setitem(sys.modules, "grp", None)
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "access", lambda p, m: False)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    st = perm.check_linux_uinput()
    assert "groups=unknown" in st.detail and st.ok is False
    monkeypatch.setattr(os, "access",
                        lambda p, m: (_ for _ in ()).throw(OSError("selinux")))
    ok, msg = perm.fix_linux_uinput()
    assert ok is False and "Fix failed" in msg


# ---------- pipeline ----------
def test_executor_injector_failure_reported():
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import CommandExecutor
    from tests.test_pipeline import FakeContext

    class Boom(FallbackInjector):
        def delete(self, *a, **k):
            raise RuntimeError("backend gone")

    res = CommandExecutor(Boom(), FakeContext()).execute(
        {"action": "delete", "target": "word", "value": "1"})
    assert res["ok"] is False and "backend gone" in res["error"]


def test_pipeline_start_stop_real(monkeypatch):
    import sys
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=FakeTranscriber("x"),
                         parser=FakeParser({"kind": "dictation", "text": "x"}),
                         context=FakeContext())
    started = pipe.start()
    assert started["capture"] == "synthetic" and "device" in started
    pipe.stop()


def test_record_quiet_break_and_empty_transcript():
    import numpy as np
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    cfg = load_config(overrides={"injection": {"backend": "fallback"},
                                 "utterance": {"end_silence_ms": 200,
                                               "pre_roll_ms": 100,
                                               "max_seconds": 30.0}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=FakeTranscriber(""),
                         parser=FakeParser({"kind": "dictation", "text": ""}),
                         context=FakeContext())
    rng = np.random.RandomState(7)
    pipe.ring.write((rng.randn(32000).astype(np.float32) * 0.004))
    audio = pipe.record_utterance()  # silence cutoff, not the time cap
    assert audio.size > 0
    assert pipe.handle_audio(audio)["kind"] == "empty"

    pipe2 = VoicePipeline(cfg, injector=FallbackInjector(),
                          transcriber=FakeTranscriber(""),
                          parser=FakeParser({"kind": "dictation", "text": ""}),
                          context=FakeContext())
    loud = (np.random.RandomState(8).randn(16000).astype(np.float32) * 0.3)
    assert pipe2.handle_audio(loud)["kind"] == "empty"  # empty transcript
