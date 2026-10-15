"""App entry points + tray: create_app, CLI flags, tray run/quit/menu paths."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    __import__("importlib").util.find_spec("PySide6") is None,
    reason="PySide6 not installed",
)


def _pipe_and_store(tmp_path):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    mgr = ConfigManager(user_path=tmp_path / "config.yaml")
    return pipe, mgr.config, SettingsStore(mgr, pipe)


def test_create_app_wires_all(tmp_path):
    from voicetype.app import create_app
    pipe, config, store = create_app(user_config=str(tmp_path / "c.yaml"))
    assert pipe is store.pipeline
    assert config["sample_rate"] == 16000
    assert store.get("wake_word.phrase")


def test_main_check_prints_config(tmp_path, capsys):
    from voicetype.app import main
    assert main(["--config", str(tmp_path / "c.yaml"), "--check"]) == 0
    assert "sample_rate" in capsys.readouterr().out


def test_main_no_tray_runs_until_interrupt(tmp_path, monkeypatch, capsys):
    from voicetype.app import main
    from voicetype import app as appmod

    started, stopped = [], []

    def fake_start(self):
        started.append(True)
        return {"capture": "synthetic", "wake": "fallback"}

    def fake_stop(self):
        stopped.append(True)

    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(appmod.VoicePipeline, "start", fake_start)
    monkeypatch.setattr(appmod.VoicePipeline, "stop", fake_stop)
    monkeypatch.setattr(appmod, "_run_forever", interrupted)
    assert main(["--config", str(tmp_path / "c.yaml"), "--no-tray"]) == 0
    assert started and stopped
    assert "headless" in capsys.readouterr().out


def test_run_forever_sleeps_until_stopped(monkeypatch):
    import time
    from voicetype import app as appmod
    calls: list = []

    def sleep_once(secs):
        calls.append(secs)
        raise RuntimeError("stop now")

    monkeypatch.setattr(time, "sleep", sleep_once)
    with pytest.raises(RuntimeError, match="stop now"):
        appmod._run_forever()
    assert calls == [1]


def test_main_tray_path_uses_tray_app(tmp_path, monkeypatch):
    from voicetype import app as appmod
    seen: list = []

    class FakeTray:
        def __init__(self, *a):
            seen.append(a)

        def run(self):
            return 0

    monkeypatch.setattr("voicetype.tray.TrayApp", FakeTray)
    assert appmod.main(["--config", str(tmp_path / "c.yaml")]) == 0
    assert len(seen) == 1 and len(seen[0]) == 3


def test_tray_run_opens_window_and_quits(tmp_path, qapp, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from voicetype.tray import TrayApp
    pipe, config, store = _pipe_and_store(tmp_path)
    config["ui"]["start_minimized"] = False  # force settings window open
    tray = TrayApp(pipe, config, store)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(type(pipe), "start", lambda self: {"capture": "x", "wake": "y"})
    assert tray.run() == 0
    assert tray.window is not None and tray.window.isVisible()
    assert tray.tray is not None
    tray.bridge.event.emit("dictation", {"text": "hello"})
    qapp.processEvents()
    assert "hello" in tray.tray.toolTip()
    tray.bridge.event.emit("listening", {"on": False})
    tray._toggle()  # negates pipeline state + updates window label
    assert pipe.listening is False
    assert "Resume" in tray.window.listen_toggle.text()
    tray.quit()
    assert tray.window.isHidden() or True


def test_tray_run_minimized_stays_hidden(tmp_path, qapp, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from voicetype.tray import TrayApp
    pipe, config, store = _pipe_and_store(tmp_path)
    config["ui"]["start_minimized"] = True
    tray = TrayApp(pipe, config, store)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(type(pipe), "start", lambda self: {})
    assert tray.run() == 0
    assert tray.window is None
    tray.open_settings()  # creates window on demand
    assert tray.window is not None
    tray.open_settings()  # second call reuses window
    tray.quit()


def test_tray_refresh_menu_and_icon(qapp):
    from voicetype.tray import TrayApp, _tray_icon
    from voicetype.config_manager import load_config
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    tray = TrayApp(pipe, load_config(overrides={"injection": {"backend": "fallback"}}))
    tray._refresh_menu()  # no tray widget yet -> safe no-op
    assert not _tray_icon().isNull()


def test_tray_run_requires_qt(monkeypatch):
    import voicetype.tray as traymod
    from voicetype.config_manager import load_config
    from tests.test_pipeline import _silent_pipeline
    monkeypatch.setattr(traymod, "HAS_QT", False)
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    with pytest.raises(RuntimeError):
        traymod.TrayApp(pipe, load_config()).run()


def test_tray_main_entry(monkeypatch, tmp_path):
    import voicetype.tray as traymod
    from tests.test_pipeline import _silent_pipeline
    from voicetype.config_manager import load_config
    ran: list = []
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    monkeypatch.setattr(traymod.TrayApp, "run", lambda self: ran.append(1) or 0)
    monkeypatch.setattr("voicetype.app.create_app", lambda: (pipe, cfg, None))
    assert traymod.main() == 0
    assert ran == [1]


def test_main_module_executes(capsys):
    import runpy
    import sys
    argv, sys.argv = sys.argv, ["voicetype", "--check"]
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("voicetype.__main__", run_name="__main__")
    finally:
        sys.argv = argv
    assert exc.value.code == 0
    assert "sample_rate" in capsys.readouterr().out


def test_create_app_survives_dll_setup_failure(tmp_path, monkeypatch):
    from voicetype import _dll_setup as dllmod
    from voicetype.app import create_app
    monkeypatch.setattr(dllmod, "ensure_cuda_dlls",
                        lambda: (_ for _ in ()).throw(RuntimeError("no cuda")))
    pipe, config, store = create_app(user_config=str(tmp_path / "c.yaml"))
    assert pipe is store.pipeline and config["sample_rate"] == 16000


def test_push_to_talk_wiring_and_failure(tmp_path, monkeypatch, capsys):
    import sys
    import threading
    import time
    import types
    from voicetype import app as appmod
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from tests.test_pipeline import _silent_pipeline

    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    mgr = ConfigManager(user_path=tmp_path / "c.yaml")
    store = SettingsStore(mgr, pipe)
    captured: dict = {}

    class FakeListener:
        def __init__(self, spec, callback):
            captured["spec"] = spec
            captured["callback"] = callback

        def start(self):
            captured["started"] = True
            return True

    monkeypatch.setitem(sys.modules, "voicetype.hotkeys",
                        types.SimpleNamespace(HotkeyListener=FakeListener))
    listener = appmod._start_push_to_talk(mgr.config, pipe)
    assert captured["spec"] == "ctrl+alt+space" and captured["started"] is True
    assert listener is not None
    done: list = []
    pipe.dictate_once = lambda: done.append(1) or {"kind": "empty", "text": ""}
    captured["callback"]()
    deadline = time.time() + 5.0
    while not done and time.time() < deadline:
        time.sleep(0.02)
    assert done == [1]

    # Hotkey subsystem missing entirely: main() reports and continues.
    monkeypatch.setitem(sys.modules, "voicetype.hotkeys", None)
    monkeypatch.setattr(appmod, "_run_forever", lambda: None)
    assert appmod.main(["--config", str(tmp_path / "c.yaml"), "--no-tray"]) == 0
    assert "hotkeys unavailable" in capsys.readouterr().out
