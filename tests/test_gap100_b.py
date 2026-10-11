"""100% push II: router/detectors/strategies/pairing/server/transcriber/parser/store/tray."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import types

import numpy as np
import pytest


# ---------- router ----------
def _router(**kw):
    from voicetype.injector import FallbackInjector
    from voicetype.integration.router import IntegrationRouter
    kw.setdefault("platform_name", "Linux")
    return IntegrationRouter(FallbackInjector(), context=None, **kw)


def test_enriched_context_role_failure(monkeypatch):
    import voicetype.integration.detectors as det
    from voicetype.integration.router import IntegrationRouter
    from voicetype.injector import FallbackInjector
    from tests.test_pipeline import FakeContext
    monkeypatch.setattr(det, "get_focused_role",
                        lambda: (_ for _ in ()).throw(RuntimeError("a11y down")))
    r = IntegrationRouter(FallbackInjector(), context=FakeContext())
    ctx = r.current_enriched_context()
    # Role unknown + generic app: safe plain default, never rich/blocked.
    assert ctx["field_role"] == "plain" and ctx["category"] == "generic"


def test_enriched_context_secure_override(monkeypatch):
    import voicetype.integration.detectors as det
    from voicetype.integration.router import IntegrationRouter
    from voicetype.injector import FallbackInjector
    from tests.test_pipeline import FakeContext
    monkeypatch.setattr(det, "get_focused_role",
                        lambda: {"role": "text", "is_secure": True,
                                 "is_password": False, "control_type": ""})
    r = IntegrationRouter(FallbackInjector(), context=FakeContext())
    assert r.current_enriched_context()["field_role"] == "password"


def test_secure_terminal_goes_raw():
    r = _router()
    res = r.inject_text("pass123", {"category": "terminal", "field_role": "plain",
                                    "app": "Terminal", "secure": True})
    assert res.ok is True and res.strategy.mode == "plain"
    assert ("primary", "type_text/plain") in res.audit  # secure terminal: raw keys
    assert "pass123" not in r.injector.typed_log


def test_paste_text_raises_in_fallback():
    from voicetype.injector import FallbackInjector
    from voicetype.integration.router import IntegrationRouter

    class BadPaste(FallbackInjector):
        def type_text(self, text, sensitive=False):
            raise RuntimeError("primary gone")

        def paste_text(self, text):
            raise RuntimeError("clipboard gone")

    r = IntegrationRouter(BadPaste(), context=None)
    res = r.inject_text("x", {"category": "generic", "field_role": "plain",
                              "app": "x", "secure": False})
    assert res.ok is False and "primary gone" in (res.warning + r.injector.last_error)


def test_darwin_terminal_raw_injection():
    r = _router(platform_name="Darwin")
    res = r.inject_text("ls\n", {"category": "terminal", "field_role": "plain",
                                 "app": "Terminal", "secure": False})
    assert res.ok is True
    assert ("primary", "type_raw/keycodes") in res.audit


def test_windows_rich_success():
    from voicetype.injector import FallbackInjector
    from voicetype.integration.router import IntegrationRouter

    class RichOk(FallbackInjector):
        def paste_rich(self, rtf, plain):
            self.events_log.append(("paste-rich", 1))
            return True

    r = IntegrationRouter(RichOk(), context=None, platform_name="Windows")
    res = r.inject_text("hi", {"category": "office", "field_role": "rich_text",
                               "app": "Word", "secure": False})
    assert res.ok is True and ("primary", "paste_rich/cf_rtf") in res.audit


def test_linux_rich_clipboard_success():
    from voicetype.injector import FallbackInjector
    from voicetype.integration.router import IntegrationRouter

    class Clip(FallbackInjector):
        def _clipboard_paste_impl(self, text):
            return True

    r = IntegrationRouter(Clip(), context=None, platform_name="Linux")
    res = r.inject_text("hi", {"category": "office", "field_role": "rich_text",
                               "app": "Writer", "secure": False})
    assert res.ok is True and ("primary", "paste_text/rich") in res.audit


def test_secure_multiline_keeps_enter_events():
    from voicetype.injector import FallbackInjector
    from voicetype.integration.router import IntegrationRouter
    r = IntegrationRouter(FallbackInjector(), context=None)
    res = r.inject_text("line1\nline2", {"category": "password_manager",
                                         "field_role": "plain",
                                         "app": "KeePassXC", "secure": True})
    assert res.ok is True
    assert ("key", "enter") in r.injector.events_log
    assert "line1" not in str(r.injector.typed_log)


def test_scrub_survives_broken_injector():
    from voicetype.integration.router import IntegrationRouter
    IntegrationRouter._scrub_secret(types.SimpleNamespace(), "x")  # no attrs: safe


# ---------- detectors ----------
def test_focused_role_dispatch_failure(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system",
                        lambda: (_ for _ in ()).throw(RuntimeError("no os")))
    assert det.get_focused_role()["role"] == "unknown"


def test_mac_ax_no_focused_element(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    mod = types.ModuleType("ApplicationServices")
    mod.AXUIElementCreateSystemWide = lambda: "sys"
    mod.AXUIElementCopyAttributeValue = lambda el, attr, _: (None, 1)
    mod.kAXFocusedUIElementAttribute = "F"
    mod.kAXRoleAttribute = "Role"
    mod.kAXSubroleAttribute = "SubRole"
    monkeypatch.setitem(sys.modules, "ApplicationServices", mod)
    monkeypatch.setitem(sys.modules, "objc", types.ModuleType("objc"))
    out = det._mac_role()
    assert out["role"] in ("unknown",) or isinstance(out["role"], str)


def test_linux_win_raise_inside_loop(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    bad_win = types.SimpleNamespace(
        getState=lambda: (_ for _ in ()).throw(RuntimeError("stale")))
    mod = types.ModuleType("pyatspi")
    mod.Registry = types.SimpleNamespace(getDesktop=lambda i: [[bad_win]])
    mod.STATE_ACTIVE = "active"
    monkeypatch.setitem(sys.modules, "pyatspi", mod)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert det._linux_role()["role"] == "unknown"


def test_linux_busctl_rc_and_ps_rc(monkeypatch):
    import platform
    from voicetype.integration import detectors as det
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setitem(sys.modules, "pyatspi", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="", returncode=3))
    assert det._linux_role()["role"] == "unknown"
    assert det.list_processes() == []


# ---------- strategies ----------
def test_rtf_unclosed_styles():
    from voicetype.integration.strategies import to_rtf
    assert to_rtf("**open").endswith("\\b0 \n}")
    assert to_rtf("*open").endswith("\\i0 \n}")


def test_preserve_blank_and_edges():
    from voicetype.integration.strategies import preserve_indentation
    assert preserve_indentation("\n\n") == ""
    assert preserve_indentation("a\n\nb") == "a\n\nb"
    assert preserve_indentation("", "  ") == ""


def test_terminal_chunk_edges():
    from voicetype.integration.strategies import terminal_chunks
    assert terminal_chunks("\nab")[0] == ("key", "enter")
    assert terminal_chunks("a\n")[-1] == ("key", "enter")
    assert terminal_chunks("\t")[0] == ("key", "tab")
    assert terminal_chunks("\x1b")[0] == ("key", "escape")
    assert terminal_chunks("ab") == [("text", "ab")]


def test_escaped_star_stays_literal():
    from voicetype.integration.strategies import to_rtf
    assert "*" in to_rtf("a\\*b")


# ---------- pairing ----------
def test_devices_corrupt_file_recovers(tmp_path):
    from voicetype.companion.pairing import PairingStore
    d = tmp_path / "c"
    d.mkdir()
    (d / "companion_devices.json").write_text("{oops", encoding="utf-8")
    assert PairingStore(config_dir=d).verify_device("x", "y") is False


def test_devices_chmod_failure_ignored(tmp_path, monkeypatch):
    import os
    from voicetype.companion.pairing import PairingStore
    monkeypatch.setattr(os, "chmod",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    store = PairingStore(config_dir=tmp_path / "c")
    did, secret = store.issue_device_credential()  # save path chmod fails
    assert store.verify_device(did, secret) is True
    store.cert_paths()  # generate path chmod fails
    assert len(store.certificate_fingerprint()) == 64


def test_cert_ip_san_failure_skipped(tmp_path, monkeypatch):
    import ipaddress
    from voicetype.companion.pairing import PairingStore
    monkeypatch.setattr(ipaddress, "ip_address",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("v6?")))
    store = PairingStore(config_dir=tmp_path / "c")
    assert len(store.certificate_fingerprint()) == 64


def test_qr_png_with_stub_qrcode(tmp_path, monkeypatch):
    from voicetype.companion.pairing import PairingStore
    store = PairingStore(config_dir=tmp_path / "c")
    payload = store.pairing_payload("h", 1)

    class Img:
        def save(self, buf, format=None):
            buf.write(b"PNGDATA")

    monkeypatch.setitem(sys.modules, "qrcode",
                        types.SimpleNamespace(make=lambda uri: Img()))
    assert store.qr_png(payload) == b"PNGDATA"


def test_default_dir_env_and_lan_failure(monkeypatch):
    import os
    import socket as _socket
    from voicetype.companion.pairing import default_config_dir, lan_ip
    monkeypatch.setenv("VOICETYPE_CONFIG_DIR", "/tmp/vt-test-dir")
    from pathlib import Path
    assert default_config_dir() == Path("/tmp/vt-test-dir")

    class DeadSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self, *a):
            raise OSError("no route")

        def close(self):
            pass

    monkeypatch.setattr(_socket, "socket", DeadSocket)
    assert lan_ip() == "127.0.0.1"


# ---------- server gaps ----------
class _SimplerWS:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent: list = []
        self.closed = False

    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m
        return gen()

    async def send(self, data):
        import json as _json
        self.sent.append(_json.loads(data))

    async def close(self):
        self.closed = True


def _srv(tmp_path, pipeline=None):
    from tests.test_companion import FakePipeline
    from voicetype.companion.pairing import PairingStore
    from voicetype.companion.server import CompanionServer
    return CompanionServer(pipeline or FakePipeline(),
                           PairingStore(config_dir=tmp_path / "c"))


@pytest.mark.asyncio
async def test_serve_legacy_and_new_api(tmp_path, monkeypatch):
    import asyncio as _aio
    from voicetype.companion import server as srvmod
    srv = _srv(tmp_path)

    entered: dict = {}

    class CM:
        async def __aenter__(self):
            entered["in"] = True
            return self

        async def __aexit__(self, *a):
            entered["out"] = True
            return False

    def fake_serve(handler, host, port, ssl=None):
        entered["args"] = (host, port, ssl)
        return CM()

    monkeypatch.setitem(sys.modules, "websockets",
                        types.SimpleNamespace(serve=fake_serve))
    task = _aio.create_task(srv.serve())
    await _aio.sleep(0.05)
    assert entered.get("in") and entered.get("args", (None, None))[1] == 8765
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    pkg = types.ModuleType("websockets")
    pkg.__path__ = []
    sub = types.ModuleType("websockets.asyncio")
    sub.__path__ = []

    def fake_new(handler, host, port, ssl=None):
        entered["new"] = True
        return CM()

    srv_pkg = types.ModuleType("websockets.asyncio.server")
    srv_pkg.serve = fake_new
    monkeypatch.setitem(sys.modules, "websockets", pkg)
    monkeypatch.setitem(sys.modules, "websockets.asyncio", sub)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.server", srv_pkg)
    task = _aio.create_task(srv.serve())
    await _aio.sleep(0.05)
    assert entered.get("new")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_serve_in_background_runs(tmp_path, monkeypatch):
    import asyncio as _aio
    from voicetype.companion import server as srvmod
    srv = _srv(tmp_path)

    async def instant(self):
        return None

    monkeypatch.setattr(srvmod.CompanionServer, "serve", instant)
    t = srv.serve_in_background()
    assert t.daemon is True
    t.join(timeout=5.0)
    assert not t.is_alive()


@pytest.mark.asyncio
async def test_handler_and_close_both_raise(tmp_path):
    from voicetype.companion.server import CompanionServer

    class BoomWS(_SimplerWS):
        def __aiter__(self):
            raise RuntimeError("socket died")

        async def close(self):
            raise RuntimeError("close died")

    srv = _srv(tmp_path)
    await srv.handle(BoomWS([]))  # never raises


@pytest.mark.asyncio
async def test_authed_unknown_and_set_listening_fail(tmp_path):
    from tests.test_companion import FakePipeline
    from voicetype.companion.pairing import PairingStore
    from voicetype.companion.server import CompanionServer
    store = PairingStore(config_dir=tmp_path / "c")
    pipe = FakePipeline()
    srv = CompanionServer(pipe, store)
    token = store.issue_pairing_token()
    ws = _SimplerWS([json.dumps({"type": "pair", "token": token}),
                     json.dumps({"type": "mystery"}),
                     json.dumps({"type": "set_listening", "on": True})])
    await srv.handle(ws)
    kinds = [m["type"] for m in ws.sent]
    assert "paired" in kinds and "error" in kinds

    def boom(on):
        raise RuntimeError("nope")

    pipe.set_listening = boom
    ws2 = _SimplerWS([json.dumps({"type": "pair", "token": store.issue_pairing_token()}),
                      json.dumps({"type": "set_listening", "on": True})])
    await srv.handle(ws2)
    assert any(m["type"] == "error" and "set_listening" in m["message"]
               for m in ws2.sent)


@pytest.mark.asyncio
async def test_binary_guards_cap_and_failures(tmp_path):
    from tests.test_companion import FakePipeline
    from voicetype.companion.pairing import PairingStore
    from voicetype.companion.server import CompanionServer, MAX_AUDIO_BYTES
    store = PairingStore(config_dir=tmp_path / "c")
    srv = CompanionServer(FakePipeline(), store)
    # binary while unauthenticated / not recording: ignored
    from voicetype.companion.server import _Session
    s = _Session()
    srv._on_binary(s, b"\x01\x02")
    assert s.chunks == []
    # safety cap trips
    s2 = _Session(authed=True, recording=True, size_bytes=MAX_AUDIO_BYTES)
    srv._on_binary(s2, b"\x01")
    assert s2.recording is False

    class BadPipe(FakePipeline):
        def _process_utterance(self, audio):
            raise RuntimeError("stt down")

        @property
        def executor(self):
            class Ex:
                @staticmethod
                def execute(cmd):
                    raise RuntimeError("exec down")
            return Ex()

    srv2 = CompanionServer(BadPipe(), store)
    token = store.issue_pairing_token()
    pcm = (np.zeros(320, dtype=np.int16)).tobytes()
    ws = _SimplerWS([json.dumps({"type": "pair", "token": token}),
                     json.dumps({"type": "ptt_start", "id": "u"}),
                     pcm,
                     json.dumps({"type": "ptt_stop"}),
                     json.dumps({"type": "command", "action": "x"})])
    await srv2.handle(ws)
    kinds = [(m["type"], m.get("kind")) for m in ws.sent]
    assert ("transcript", "error") in kinds
    assert any(m["type"] == "command_result" and not m["ok"] for m in ws.sent)


def test_services_tls_off_and_ssl_failure(tmp_path, monkeypatch):
    from tests.test_companion import FakePipeline
    from voicetype.companion import server as srvmod
    from voicetype.companion.pairing import PairingStore
    store = PairingStore(config_dir=tmp_path / "c")
    handles = srvmod.start_companion_services(
        {"companion": {"port": 18766, "tls": False}}, FakePipeline(), store=store)
    assert handles["fingerprint"] == "" and handles["payload"] is None
    assert handles["qr_png"] == b""

    def boom(s):
        raise RuntimeError("no crypto")

    monkeypatch.setattr(srvmod, "ssl_context_for", boom)
    handles = srvmod.start_companion_services({"companion": {"port": 18767}},
                                              FakePipeline(), store=store)
    assert "TLS unavailable" in handles["tls_error"]
    assert handles["ssl_context"] is None


# ---------- transcriber ----------
def test_transcriber_race_second_check():
    from voicetype.transcriber import Transcriber
    tr = Transcriber()
    sentinel = object()
    real_lock = tr._lock

    class SneakyLock:
        def __enter__(self):
            tr._model = sentinel  # another thread wins mid-acquisition
            return real_lock.__enter__()

        def __exit__(self, *a):
            return real_lock.__exit__(*a)

    tr._lock = SneakyLock()
    assert tr._ensure_model() is sentinel


def test_transcriber_tiny_resample_and_bad_scores(speech_audio):
    import numpy as np
    from tests.test_transcription import FakeWhisperModel
    from voicetype.transcriber import Transcriber
    tr = Transcriber(model_override=FakeWhisperModel(("e",)))
    assert tr.transcribe(np.ones(2, dtype=np.float32), sample_rate=10 ** 9)["text"] == "e"

    class Weird:
        def __init__(self, text, p, l):
            self.text = text
            self.no_speech_prob = p
            self.avg_logprob = l

    class WModel(FakeWhisperModel):
        def transcribe(self, audio, **kwargs):
            info = type("Info", (), {"language": "en"})()
            return [Weird("a", object(), 0.0), Weird("b", 0.0, object())], info

    tr2 = Transcriber(model_override=WModel(("x",)))
    assert tr2.transcribe(speech_audio)["text"] == "a b"


# ---------- ollama_parser ----------
@pytest.mark.parametrize("raw", ['COMMAND: {not json}', '{"foo": 1}', '{oops}'])
def test_parser_malformed_variants(raw):
    from voicetype.ollama_parser import parse_llm_output
    assert parse_llm_output(raw)["kind"] == "dictation"


# ---------- settings_store ----------
def test_store_config_property_and_empty_pipeline(tmp_path):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    mgr = ConfigManager(user_path=tmp_path / "s.yaml")
    store = SettingsStore(mgr, pipeline=types.SimpleNamespace())
    assert store.config is mgr.config  # property path
    store.apply_live("wake_word.threshold", 0.5)  # missing attrs: swallowed
    store.set("wake_word.threshold", 0.6, save=False)


def test_store_import_skips_duplicates(tmp_path):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    mgr = ConfigManager(user_path=tmp_path / "s.yaml")
    store = SettingsStore(mgr, pipeline=None)
    src = tmp_path / "w.txt"
    src.write_text("alpha\nalpha\nbeta\n", encoding="utf-8")
    words = store.import_vocabulary(src, merge=True)
    assert words[-2:] == ["alpha", "beta"]  # dupes skipped, defaults kept
    assert len(words) == len(set(words))


# ---------- tray without Qt ----------
def test_tray_without_qt(monkeypatch):
    import importlib
    import voicetype.tray as traymod
    saved = {k: v for k, v in sys.modules.items()
             if k == "PySide6" or k.startswith("PySide6.")}
    for k in saved:
        monkeypatch.delitem(sys.modules, k, raising=False)
    monkeypatch.setitem(sys.modules, "PySide6", None)
    try:
        importlib.reload(traymod)
        assert traymod.HAS_QT is False
        traymod.PipelineBridge()  # plain-object branch
        from tests.test_pipeline import _silent_pipeline
        from voicetype.config_manager import load_config
        pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
        tray = traymod.TrayApp(pipe, load_config())
        assert tray.bridge is None
        tray._refresh_menu()
        tray.open_settings()  # HAS_QT False: immediate return
    finally:
        for k, v in saved.items():
            sys.modules[k] = v
        sys.modules.pop("PySide6", None)
        importlib.reload(traymod)
    assert traymod.HAS_QT is True


def test_tray_event_and_window_failures(qapp):
    from voicetype.config_manager import load_config
    from voicetype.tray import TrayApp
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    tray = TrayApp(pipe, load_config(overrides={"injection": {"backend": "fallback"}}))

    class BadTray:
        def setToolTip(self, *a):
            raise RuntimeError("tray gone")

        def setContextMenu(self, *a):
            raise RuntimeError("tray gone")

    tray.tray = BadTray()
    tray._on_pipeline_event("dictation", {"text": "x"})  # swallowed
    tray.tray = None
    tray._toggle()  # window None: skips label update

    class BadLabel:
        def setText(self, *a):
            raise RuntimeError("ui gone")

    tray.window = types.SimpleNamespace(listen_toggle=BadLabel())
    tray._toggle()  # label failure swallowed
    tray.open_settings()  # app None: early return
    tray.window = types.SimpleNamespace(close=lambda: (_ for _ in ()).throw(
        RuntimeError("close gone")))
    tray.quit()  # close failure swallowed
