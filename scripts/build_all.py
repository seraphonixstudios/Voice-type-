"""PyInstaller one-dir build for the current OS."""
import platform
import subprocess
import sys

APP = "VoiceType"
ENTRY = "src/voicetype/app.py"

if __name__ == "__main__":
    sep = ";" if platform.system() == "Windows" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", "--name", APP,
           "--noconfirm", "--clean",
           "--add-data", f"config{sep}config",
           ENTRY]
    if platform.system() == "Darwin":
        cmd += ["--windowed", "--target-arch", "universal2"]
    elif platform.system() == "Windows":
        cmd += ["--windowed"]
    print("+", " ".join(cmd))
    raise SystemExit(subprocess.call(cmd))
