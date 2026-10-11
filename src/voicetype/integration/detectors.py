"""Platform role readers + anti-cheat detection.

Every function degrades gracefully: missing OS APIs, blocked access, or
unknown environments yield {"role": "unknown", ...} or [] — the router then
uses plain-text injection with no formatting.
"""
from __future__ import annotations

import platform
import subprocess

UNKNOWN_ROLE = {"role": "unknown", "is_secure": False, "is_password": False,
                "control_type": ""}

KNOWN_ANTICHEAT = (
    "easyanticheat", "eac", "battleye", "beclient", "bedaisy",
    "vanguard", "vgc", "vgk", "faceit", "warden", "warden",
    "punkbuster", "pnkbstra", "xigncode", "nprotect", "equ8",
)


def get_focused_role() -> dict:
    """Read the focused UI element's role via the OS accessibility layer."""
    s = platform.system()
    try:
        if s == "Darwin":
            return _mac_role()
        if s == "Windows":
            return _windows_role()
        return _linux_role()
    except Exception:
        return dict(UNKNOWN_ROLE)


def _mac_role() -> dict:
    # AXUIElement via pyobjc when present; else AppleScript attribute probe.
    try:
        from ApplicationServices import (  # type: ignore
            AXUIElementCreateSystemWide, AXUIElementCopyAttributeValue,
            kAXFocusedUIElementAttribute, kAXRoleAttribute,
            kAXSubroleAttribute,
        )
        import objc  # type: ignore
        system = AXUIElementCreateSystemWide()
        focused, err = AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute, None)
        if err or focused is None:
            raise RuntimeError("no focused element")
        role, _ = AXUIElementCopyAttributeValue(focused, kAXRoleAttribute, None)
        subrole, _ = AXUIElementCopyAttributeValue(focused, kAXSubroleAttribute, None)
        role_s = str(role or "unknown")
        sub_s = str(subrole or "")
        secure = role_s == "AXSecureTextField" or "Secure" in sub_s
        return {"role": role_s, "is_secure": secure,
                "is_password": secure or "password" in sub_s.lower(),
                "control_type": sub_s}
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get role of first UI element of '
             '(first application process whose frontmost is true)'],
            capture_output=True, text=True, timeout=3,
        )
        role = out.stdout.strip() or "unknown"
        secure = "secure" in role.lower() or "password" in role.lower()
        return {"role": role, "is_secure": secure, "is_password": secure,
                "control_type": ""}
    except Exception:
        return dict(UNKNOWN_ROLE)


def _windows_role() -> dict:
    # UIA focused element via Oleacc (no extra deps); fallback unknown.
    try:
        import ctypes
        from ctypes import wintypes
        oleacc = ctypes.windll.oleacc
        ROLE_SYSTEM_TEXT = 0x2B
        ROLE_SYSTEM_PASSWORD = 0x35  # not a real ROLE_ constant; probed below
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            raise RuntimeError("no foreground window")
        gui = wintypes.DWORD()
        user32.GetGUIThreadInfo(0, ctypes.byref(gui))  # probe; ignore result
        # Heuristic: edit-style classes are text; password style bit = secure.
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        cls = (buf.value or "").lower()
        style = user32.GetWindowLongW(hwnd, -16)
        is_password = bool(style & 0x20) or "password" in cls  # ES_PASSWORD = 0x20
        is_edit = cls in ("edit", "richedit", "richedit20a", "richedit50w") or is_password
        role = "password" if is_password else ("text" if is_edit else "unknown")
        _ = (oleacc, ROLE_SYSTEM_TEXT, ROLE_SYSTEM_PASSWORD)  # keep API refs explicit
        return {"role": role, "is_secure": is_password, "is_password": is_password,
                "control_type": cls}
    except Exception:
        return dict(UNKNOWN_ROLE)


def _linux_role() -> dict:
    # AT-SPI via pyatspi when installed; else at-spi-bus probe; else unknown.
    try:
        import pyatspi  # type: ignore
        desktop = pyatspi.Registry.getDesktop(0)
        for app in desktop:
            for win in app:
                try:
                    if win.getState().contains(pyatspi.STATE_ACTIVE):
                        comp = win.queryComponent() if hasattr(win, "queryComponent") else None
                        _ = comp
                        role = str(win.getRoleName()).lower()
                        secure = "password" in role
                        return {"role": role, "is_secure": secure,
                                "is_password": secure, "control_type": role}
                except Exception:
                    continue
    except Exception:
        pass
    try:
        out = subprocess.run(["busctl", "--user", "list", "org.a11y.Bus"],
                             capture_output=True, text=True, timeout=2)
        if out.returncode == 0:
            return {"role": "unknown", "is_secure": False, "is_password": False,
                    "control_type": "atspi-bus-present"}
    except Exception:
        pass
    return dict(UNKNOWN_ROLE)


def list_processes() -> list[str]:
    """Best-effort process names, lowercased. [] when unavailable."""
    s = platform.system()
    try:
        if s == "Windows":
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=5)
            names = [ln.split('","')[0].strip('"').lower()
                     for ln in out.stdout.splitlines() if ln.strip()]
            return names
        out = subprocess.run(["ps", "-e", "-o", "comm="],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return [ln.strip().lower() for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        pass
    return []


def detect_anticheat(processes: list[str] | None = None) -> list[str]:
    """Return running anti-cheat service names (empty = none detected)."""
    procs = [p.lower() for p in (processes if processes is not None else list_processes())]
    found: list[str] = []
    for proc in procs:
        for sig in KNOWN_ANTICHEAT:
            if sig in proc and proc not in found:
                found.append(proc)
                break
    return found


def should_block_injection(category: str, anticheat: list[str] | None = None) -> tuple[bool, str]:
    """Games + active anti-cheat => hard block with a clear reason."""
    if category == "game" and (anticheat or detect_anticheat()):
        names = ", ".join(anticheat or detect_anticheat())
        return True, (f"Anti-cheat detected ({names}). Injection is disabled in this "
                      f"game to avoid bans — use push-to-talk clipboard mode instead.")
    return False, ""
