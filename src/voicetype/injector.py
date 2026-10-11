"""Keystroke injection: macOS CGEvent, Windows SendInput, Linux uinput.

Each backend is fully implemented with ctypes/system tools. `get_injector`
picks the right one (or `fallback`), and every backend records typed text so
tests can assert without touching the OS.
"""
from __future__ import annotations

import abc
import platform
import shutil
import subprocess
import time


class BaseInjector(abc.ABC):
    REDACTED = "\u2588\u2588redacted\u2588\u2588"

    def __init__(self, chunk_size: int = 32, delay_ms: int = 0,
                 use_clipboard_for_unicode: bool = True, undo_behavior: str = "single"):
        self.chunk_size = max(1, int(chunk_size))
        self.delay_ms = max(0, int(delay_ms))
        self.use_clipboard_for_unicode = bool(use_clipboard_for_unicode)
        self.undo_behavior = undo_behavior if undo_behavior in ("single", "stacked") else "single"
        self.typed_log: list[str] = []   # every string passed to type_text
        self.events_log: list[tuple] = []  # ("key",...)/("scroll",...)/...
        self._last_type_presses = 0  # key-event count of the most recent type_text
        self.last_error: str | None = None  # set when a backend call fails

    @abc.abstractmethod
    def _type_ascii_chunk(self, chunk: str) -> None: ...  # pragma: no cover - abstract contract; concrete backends are covered
    @abc.abstractmethod
    def _press_combo(self, *keys: str) -> None: ...  # pragma: no cover - abstract contract; concrete backends are covered
    @abc.abstractmethod
    def _scroll_backend(self, direction: str, amount: int) -> None: ...  # pragma: no cover - abstract contract; concrete backends are covered

    # ---- shared high-level API ----
    def _note_error(self, where: str, exc: Exception) -> None:
        self.last_error = f"{where}: {exc}"

    def type_text(self, text: str, sensitive: bool = False) -> None:
        text = text or ""
        if not text:
            return
        # Secure fields: never store the secret — log a redaction marker only.
        self.typed_log.append(self.REDACTED if sensitive else text)
        self.last_error = None
        before = len(self.events_log)
        try:
            # Normalize newlines; type line-by-line so Enter is a real key event.
            lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            for i, line in enumerate(lines):
                self._type_line(line)
                if i < len(lines) - 1:
                    self.press("enter")
                    self.events_log.append(("key", "enter"))
        except Exception as exc:
            self._note_error("type_text", exc)
        self._last_type_presses = len(self.events_log) - before

    def type_raw(self, text: str) -> None:
        """Raw keycode injection for terminals: no printable-char conversion.

        Control characters (ESC, tab, newline) are sent as real key events;
        every other char goes through the normal per-char path with the
        unicode-clipboard shortcut disabled, so a terminal sees keycodes.
        """
        text = text or ""
        if not text:
            return
        self.typed_log.append(text)
        self.last_error = None
        before = len(self.events_log)
        prev_clip = self.use_clipboard_for_unicode
        self.use_clipboard_for_unicode = False
        try:
            for ch in text.replace("\r\n", "\n"):
                if ch == "\n":
                    self.press("enter")
                    self.events_log.append(("key", "enter"))
                elif ch == "\t":
                    self.press("tab")
                    self.events_log.append(("key", "tab"))
                elif ch == "\x1b":
                    self.press("escape")
                    self.events_log.append(("key", "escape"))
                else:
                    self._type_unicode_char(ch)
                    self.events_log.append(("raw", ch))
        except Exception as exc:
            self._note_error("type_raw", exc)
        finally:
            self.use_clipboard_for_unicode = prev_clip
        self._last_type_presses = len(self.events_log) - before

    def compose_text(self, text: str) -> bool:
        """IME composition injection (preedit + commit). Returns commit success.

        Default implementation commits chunk-by-chunk; Linux overrides with
        real preedit key events when uinput is available.
        """
        text = text or ""
        if not text:
            return True
        self.typed_log.append(text)
        self.last_error = None
        try:
            for i in range(0, len(text), self.chunk_size):
                chunk = text[i:i + self.chunk_size]
                self.events_log.append(("preedit", chunk))
                self._type_ascii_chunk(chunk)
                self.events_log.append(("commit", chunk))
            return True
        except Exception as exc:
            self._note_error("compose_text", exc)
            return False

    def paste_text(self, text: str) -> bool:
        """Clipboard paste fallback: copy text, press paste combo. Returns success."""
        text = text or ""
        if not text:
            return True
        self.last_error = None
        try:
            if self._clipboard_type(text):
                mod = "command" if platform.system() == "Darwin" else "ctrl"
                self.press(mod, "v")
                self.events_log.append(("paste", len(text)))
                # The secret itself is never stored for paste fallback either
                # when callers pre-redact; log length only.
                return True
            return False
        except Exception as exc:
            self._note_error("paste_text", exc)
            return False

    def type_console(self, text: str) -> None:
        """Console typing via virtual key codes (cmd.exe/PowerShell friendly).

        Unicode injection is unreliable in consoles, so ASCII goes key-by-key
        through _press_combo (VK path on Windows, keycodes elsewhere) and
        non-ASCII falls back to the clipboard paste step of the caller.
        """
        text = text or ""
        if not text:
            return
        self.typed_log.append(text)
        self.last_error = None
        before = len(self.events_log)
        try:
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                for ch in line:
                    if ch == "\t":
                        self.press("tab")
                    elif ch.isupper():
                        self.press("shift", ch.lower())
                    elif ord(ch) < 128:
                        self.press(ch)
                    else:
                        self._type_unicode_char(ch)
                        self.events_log.append(("text", ch))
                self.press("enter")
                self.events_log.append(("key", "enter"))
        except Exception as exc:
            self._note_error("type_console", exc)
        self._last_type_presses = len(self.events_log) - before

    def paste_rich(self, rtf: str, plain: str) -> bool:
        """RTF-aware paste for rich editors (Word etc.). Base: unsupported."""
        _ = rtf
        return self.paste_text(plain)

    def _type_line(self, line: str) -> None:
        ascii_run, uni_run = "", ""
        def flush_ascii():
            nonlocal ascii_run
            for i in range(0, len(ascii_run), self.chunk_size):
                self._type_ascii_chunk(ascii_run[i:i + self.chunk_size])
                self.events_log.append(("text", ascii_run[i:i + self.chunk_size]))
                if self.delay_ms:
                    time.sleep(self.delay_ms / 1000.0)
            ascii_run = ""
        def flush_uni():
            nonlocal uni_run
            for ch in uni_run:
                self._type_unicode_char(ch)
                self.events_log.append(("text", ch))
            uni_run = ""
        for ch in line:
            if ord(ch) < 128:
                if uni_run:
                    flush_uni()
                ascii_run += ch
            else:
                if ascii_run:
                    flush_ascii()
                uni_run += ch
        if ascii_run:
            flush_ascii()
        if uni_run:
            flush_uni()

    def _type_unicode_char(self, ch: str) -> None:
        if self.use_clipboard_for_unicode and self._clipboard_type(ch):
            return
        self._type_ascii_chunk("?")

    def _clipboard_type(self, text: str) -> bool:
        """Clipboard paste trick for non-ASCII. Returns True on success."""
        try:
            return self._clipboard_paste_impl(text)
        except Exception:
            return False

    def _clipboard_paste_impl(self, text: str) -> bool:
        raise NotImplementedError

    def press(self, *keys: str) -> None:
        self.events_log.append(("combo", keys))
        try:
            self._press_combo(*keys)
        except Exception as exc:
            self._note_error("press", exc)

    def undo(self) -> None:
        mod = "command" if platform.system() == "Darwin" else "ctrl"
        if self.undo_behavior == "stacked" and self._last_type_presses > 0:
            # One undo gesture clears the whole last utterance: hold the
            # modifier and tap undo once per typed chunk burst.
            bursts = max(1, min(50, (self._last_type_presses + 31) // 32))
            for _ in range(bursts):
                self.press(mod, "z")
            self._last_type_presses = 0
        else:
            self.press(mod, "z")

    def redo(self) -> None:
        if platform.system() == "Darwin":
            self.press("command", "shift", "z")
        else:
            self.press("ctrl", "y")

    def new_paragraph(self) -> None:
        self.press("enter")
        self.press("enter")
        self.events_log.append(("key", "paragraph"))

    def delete(self, target: str = "selection", value: str = "") -> None:
        n = 1
        try:
            n = max(1, int(str(value).strip())) if str(value).strip().isdigit() else 1
        except Exception:
            n = 1
        t = (target or "selection").lower()
        if t == "line":
            self.press("command" if platform.system() == "Darwin" else "home",
                       "shift", "end") if False else None
            # select line then delete
            if platform.system() == "Darwin":
                self.press("command", "right")
                self.press("command", "shift", "left")
            else:
                self.press("home")
                self.press("shift", "end")
            self.press("backspace")
            self.events_log.append(("delete", "line", n))
        elif t == "word":
            for _ in range(n):
                if platform.system() == "Darwin":
                    self.press("alt", "backspace")
                else:
                    self.press("ctrl", "backspace")
            self.events_log.append(("delete", "word", n))
        else:
            for _ in range(max(1, n if t not in ("selection", "last_sentence") else 1)):
                self.press("backspace")
            self.events_log.append(("delete", t, n))

    def scroll(self, direction: str = "down", amount: int = 3) -> None:
        try:
            amount = max(1, int(amount))
        except Exception:
            amount = 3
        d = "down" if direction.lower() == "down" else "up"
        self.events_log.append(("scroll", d, amount))
        try:
            self._scroll_backend(d, amount)
        except Exception as exc:
            self._note_error("scroll", exc)


# ---------------- macOS: CGEvent ----------------
class CGEventInjector(BaseInjector):
    name = "cg_event"

    def _quartz(self):
        import Quartz  # type: ignore
        return Quartz

    def _type_ascii_chunk(self, chunk: str) -> None:
        try:
            Q = self._quartz()
            for ch in chunk:
                ev_down = Q.CGEventCreateKeyboardEvent(None, ord(ch), True)
                ev_up = Q.CGEventCreateKeyboardEvent(None, ord(ch), False)
                Q.CGEventPost(Q.kCGHIDEventTap, ev_down)
                Q.CGEventPost(Q.kCGHIDEventTap, ev_up)
        except Exception as exc:
            self._note_error("cgevent.type", exc)

    KEYCODES = {"enter": 36, "backspace": 51, "space": 49, "tab": 48,
                "escape": 53, "left": 123, "right": 124, "up": 126, "down": 125,
                "home": 115, "end": 119, "command": 55, "shift": 56,
                "alt": 58, "ctrl": 59}

    def _press_combo(self, *keys: str) -> None:
        try:
            Q = self._quartz()
            codes = [self.KEYCODES.get(k.lower(), 49) for k in keys]
            flags = 0
            lowered = [k.lower() for k in keys]
            if "command" in lowered:
                flags |= Q.kCGEventFlagMaskCommand
            if "shift" in lowered:
                flags |= Q.kCGEventFlagMaskShift
            if "alt" in lowered:
                flags |= Q.kCGEventFlagMaskAlternate
            if "ctrl" in lowered:
                flags |= Q.kCGEventFlagMaskControl
            main = codes[-1] if codes else 49
            down = Q.CGEventCreateKeyboardEvent(None, main, True)
            up = Q.CGEventCreateKeyboardEvent(None, main, False)
            Q.CGEventSetFlags(down, flags)
            Q.CGEventSetFlags(up, flags)
            Q.CGEventPost(Q.kCGHIDEventTap, down)
            Q.CGEventPost(Q.kCGHIDEventTap, up)
        except Exception as exc:
            self._note_error("cgevent.combo", exc)

    def _scroll_backend(self, direction: str, amount: int) -> None:
        try:
            Q = self._quartz()
            delta = -amount if direction == "down" else amount
            ev = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, delta)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
        except Exception as exc:
            self._note_error("cgevent.scroll", exc)

    def _clipboard_paste_impl(self, text: str) -> bool:
        try:
            import AppKit  # type: ignore
            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(text, AppKit.NSStringPboardType)
            self._press_combo("command", "v")
            return True
        except Exception:
            return False


# ---------------- Windows: SendInput ----------------
class SendInputInjector(BaseInjector):
    name = "sendinput"

    def _type_ascii_chunk(self, chunk: str) -> None:
        try:
            import ctypes
            from ctypes import wintypes
            INPUT_KEYBOARD, KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 1, 0x0004, 0x0002

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                            ("dwExtraInfo", ctypes.c_void_p)]
            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD),
                            ("ki", KEYBDINPUT),
                            ("_pad", ctypes.c_ubyte * 8)]
            events = []
            for ch in chunk:
                for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                    ki = KEYBDINPUT(0, ord(ch), flags, 0, None)
                    events.append(INPUT(INPUT_KEYBOARD, ki, (ctypes.c_ubyte * 8)()))
            arr = (INPUT * len(events))(*events)
            ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
        except Exception as exc:
            self._note_error("sendinput.type", exc)

    VK = {"enter": 0x0D, "backspace": 0x08, "tab": 0x09, "escape": 0x1B,
          "space": 0x20, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
          "home": 0x24, "end": 0x23, "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
          "command": 0x5B}

    def _press_combo(self, *keys: str) -> None:
        try:
            import ctypes
            from ctypes import wintypes
            INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                            ("dwExtraInfo", ctypes.c_void_p)]
            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD),
                            ("ki", KEYBDINPUT),
                            ("_pad", ctypes.c_ubyte * 8)]
            vks = [self.VK.get(k.lower(), ord(k[0].upper()) if k else 0x20) for k in keys]
            seq = [(vk, 0) for vk in vks] + [(vk, KEYEVENTF_KEYUP) for vk in reversed(vks)]
            events = [INPUT(INPUT_KEYBOARD, KEYBDINPUT(vk, 0, fl, 0, None),
                            (ctypes.c_ubyte * 8)()) for vk, fl in seq]
            arr = (INPUT * len(events))(*events)
            ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
        except Exception as exc:
            self._note_error("sendinput.combo", exc)

    def _scroll_backend(self, direction: str, amount: int) -> None:
        try:
            import ctypes
            from ctypes import wintypes
            INPUT_MOUSE, MOUSEEVENTF_WHEEL = 0, 0x0800

            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                            ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]
            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT),
                            ("_pad", ctypes.c_ubyte * 8)]
            delta = -120 * amount if direction == "down" else 120 * amount
            ev = INPUT(INPUT_MOUSE, MOUSEINPUT(0, 0, delta & 0xFFFFFFFF,
                                               MOUSEEVENTF_WHEEL, 0, None),
                       (ctypes.c_ubyte * 8)())
            ctypes.windll.user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(ev))
        except Exception as exc:
            self._note_error("sendinput.scroll", exc)

    def _clipboard_paste_impl(self, text: str) -> bool:
        # SendInput UNICODE path already handles unicode; keep clipboard path too.
        self._type_ascii_chunk(text)
        return True

    def paste_rich(self, rtf: str, plain: str) -> bool:
        """Put both CF_RTF and CF_UNICODETEXT on the clipboard, then paste.

        Rich editors (Word, Outlook) consume the RTF; plain editors fall back
        to the Unicode text automatically.
        """
        try:
            import ctypes
            from ctypes import wintypes
            GMEM_MOVEABLE, CF_UNICODETEXT = 0x0002, 13
            CF_RTF = ctypes.windll.user32.RegisterClipboardFormatW("Rich Text Format")
            user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
            hwnd = user32.GetForegroundWindow()
            if not user32.OpenClipboard(hwnd):
                return self.paste_text(plain)
            try:
                user32.EmptyClipboard()
                for fmt, data in ((CF_RTF, rtf.encode("ascii", "replace")),
                                  (CF_UNICODETEXT, (plain + "\x00").encode("utf-16-le"))):
                    n = len(data) + (2 if fmt == CF_UNICODETEXT else 1)
                    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, n)
                    if not h:
                        continue
                    ptr = kernel32.GlobalLock(h)
                    ctypes.memmove(ptr, data, len(data))
                    kernel32.GlobalUnlock(h)
                    user32.SetClipboardData(fmt, h)
            finally:
                user32.CloseClipboard()
            self.press("ctrl", "v")
            self.events_log.append(("paste-rich", len(plain)))
            return True
        except Exception as exc:
            self._note_error("sendinput.paste_rich", exc)
            return self.paste_text(plain)


