"""Context layer: cached active-app + window title, refreshed on focus change."""
from __future__ import annotations

import platform
import subprocess
import threading
import time


def _mac_active_app() -> dict:
    try:
        from AppKit import NSWorkspace  # type: ignore
        ws = NSWorkspace.sharedWorkspace()
        app = ws.frontmostApplication()
        return {
            "app": str(app.localizedName()),
            "bundle": str(app.bundleIdentifier() or ""),
            "title": str(app.localizedName()),
            "platform": "mac",
        }
    except Exception:
        pass
    try:  # AppleScript fallback (no pyobjc needed)
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=3,
        )
        name = out.stdout.strip() or "unknown"
        return {"app": name, "bundle": "", "title": name, "platform": "mac"}
    except Exception:
        return {"app": "unknown", "bundle": "", "title": "", "platform": "mac"}


def _windows_active_app() -> dict:
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        try:
            import ctypes as _c
            _k32 = _c.windll.kernel32
            _h = _k32.OpenProcess(0x1000, False, pid.value)
            if _h:
                _b = _c.create_unicode_buffer(260)
                _ps = _c.windll.psapi if hasattr(_c.windll, "psapi") else None
                _k32.CloseHandle(_h)
        except Exception:
            pass
        return {"app": buf.value or "unknown", "bundle": str(pid.value),
                "title": buf.value or "", "platform": "windows", "exe": exe}
    except Exception:
        return {"app": "unknown", "bundle": "", "title": "", "platform": "windows"}


def _linux_active_app() -> dict:
    # Works on X11 and Wayland: try xdotool/xprop, else wlroots/swaymsg, else env.
    for cmd in (["xdotool", "getwindowfocus", "getwindowname"],
                ["swaymsg", "-t", "get_tree"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if out.returncode == 0 and out.stdout.strip():
                title = out.stdout.strip()[:200]
                return {"app": title, "bundle": "", "title": title, "platform": "linux"}
        except Exception:
            continue
    import os
    app = os.environ.get("XDG_CURRENT_DESKTOP", "") or os.environ.get("DESKTOP_SESSION", "") or "unknown"
    return {"app": app, "bundle": "", "title": "", "platform": "linux"}


def query_active_app() -> dict:
    s = platform.system()
    if s == "Darwin":
        return _mac_active_app()
    if s == "Windows":
        return _windows_active_app()
    return _linux_active_app()


def query_cursor_position() -> dict:
    """Best-effort cursor position. Returns {"x": int|None, "y": int|None}."""
    s = platform.system()
    try:
        if s == "Darwin":
            try:
                from AppKit import NSEvent  # type: ignore
                pt = NSEvent.mouseLocation()
                return {"x": int(pt.x), "y": int(pt.y)}
            except Exception:
                pass
            try:
                import Quartz  # type: ignore
                loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
                return {"x": int(loc.x), "y": int(loc.y)}
            except Exception:
                pass
        elif s == "Windows":
            import ctypes
            from ctypes import wintypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            pt = POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return {"x": int(pt.x), "y": int(pt.y)}
        else:
            out = subprocess.run(
                ["xdotool", "getmouselocation", "--shell"],
                capture_output=True, text=True, timeout=2,
            )
            if out.returncode == 0:
                vals: dict = {}
                for line in out.stdout.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        vals[k.strip()] = v.strip()
                return {"x": int(vals["X"]), "y": int(vals["Y"])}
    except Exception:
        pass
    return {"x": None, "y": None}


class ContextLayer:
    """Caches active-app info; polls in background and refreshes on focus change."""

    def __init__(self, poll_interval: float = 1.0, backend: str = "auto",
                 query_fn=None, cursor_fn=None):
        self.poll_interval = float(poll_interval)
        self.backend = backend
        self._query = query_fn or query_active_app
        self._cursor_fn = cursor_fn or query_cursor_position
        self._info: dict = {"app": "unknown", "bundle": "", "title": "",
                            "platform": platform.system(),
                            "cursor": {"x": None, "y": None}, "stale": False}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listeners: list = []
        self.refresh()

    def add_focus_listener(self, fn) -> None:
        self._listeners.append(fn)

    def refresh(self) -> dict:
        try:
            info = dict(self._query())
            stale = False
        except Exception:
            # Keep the last-good (stale) cache instead of blanking it.
            with self._lock:
                self._info["stale"] = True
                return dict(self._info)
        try:
            info["cursor"] = dict(self._cursor_fn())
        except Exception:
            info["cursor"] = {"x": None, "y": None}
        info["stale"] = stale
        with self._lock:
            changed = info.get("app") != self._info.get("app") or \
                info.get("title") != self._info.get("title")
            self._info = info
        if changed:
            for fn in list(self._listeners):
                try:
                    fn(dict(info))
                except Exception:
                    continue
        return dict(info)

    def current(self) -> dict:
        with self._lock:
            return dict(self._info)

    def start(self) -> None:
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="vt-context", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.poll_interval)
            try:
                self.refresh()
            except Exception:
                continue

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
