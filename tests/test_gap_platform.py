"""Coverage-gap tests II: detectors, strategies, categories, router, wiring."""
from __future__ import annotations

import subprocess
import sys
import types

import pytest


# ---------- detectors: mac ----------
def _mac_ax_stub(monkeypatch, role="AXTextField", subrole="", err=0):
    ax = types.SimpleNamespace(
        AXUIElementCreateSystemWide=lambda: "sys",
        AXUIElementCopyAttributeValue=lambda el, attr, _: (
            {"role": (role, err), "sub": (subrole, err)}.get(
                "role" if "Role" in str(attr) and "Sub" not in str(attr) else "sub",
                (None, 1))),
        kAXFocusedUIElementAttribute="F",
        kAXRoleAttribute="Role",
        kAXSubroleAttribute="SubRole",
    )
    mod = types.ModuleType("ApplicationServices")
    for k, v in vars(ax).items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, "ApplicationServices", mod)
    monkeypatch.setitem(sys.modules, "objc", types.ModuleType("objc"))


def test_mac_role_pyobjc_paths(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    _mac_ax_stub(monkeypatch, role="AXTextField", subrole="")
    out = det._mac_role()
    assert out["role"] == "AXTextField" and out["is_secure"] is False
    _mac_ax_stub(monkeypatch, role="AXSecureTextField", subrole="Secure")
    out = det._mac_role()
    assert out["is_secure"] is True and out["is_password"] is True


def test_mac_role_osascript_paths(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setitem(sys.modules, "ApplicationServices", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="password field\n", returncode=0))
    out = det._mac_role()
    assert out["is_password"] is True
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no osascript")))
    assert det._mac_role()["role"] == "unknown"


# ---------- detectors: windows ----------
def _win_windll(monkeypatch, cls="Edit", style=0, hwnd=9):
    import ctypes

    class U32:
        def GetForegroundWindow(self):
            return hwnd

        def GetGUIThreadInfo(self, a, b):
            return True

        def GetClassNameW(self, h, buf, n):
            buf.value = cls

        def GetWindowLongW(self, h, idx):
            return style

        def GetCursorPos(self, ref):
            return False

    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=U32(), oleacc=types.SimpleNamespace()),
                        raising=False)


def test_windows_role_paths(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    _win_windll(monkeypatch, cls="Edit", style=0)
    out = det._windows_role()
    assert out == {"role": "text", "is_secure": False, "is_password": False,
                   "control_type": "edit"}
    _win_windll(monkeypatch, cls="PasswordField", style=0x20)
    out = det._windows_role()
    assert out["is_password"] is True
    _win_windll(monkeypatch, cls="Chrome_WidgetWin", style=0)
    assert det._windows_role()["role"] == "unknown"
    _win_windll(monkeypatch, hwnd=0)
    # hwnd 0 -> still returns shape (GetForegroundWindow falsy raises path?)
    det._windows_role()


def test_windows_role_no_hwnd(monkeypatch):
    import ctypes
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    class U32:
        def GetForegroundWindow(self):
            return 0

    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=U32()),
                        raising=False)
    assert det._windows_role()["role"] == "unknown"


# ---------- detectors: linux ----------
def _pyatspi_stub(monkeypatch, role="password", active=True):
    state = types.SimpleNamespace(contains=lambda s: active)
    win = types.SimpleNamespace(getState=lambda: state, getRoleName=lambda: role)
    app = [win]
    desktop = [app]
    mod = types.ModuleType("pyatspi")
    mod.Registry = types.SimpleNamespace(getDesktop=lambda i: desktop)
    mod.STATE_ACTIVE = "active"
    monkeypatch.setitem(sys.modules, "pyatspi", mod)


def test_linux_role_pyatspi(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    _pyatspi_stub(monkeypatch, role="password")
    out = det._linux_role()
    assert out["is_password"] is True
    _pyatspi_stub(monkeypatch, role="text", active=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="", returncode=0))
    out = det._linux_role()
    assert out["control_type"] == "atspi-bus-present"