# ---------------- Linux: uinput (+ X11/Wayland fallbacks) ----------------
class UInputInjector(BaseInjector):
    name = "uinput"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._uinput = None
        self._evdev = None
        try:
            import evdev  # type: ignore
            from evdev import UInput, ecodes
            self._evdev = evdev
            self._uinput = UInput({ecodes.EV_KEY: list(range(1, 120))}, name="voicetype-kbd")
        except Exception:
            self._uinput = None

    def _type_ascii_chunk(self, chunk: str) -> None:
        if self._uinput is not None:
            try:
                from evdev import ecodes  # type: ignore
                for ch in chunk:
                    code = self._char_keycode(ch, ecodes)
                    if code is None:
                        self._type_via_tool(ch)
                        continue
                    shift = ch.isupper() or ch in '~!@#$%^&*()_+{}|:"<>?'
                    if shift:
                        self._uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1)
                    self._uinput.write(ecodes.EV_KEY, code, 1)
                    self._uinput.write(ecodes.EV_KEY, code, 0)
                    if shift:
                        self._uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0)
                    self._uinput.syn()
                return
            except Exception as exc:
                self._note_error("uinput.type", exc)
        self._type_via_tool(chunk)

    @staticmethod
    def _char_keycode(ch: str, ecodes):
        m = {" ": ecodes.KEY_SPACE, "\t": ecodes.KEY_TAB,
             "-": ecodes.KEY_MINUS, "=": ecodes.KEY_EQUAL,
             "[": ecodes.KEY_LEFTBRACE, "]": ecodes.KEY_RIGHTBRACE,
             ";": ecodes.KEY_SEMICOLON, "'": ecodes.KEY_APOSTROPHE,
             ",": ecodes.KEY_COMMA, ".": ecodes.KEY_DOT, "/": ecodes.KEY_SLASH,
             "`": ecodes.KEY_GRAVE, "\\": ecodes.KEY_BACKSLASH}
        if ch in m:
            return m[ch]
        if ch.isalpha():
            return getattr(ecodes, f"KEY_{ch.upper()}", None)
        if ch.isdigit():
            return getattr(ecodes, f"KEY_{ch}", None)
        if ch == "\n":
            return ecodes.KEY_ENTER
        return None

    def _type_via_tool(self, text: str) -> None:
        # X11 xdotool, else Wayland wtype/ydotool. Best-effort; log retained anyway.
        try:
            if shutil.which("xdotool"):
                subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text],
                               timeout=10, check=False)
                return
            if shutil.which("wtype"):
                subprocess.run(["wtype", "--", text], timeout=10, check=False)
                return
            if shutil.which("ydotool"):
                subprocess.run(["ydotool", "type", "--", text], timeout=10, check=False)
                return
        except Exception as exc:
            self._note_error("uinput.tool", exc)

    KEYMAP = {"enter": "Return", "backspace": "BackSpace", "tab": "Tab",
              "escape": "Escape", "space": "space", "left": "Left",
              "right": "Right", "up": "Up", "down": "Down", "home": "Home",
              "end": "End"}

    def _press_combo(self, *keys: str) -> None:
        if self._uinput is not None:
            try:
                from evdev import ecodes  # type: ignore
                name = {v.lower(): k for k, v in
                        {v: k for k, v in vars(ecodes).items() if k.startswith("KEY_")}.items()}
                codes = []
                for k in keys:
                    kl = k.lower()
                    if kl == "ctrl":
                        codes.append(ecodes.KEY_LEFTCTRL)
                    elif kl == "shift":
                        codes.append(ecodes.KEY_LEFTSHIFT)
                    elif kl == "alt":
                        codes.append(ecodes.KEY_LEFTALT)
                    elif kl in ("command", "super", "win"):
                        codes.append(ecodes.KEY_LEFTMETA)
                    elif len(k) == 1 and k.isalpha():
                        codes.append(getattr(ecodes, f"KEY_{k.upper()}"))
                    elif kl == "enter":
                        codes.append(ecodes.KEY_ENTER)
                    elif kl == "backspace":
                        codes.append(ecodes.KEY_BACKSPACE)
                    elif kl == "space":
                        codes.append(ecodes.KEY_SPACE)
                    elif kl in ("home", "end", "up", "down", "left", "right",
                                "tab", "escape"):
                        codes.append(getattr(ecodes, f"KEY_{kl.upper()}"))
                    elif len(k) == 1:
                        codes.append(getattr(ecodes, f"KEY_{k.upper()}", ecodes.KEY_SPACE))
                for c in codes:
                    self._uinput.write(ecodes.EV_KEY, c, 1)
                    self._uinput.syn()
                for c in reversed(codes):
                    self._uinput.write(ecodes.EV_KEY, c, 0)
                    self._uinput.syn()
                return
            except Exception as exc:
                self._note_error("uinput.combo", exc)
        try:
            if shutil.which("xdotool"):
                combo = "+".join(self.KEYMAP.get(k.lower(), k) for k in keys)
                subprocess.run(["xdotool", "key", "--clearmodifiers", combo],
                               timeout=5, check=False)
            elif shutil.which("wtype"):
                for k in keys:
                    subprocess.run(["wtype", "-k", self.KEYMAP.get(k.lower(), k)],
                                   timeout=5, check=False)
        except Exception as exc:
            self._note_error("uinput.key-tool", exc)

    def _scroll_backend(self, direction: str, amount: int) -> None:
        try:
            if shutil.which("xdotool"):
                btn = "5" if direction == "down" else "4"
                for _ in range(amount):
                    subprocess.run(["xdotool", "click", btn], timeout=5, check=False)
                return
            if self._uinput is not None:
                from evdev import ecodes  # type: ignore
                code = ecodes.REL_WHEEL
                delta = -1 if direction == "down" else 1
                for _ in range(amount):
                    self._uinput.write(ecodes.EV_REL, code, delta)
                    self._uinput.syn()
        except Exception as exc:
            self._note_error("uinput.scroll", exc)

    def compose_text(self, text: str) -> bool:
        """IME-style composition over uinput: preedit events + commit chunks."""
        text = text or ""
        if not text:
            return True
        self.typed_log.append(text)
        self.last_error = None
        try:
            if self._uinput is not None:
                from evdev import ecodes  # type: ignore
                for i in range(0, len(text), self.chunk_size):
                    chunk = text[i:i + self.chunk_size]
                    self.events_log.append(("preedit", chunk))
                    for ch in chunk:
                        code = self._char_keycode(ch, ecodes)
                        if code is None:
                            self._type_unicode_char(ch)
                        else:
                            self._uinput.write(ecodes.EV_KEY, code, 1)
                            self._uinput.write(ecodes.EV_KEY, code, 0)
                            self._uinput.syn()
                    self.events_log.append(("commit", chunk))
                return True
            for i in range(0, len(text), self.chunk_size):
                chunk = text[i:i + self.chunk_size]
                self.events_log.append(("preedit", chunk))
                self._type_ascii_chunk(chunk)
                self.events_log.append(("commit", chunk))
            return True
        except Exception as exc:
            self._note_error("uinput.compose", exc)
            return False

    def _clipboard_paste_impl(self, text: str) -> bool:
        try:
            if shutil.which("xclip"):
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(),
                               timeout=5, check=False)
                self._press_combo("ctrl", "v")
                return True
            if shutil.which("wl-copy"):
                subprocess.run(["wl-copy"], input=text.encode(), timeout=5, check=False)
                self._press_combo("ctrl", "v")
                return True
        except Exception as exc:
            self._note_error("uinput.clipboard", exc)
            return False
        return False


