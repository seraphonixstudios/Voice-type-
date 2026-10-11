"""Context tests: active-app detection, cursor refresh, stale cache, threads."""
from __future__ import annotations

import subprocess
import sys
import time
import types

import pytest

from voicetype import context_layer as cl
from voicetype.context_layer import ContextLayer, query_active_app, query_cursor_position


def _ctx(query, cursor=None) -> ContextLayer:
    return ContextLayer(poll_interval=60, query_fn=query,
                        cursor_fn=(lambda: {"x": 1, "y": 2}) if cursor is None else cursor)


def test_focus_change_fires_listeners():
    apps = [{"app": "Terminal", "title": "t1"}, {"app": "Browser", "title": "t2"}]
    it = iter(apps + [apps[-1]])
    ctx = _ctx(lambda: next(it))
    assert ctx.current()["app"] == "Terminal"
    changes: list = []
    ctx.add_focus_listener(changes.append)
    bad_called = []
    ctx.add_focus_listener(lambda info: bad_called.append(1) or (_ for _ in ()).throw(RuntimeError()))
    ctx.refresh()
    assert ctx.current()["app"] == "Browser"
    assert len(changes) == 1 and changes[0]["app"] == "Browser"
    ctx.refresh()  # no change -> no event
    assert len(changes) == 1
    ctx.stop()


def test_cursor_position_refreshes_without_focus_event():
    states = [{"app": "A", "title": "t"}]
    cursors = iter([{"x": 10, "y": 20}, {"x": 30, "y": 40}])
    ctx = ContextLayer(poll_interval=60, query_fn=lambda: states[0],
                       cursor_fn=lambda: next(cursors))
    assert ctx.current()["cursor"] == {"x": 10, "y": 20}
    events: list = []
    ctx.add_focus_listener(events.append)
    ctx.refresh()  # same app, new cursor -> no focus event, cursor updated
    assert ctx.current()["cursor"] == {"x": 30, "y": 40}
    assert events == []
    ctx.stop()


def test_stale_cache_survives_query_failure():
    ctx = _ctx(lambda: {"app": "Good", "title": "t"})
    assert ctx.current()["stale"] is False
    ctx._query = lambda: (_ for _ in ()).throw(RuntimeError("AT-SPI down"))
    out = ctx.refresh()
    assert out["app"] == "Good" and out["stale"] is True
    assert ctx.current()["app"] == "Good"  # last-good cache kept
    ctx.stop()


def test_cursor_failure_defaults_to_none():
    ctx = _ctx(lambda: {"app": "A", "title": "t"},
               cursor=lambda: (_ for _ in ()).throw(RuntimeError("no mouse")))
    assert ctx.current()["cursor"] == {"x": None, "y": None}
    ctx.stop()


def test_start_stop_thread_and_restart_safe():
    ctx = _ctx(lambda: {"app": "A", "title": "t"})
    ctx.start()
    ctx.start()  # second start is a no-op
    time.sleep(0.05)
    ctx.stop()
    ctx.stop()  # double stop is safe
    assert ctx._thread is None


def test_poll_loop_refreshes(monkeypatch):
    seen: list = []
    ctx = ContextLayer(poll_interval=0.01, query_fn=lambda: {"app": "P", "title": "t"},
                       cursor_fn=lambda: {"x": 0, "y": 0})
    ctx.add_focus_listener(lambda info: seen.append(info))
    ctx.start()
    time.sleep(0.05)
    ctx.stop()
    assert ctx.current()["app"] == "P"


# ---- per-platform query routing with mocked OS APIs ----
def test_query_active_app_mac_applescript(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setitem(sys.modules, "AppKit", None)

    def fake_run(*a, **k):
        return types.SimpleNamespace(stdout="Safari\n", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert query_active_app()["app"] == "Safari"


def test_query_active_app_mac_total_failure(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert query_active_app()["app"] == "unknown"


def test_query_active_app_linux_xdotool(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    def fake_run(cmd, **k):
        if cmd[0] == "xdotool":
            return types.SimpleNamespace(stdout="firefox\n", returncode=0)
        raise OSError("no sway")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert query_active_app()["title"] == "firefox"


def test_query_active_app_linux_env_fallback(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    assert query_active_app()["app"] == "GNOME"


def test_query_cursor_linux_parses_xdotool(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="X=100\nY=200\nSCREEN=0\n", returncode=0))
    assert query_cursor_position() == {"x": 100, "y": 200}


def test_query_cursor_linux_failure(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert query_cursor_position() == {"x": None, "y": None}


def test_query_cursor_windows_missing_windll(monkeypatch):
    import ctypes
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.delattr(ctypes, "windll", raising=False)
    assert query_cursor_position() == {"x": None, "y": None}


def test_windows_active_app_failure_shape(mock_platform_windows):
    assert cl._windows_active_app()["platform"] == "windows"
