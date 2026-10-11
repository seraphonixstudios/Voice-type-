"""Render real offscreen screenshots of the VoiceType control center."""
import sys
from pathlib import Path

# NOTE: do not force QT_QPA_PLATFORM=offscreen — the offscreen plugin ships no
# fonts on some machines. Native platform renders text correctly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from voicetype.config_manager import ConfigManager
from voicetype.settings_store import SettingsStore
from voicetype.theme import apply_theme
from voicetype.ui_main import MainWindow
from tests.test_pipeline import _silent_pipeline, FakeParser


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "#4da6ff")
    pipe, _ = _silent_pipeline(
        "hello world", {"kind": "dictation", "text": "Hello world"})
    pipe.last_transcription = "Hello world, this is VoiceType"
    mgr = ConfigManager(user_path=out / "_shot-config.yaml")
    store = SettingsStore(mgr, pipe)
    win = MainWindow(store, pipe)
    win.resize(1100, 720)
    win.show()
    app.processEvents()
    win._add_log("dictation", "Hello world, this is VoiceType")
    win._add_log("command", "scroll down 3")
    win.nav.setCurrentRow(0)
    app.processEvents()
    win.grab().save(str(out / "status.png"))
    win.nav.setCurrentRow(1)
    app.processEvents()
    win.grab().save(str(out / "wake-word.png"))
    win.nav.setCurrentRow(8)
    app.processEvents()
    win.grab().save(str(out / "logs.png"))
    win.close()
    print("saved:", sorted(p.name for p in out.glob("*.png")))


if __name__ == "__main__":
    main()