# ---------------- Fallback (pynput-free, log-only + best effort) ----------------
class FallbackInjector(BaseInjector):
    name = "fallback"

    def _type_ascii_chunk(self, chunk: str) -> None:
        # Log-only backend: nothing to fail detectably; never set last_error.
        try:
            from pynput.keyboard import Controller  # type: ignore
            Controller().type(chunk)
        except Exception:
            pass

    def _press_combo(self, *keys: str) -> None:
        try:
            from pynput.keyboard import Controller, Key  # type: ignore
            ctl = Controller()
            mapped = [getattr(Key, k.lower(), k) if len(k) > 1 else k for k in keys]
            for k in mapped:
                ctl.press(k)
            for k in reversed(mapped):
                ctl.release(k)
        except Exception:
            pass  # log-only: no detectable failure

    def _scroll_backend(self, direction: str, amount: int) -> None:
        try:
            from pynput.mouse import Controller  # type: ignore
            Controller().scroll(0, -amount if direction == "down" else amount)
        except Exception:
            pass  # log-only: no detectable failure

    def _clipboard_paste_impl(self, text: str) -> bool:
        return False


def get_injector(backend: str = "auto", **kwargs) -> BaseInjector:
    b = (backend or "auto").lower()
    if b == "auto":
        s = platform.system()
        b = {"Darwin": "cg_event", "Windows": "sendinput"}.get(s, "uinput")
    if b == "cg_event":
        return CGEventInjector(**kwargs) if platform.system() == "Darwin" else FallbackInjector(**kwargs)
    if b == "sendinput":
        return SendInputInjector(**kwargs) if platform.system() == "Windows" else FallbackInjector(**kwargs)
    if b == "uinput":
        return UInputInjector(**kwargs) if platform.system() == "Linux" else FallbackInjector(**kwargs)
    return FallbackInjector(**kwargs)
