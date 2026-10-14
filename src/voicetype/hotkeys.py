"""Global push-to-talk hotkeys: dictate without ever leaving the target app.

Clicking a "dictate" button steals keyboard focus, so the transcript lands
in the wrong window. A system-wide hotkey (default Ctrl+Alt+Space) triggers
recording while the cursor stays where the user left it.

Windows: RegisterHotKey + a daemon message-loop thread (real implementation).
macOS/Linux: best-effort note — global hotkeys need an event tap / key
grabber with extra permissions; the in-app button remains. start() returns
False there instead of pretending.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("voicetype.hotkeys")

MOD_ALT = 0x1
MOD_CONTROL = 0x2
MOD_SHIFT = 0x4
MOD_WIN = 0x8
WM_HOTKEY = 0x0312

_VK = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "escape": 0x1B,
       "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27}
for _i in range(1, 25):
    _VK[f"f{_i}"] = 0x6F + _i


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse "ctrl+alt+space" -> (modifiers, virtual-key code)."""
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    mods, vk = 0, 0
    for part in parts:
        if part == "ctrl":
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "command", "super"):
            mods |= MOD_WIN
        elif part in _VK:
            vk = _VK[part]
        elif len(part) == 1:
            vk = ord(part.upper())
        else:
            raise ValueError(f"unknown hotkey part: {part!r}")
    if not vk:
        raise ValueError(f"no key in hotkey spec: {spec!r}")
    return mods, vk


class HotkeyListener:
    """Registers one global hotkey; invokes `callback` on each press."""

    def __init__(self, spec: str, callback, hotkey_id: int = 0xBEEF):
        self.spec = spec
        self.callback = callback
        self.hotkey_id = hotkey_id
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._supported = self._platform_supported()

    @staticmethod
    def _platform_supported() -> bool:
        import platform
        return platform.system() == "Windows"

    @property
    def supported(self) -> bool:
        return self._supported

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if not self._supported:
            log.info("global hotkeys need Windows RegisterHotKey; "
                     "use the in-app Dictate button on this platform")
            return False
        try:
            self._mods, self._vk = parse_hotkey(self.spec)
        except ValueError as exc:
            log.warning("bad push-to-talk hotkey %r: %s", self.spec, exc)
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="vt-hotkey",
                                        daemon=True)
        self._thread.start()
        return True

    def _loop(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            if not user32.RegisterHotKey(None, self.hotkey_id,
                                         self._mods, self._vk):
                log.warning("RegisterHotKey(%r) failed; hotkey inactive", self.spec)
                return
            log.info("hotkey registered: %r", self.spec)
            try:
                msg = wintypes.MSG()
                while not self._stop.is_set():
                    # Blocking wait; -1 = error/quit.
                    ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if ret <= 0:
                        break
                    if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                        try:
                            self.callback()
                        except Exception as exc:
                            log.warning("hotkey callback failed: %s", exc)
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                user32.UnregisterHotKey(None, self.hotkey_id)
        except Exception as exc:
            log.warning("hotkey listener died: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._platform_supported():
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(
                    self._thread.ident, 0x0012, 0, 0)  # WM_QUIT wakes GetMessage
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
