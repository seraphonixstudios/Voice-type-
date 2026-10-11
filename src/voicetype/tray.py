"""System-tray app (PySide6): quick menu + control-center window.

Keeps the original `TrayApp.build_menu_actions` API (UI-free, used by tests)
and adds the full dark-themed MainWindow on top.
"""
from __future__ import annotations

import sys

try:
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    from PySide6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor
    from PySide6.QtCore import QObject, Signal, Qt
    HAS_QT = True
except Exception:
    HAS_QT = False


class PipelineBridge(QObject if HAS_QT else object):
    if HAS_QT:
        event = Signal(str, dict)

    def __init__(self):
        if HAS_QT:
            super().__init__()


def _tray_icon() -> "QIcon":
    """Generated mic glyph so no asset files are needed on any platform."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#4da6ff"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(26, 8, 12, 26, 6, 6)  # mic head
    p.setBrush(Qt.NoBrush)
    pen = p.pen()
    from PySide6.QtGui import QPen
    p.setPen(QPen(QColor("#4da6ff"), 4))
    p.drawArc(16, 26, 32, 22, 200 * 16, 140 * 16)
    p.drawLine(32, 48, 32, 58)
    p.drawLine(22, 58, 42, 58)
    p.end()
    return QIcon(pix)


class TrayApp:
    """Owns QApplication + QSystemTrayIcon + MainWindow."""

    def __init__(self, pipeline, config: dict, store=None):
        self.pipeline = pipeline
        self.config = config
        self.store = store
        self.app: QApplication | None = None
        self.tray: QSystemTrayIcon | None = None
        self.window = None
        self.bridge = PipelineBridge() if HAS_QT else None
        if HAS_QT and self.bridge is not None:
            self.bridge.event.connect(self._on_pipeline_event)
            pipeline.add_event_listener(lambda e, p: self.bridge.event.emit(e, dict(p or {})))

    # ---- UI-free menu model (used by tests) ----
    def build_menu_actions(self) -> list[dict]:
        on = bool(getattr(self.pipeline, "listening", True))
        last = (getattr(self.pipeline, "last_transcription", "") or "")[:60] or "—"
        return [
            {"id": "status", "label": f"Mic: {'listening' if on else 'paused'}", "enabled": False},
            {"id": "last", "label": f"Last: {last}", "enabled": False},
            {"id": "toggle", "label": "Stop listening" if on else "Start listening"},
            {"id": "settings", "label": "Open settings"},
            {"id": "quit", "label": "Quit VoiceType"},
        ]

    def _on_pipeline_event(self, event: str, payload: dict) -> None:
        if self.tray is None:
            return
        try:
            if event in ("transcribed", "dictation", "command"):
                text = payload.get("text") or str(payload)[:80]
                self.tray.setToolTip(f"VoiceType: {text[:100]}")
            elif event == "listening":
                on = bool(payload.get("on", True))
                self.tray.setToolTip(f"VoiceType — {'listening' if on else 'paused'}")
            self._refresh_menu()
        except Exception:
            pass

    def _toggle(self) -> None:
        self.pipeline.set_listening(not self.pipeline.listening)
        self._refresh_menu()
        if self.window is not None:
            try:
                self.window.listen_toggle.setText(
                    "Pause listening" if self.pipeline.listening else "Resume listening")
            except Exception:
                pass

    def _refresh_menu(self) -> None:
        if self.tray is None or not HAS_QT:
            return
        menu = QMenu()
        for item in self.build_menu_actions():
            if item["id"] in ("toggle", "settings", "quit"):
                act = QAction(item["label"], menu)
                if item["id"] == "toggle":
                    act.triggered.connect(self._toggle)
                elif item["id"] == "settings":
                    act.triggered.connect(self.open_settings)
                else:
                    act.triggered.connect(self.quit)
                menu.addAction(act)
            else:
                act = QAction(item["label"], menu)
                act.setEnabled(False)
                menu.addAction(act)
        self.tray.setContextMenu(menu)

    def open_settings(self) -> None:
        if not HAS_QT or self.app is None:
            return
        if self.window is None:
            from voicetype.theme import apply_theme
            from voicetype.ui_main import MainWindow
            assert self.store is not None, "SettingsStore required to open settings"
            apply_theme(self.app, str((self.config.get("ui", {}) or {}).get("accent_color", "#4da6ff")))
            self.window = MainWindow(self.store, self.pipeline)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def run(self) -> int:
        if not HAS_QT:
            raise RuntimeError("PySide6 is required for the tray UI. Install with: pip install PySide6")
        from voicetype.theme import apply_theme
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("VoiceType")
        self.app.setOrganizationName("VoiceType")
        apply_theme(self.app, str((self.config.get("ui", {}) or {}).get("accent_color", "#4da6ff")))
        self.tray = QSystemTrayIcon(_tray_icon(), self.app)
        self.tray.setToolTip("VoiceType — listening")
        self.tray.activated.connect(lambda r: self.open_settings() if r == QSystemTrayIcon.DoubleClick else None)
        self._refresh_menu()
        self.tray.show()
        self.pipeline.start()
        start_min = bool((self.config.get("ui", {}) or {}).get("start_minimized", True))
        if not start_min:
            self.open_settings()
        return self.app.exec()

    def quit(self) -> None:
        try:
            self.pipeline.stop()
        finally:
            if self.window is not None:
                try:
                    self.window.close()
                except Exception:
                    pass
            if self.app is not None:
                self.app.quit()


def main() -> int:
    from voicetype.app import create_app
    pipeline, config, store = create_app()
    TrayApp(pipeline, config, store).run()
    return 0