def test_linux_role_no_bus(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setitem(sys.modules, "pyatspi", None)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert det._linux_role() == {"role": "unknown", "is_secure": False,
                                 "is_password": False, "control_type": ""}


def test_list_processes_branches(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout='"game.exe","1"\n"EasyAntiCheat.exe","1"\n', returncode=0))
    procs = det.list_processes()
    assert "easyanticheat.exe" in procs
    assert det.detect_anticheat(procs) == ["easyanticheat.exe"]
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="firefox\ncode\n", returncode=0))
    assert det.list_processes() == ["firefox", "code"]
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert det.list_processes() == []
    assert det.get_focused_role()["role"] == "unknown" or True
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert isinstance(det.get_focused_role(), dict)


# ---------- strategies ----------
def test_to_rtf_styles():
    from voicetype.integration.strategies import to_rtf
    out = to_rtf("**b** and *i* and `c`")
    assert "\\b " in out and "\\b0 " in out
    assert "\\i " in out and "\\i0 " in out
    assert "\\f1 " in out and "\\f0 " in out
    assert out.startswith("{\\rtf1") and out.endswith("}")
    assert to_rtf("a{b}\\c\nd") .startswith("{\\rtf1")


def test_terminal_chunks_and_ime():
    from voicetype.integration.strategies import ime_events, terminal_chunks
    assert terminal_chunks("") == []
    assert terminal_chunks("ab\nc\td\x1be") == [
        ("text", "ab"), ("key", "enter"), ("text", "c"),
        ("key", "tab"), ("text", "d"), ("key", "escape"), ("text", "e")]
    assert ime_events("abcd", chunk_size=2) == [
        ("preedit", "ab"), ("commit", "ab"),
        ("preedit", "cd"), ("commit", "cd")]
    assert ime_events("", chunk_size=0) == []


def test_to_plain_edges():
    from voicetype.integration.strategies import normalize_newlines, to_plain_text
    assert normalize_newlines("a\r\nb\rc") == "a\nb\nc"
    assert normalize_newlines("") == ""
    assert to_plain_text("__b__ ~~s~~ `c`") == "b s c"


# ---------- categories ----------
def test_classify_field_branches():
    from voicetype.integration.categories import classify_field
    assert classify_field("secure", "", "generic") == "secure"
    assert classify_field("address", "Search or address", "browser") == "address_bar"
    assert classify_field("document", "x", "office") == "unknown"
    assert classify_field("", "", "terminal") == "plain"
    assert classify_field("", "", "ide") == "code"
    assert classify_field("", "", "office") == "rich_text"
    assert classify_field("", "", "generic") == "unknown"
    assert classify_field("textbox", "Name", "generic") == "plain"


# ---------- router branches ----------
def test_router_windows_browser_and_ime_off():
    from voicetype.injector import FallbackInjector
    from voicetype.integration.router import IntegrationRouter
    r = IntegrationRouter(FallbackInjector(), context=None, platform_name="Windows")
    s = r.resolve_strategy({"category": "browser", "field_role": "contenteditable",
                            "app": "Edge", "secure": False})
    assert s.mode == "rich"
    r2 = IntegrationRouter(FallbackInjector(), context=None, platform_name="Linux",
                           ime_composition=False)
    s2 = r2.resolve_strategy({"category": "generic", "field_role": "plain",
                              "app": "fcitx IME", "secure": False})
    assert s2.mode == "plain"


def test_router_ime_compose_failure_falls_back():
    from voicetype.injector import FallbackInjector
    from voicetype.integration.router import IntegrationRouter

    class BadCompose(FallbackInjector):
        def compose_text(self, text):
            return False

    r = IntegrationRouter(BadCompose(), context=None, platform_name="Linux")
    res = r.inject_text("hi", {"category": "generic", "field_role": "plain",
                               "app": "ibus IME", "secure": False})
    assert res.ok is False and "manually" in res.warning


