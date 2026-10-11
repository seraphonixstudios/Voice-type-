"""Platform permission checks + one-click fixes. Qt-free so tests can use it.

Each check returns a PermissionStatus(ok: bool, detail: str, fix: str).
`fix_<name>` performs the best-effort automated fix and returns (ok, message).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class PermissionStatus:
    name: str
    ok: bool
    detail: str
    fix_hint: str


# ---------------- macOS Accessibility ----------------
def check_mac_accessibility() -> PermissionStatus:
    trusted = False
    detail = "unknown"
    try:
        import ctypes
        ax = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        trusted = bool(ax.AXIsProcessTrusted())
        detail = "trusted" if trusted else "not trusted"
    except Exception as exc:
        detail = f"could not query trust: {exc}"
    try:
        from AppKit import NSWorkspace  # type: ignore
        _ = NSWorkspace.sharedWorkspace().frontmostApplication()
        detail += "; AppKit reachable"
    except Exception:
        pass
    return PermissionStatus(
        "mac_accessibility", trusted, detail,
        "System Settings > Privacy & Security > Accessibility > enable VoiceType/terminal")


def fix_mac_accessibility() -> tuple[bool, str]:
    """Prompt macOS to show the Accessibility consent dialog, then open settings."""
    try:
        import ctypes
        ax = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        prompt = getattr(ax, "AXIsProcessTrustedWithOptions", None)
        if prompt is not None:
            import ctypes.util  # noqa
            prompt.restype = ctypes.c_bool
            prompt.argtypes = [ctypes.c_void_p]
            prompt(None)  # triggers the system consent prompt
    except Exception:
        pass
    try:
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security"
                                "?Privacy_Accessibility"], check=False, timeout=5)
        return True, "Opened Accessibility settings — enable VoiceType, then re-check."
    except Exception as exc:
        return False, f"Could not open settings: {exc}"


# ---------------- Windows UI Automation ----------------
def check_windows_uia() -> PermissionStatus:
    import ctypes
    ok, parts = False, []
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        ok = int(hwnd or 0) != 0
        parts.append(f"foreground window handle={'ok' if ok else 'missing'}")
    except Exception as exc:
        parts.append(f"GetForegroundWindow failed: {exc}")
    elevated = False
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        parts.append("process elevated" if elevated else "process not elevated")
    except Exception:
        pass
    # Mic privacy: registry capability key (best effort).
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
                            r"\ConsentStore\microphone") as key:
            val, _ = winreg.QueryValueEx(key, "Value")
            parts.append(f"mic consent={val}")
            if str(val).lower() not in ("allow",):
                ok = False
    except Exception:
        parts.append("mic consent unknown")
    return PermissionStatus("windows_uia", ok, "; ".join(parts),
                            "Settings > Privacy > Microphone > allow; re-run elevated for admin windows")


def fix_windows_uia() -> tuple[bool, str]:
    try:
        subprocess.run(["start", "ms-settings:privacy-microphone"], shell=True,
                       check=False, timeout=5)
        return True, "Opened microphone privacy settings — allow access, then re-check."
    except Exception as exc:
        return False, f"Could not open settings: {exc}"


# ---------------- Linux uinput ----------------
def check_linux_uinput() -> PermissionStatus:
    path = "/dev/uinput"
    exists = os.path.exists(path)
    rw = os.access(path, os.R_OK | os.W_OK) if exists else False
    groups: list[str] = []
    try:
        import grp
        groups = [g.gr_name for g in grp.getgrall() if str(os.environ.get("USER", "")) in g.gr_mem]
    except Exception:
        pass
    detail = f"{path} exists={exists} rw={rw} groups={groups or 'unknown'}"
    tools = [t for t in ("xdotool", "wtype", "ydotool", "wl-copy", "xclip") if shutil.which(t)]
    detail += f"; fallback tools={tools or 'none'}"
    ok = rw or bool(tools)
    return PermissionStatus("linux_uinput", ok, detail,
                            'sudo usermod -aG input,audio $USER, add udev rule, then relogin')


UDEV_RULE = 'KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"\n'


def fix_linux_uinput() -> tuple[bool, str]:
    """Write a udev rule (needs sudo); falls back to instructing the user."""
    rule_path = "/etc/udev/rules.d/99-voicetype-uinput.rules"
    try:
        if os.access("/dev/uinput", os.R_OK | os.W_OK):
            return True, "/dev/uinput already writable."
        if shutil.which("pkexec"):
            subprocess.run(["pkexec", "sh", "-c",
                            f"echo '{UDEV_RULE.strip()}' > {rule_path} && udevadm control "
                            "--reload-rules && udevadm trigger"], check=False, timeout=30)
            ok = os.access("/dev/uinput", os.R_OK | os.W_OK)
            return ok, ("Installed udev rule. Relogin / replug may be needed."
                        if ok else "Rule install attempted; relogin then re-check.")
        return False, ("No password helper found. Run: echo 'KERNEL==\"uinput\", MODE=\"0660\", "
                       "GROUP=\"input\"' | sudo tee /etc/udev/rules.d/99-voicetype-uinput.rules "
                       "&& sudo udevadm control --reload-rules && sudo usermod -aG input $USER")
    except Exception as exc:
        return False, f"Fix failed: {exc}"


def check_microphone() -> PermissionStatus:
    try:
        import sounddevice as sd  # type: ignore
        devs = sd.query_devices()
        inputs = [d for d in devs if d.get("max_input_channels", 0) > 0]
        return PermissionStatus("microphone", bool(inputs),
                                f"{len(inputs)} input device(s) found",
                                "Check OS mic privacy + cable, then re-check.")
    except Exception as exc:
        return PermissionStatus("microphone", True, f"sounddevice unavailable ({exc}); assuming ok",
                                "pip install sounddevice")


def check_all() -> list[PermissionStatus]:
    s = platform.system()
    out = [check_microphone()]
    if s == "Darwin":
        out.append(check_mac_accessibility())
    elif s == "Windows":
        out.append(check_windows_uia())
    else:
        out.append(check_linux_uinput())
    return out