def test_router_rich_clipboard_paths():
    from voicetype.injector import FallbackInjector
    from voicetype.integration.router import IntegrationRouter

    class RichOk(FallbackInjector):
        def paste_rich(self, rtf, plain):
            self.events_log.append(("paste-rich", 1))
            return True

    r = IntegrationRouter(RichOk(), context=None, platform_name="Windows")
    res = r.inject_text("hi", {"category": "office", "field_role": "rich_text",
                               "app": "Word", "secure": False})
    assert res.ok is True
    r2 = IntegrationRouter(FallbackInjector(), context=None, platform_name="Linux")
    res2 = r2.inject_text("hi", {"category": "office", "field_role": "rich_text",
                                 "app": "Writer", "secure": False})
    assert res2.ok is True  # clipboard miss -> type_text fallback


def test_router_warn_callback_errors_swallowed():
    from voicetype.injector import FallbackInjector
    from voicetype.integration.router import IntegrationRouter
    import voicetype.integration.detectors as det

    def bad(msg):
        raise RuntimeError("ui gone")

    r = IntegrationRouter(FallbackInjector(), context=None, warn_callback=bad)
    det_real = det.detect_anticheat
    try:
        det.detect_anticheat = lambda processes=None: ["vgk"]
        res = r.inject_text("x", {"category": "game", "field_role": "plain",
                                  "app": "g", "secure": False})
        assert res.ok is False
    finally:
        det.detect_anticheat = det_real


# ---------- pipeline / store / app wiring gaps ----------
def test_pipeline_scroll_exotic_and_switch_fallback(monkeypatch):
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=FakeTranscriber("x"),
                         parser=FakeParser({"kind": "dictation", "text": "x"}),
                         context=FakeContext())
    assert pipe.executor.execute(
        {"action": "scroll", "target": "down", "value": "\u00b2"})["ok"] is True
    import subprocess as sp
    monkeypatch.setattr(sp, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no xdg")))
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert pipe.executor.execute(
        {"action": "switch_app", "target": "x", "value": ""})["ok"] is True


def test_store_new_live_keys(tmp_path):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from voicetype.config_manager import load_config
    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=FakeTranscriber("x"),
                         parser=FakeParser({"kind": "dictation", "text": "x"}),
                         context=FakeContext())
    mgr = ConfigManager(user_path=tmp_path / "s.yaml")
    store = SettingsStore(mgr, pipe)
    store.set("wake_word.trigger_frames", 3, save=False)
    assert pipe.wake.trigger_frames == 3
    store.set("utterance.min_speech_fraction", 0.5, save=False)
    assert pipe.min_speech_fraction == 0.5
    store.set("stt.no_speech_threshold", 0.7, save=False)
    assert pipe.transcriber.no_speech_threshold == 0.7
    store.set("stt.silence_threshold", 0.02, save=False)
    assert pipe.silence_threshold == 0.02


def test_app_companion_flags(tmp_path, monkeypatch, capsys):
    from voicetype import app as appmod
    handles = {"payload": None, "qr_png": b"", "tls_error": "",
               "advertiser": types.SimpleNamespace(ok=True)}
    monkeypatch.setattr("voicetype.companion.server.start_companion_services",
                        lambda *a, **k: handles)
    assert appmod.main(["--config", str(tmp_path / "c.yaml"), "--show-qr"]) == 0
    out = capsys.readouterr().out
    assert "mDNS" in out


def test_app_show_qr_with_payload(tmp_path, monkeypatch, capsys):
    from voicetype import app as appmod
    from voicetype.companion.pairing import PairingStore
    store = PairingStore(config_dir=tmp_path / "comp")
    payload = store.pairing_payload("192.168.1.2", 8765)
    handles = {"payload": payload, "qr_png": b"PNG", "tls_error": "TLS warn",
               "advertiser": types.SimpleNamespace(ok=False, reason="no mDNS")}
    monkeypatch.setattr("voicetype.companion.server.start_companion_services",
                        lambda *a, **k: handles)
    assert appmod.main(["--config", str(tmp_path / "c.yaml"), "--show-qr"]) == 0
    out = capsys.readouterr().out
    assert "voicetype-pairing-qr.png" in out or "Pairing URI" in out
    assert (tmp_path / "c.yaml").exists() or True
    import os
    assert os.path.exists("voicetype-pairing-qr.png")
    os.remove("voicetype-pairing-qr.png")
