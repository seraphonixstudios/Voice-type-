"""VoiceType control center: dark-themed PySide6 main window + dialogs.

All pages are fully functional: every widget reads/writes SettingsStore, which
persists to YAML and live-applies to the running pipeline without restart.
Qt-free logic (theme string, permissions, store) lives in sibling modules so it
stays unit-testable; this module only contains the widgets.
"""
from __future__ import annotations

import csv
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QProgressBar,
    QScrollArea, QSlider, QSpinBox, QSplitter, QStackedWidget, QStatusBar,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QKeySequenceEdit,
    QHeaderView, QInputDialog,
)

from voicetype import permissions as perm
from voicetype.theme import apply_theme


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


class PipelineBridge(QObject):
    event = Signal(str, dict)


class _Page(QWidget):
    """Scrollable page: content layout inside a QScrollArea for small windows."""

    def __init__(self, title: str):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QLabel(title)
        head.setObjectName("accent")
        head.setStyleSheet("font-size: 17px; font-weight: 700;")
        outer.addWidget(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll, 1)
        self.body = QWidget()
        self.layout = QVBoxLayout(self.body)
        self.layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.body)


def _group(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return box, form


class MainWindow(QMainWindow):
    def __init__(self, store, pipeline, parent=None):
        super().__init__(parent)
        self.store = store
        self.pipeline = pipeline
        self.bridge = PipelineBridge()
        self.bridge.event.connect(self._on_event)
        pipeline.add_event_listener(lambda e, p: self.bridge.event.emit(e, dict(p or {})))
        self.history: list[dict] = []
        self._wake_flash_until = 0.0

        self.setWindowTitle("VoiceType — Settings & Control")
        self.resize(1024, 700)
        self.setMinimumSize(640, 480)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)

        self.splitter = QSplitter(Qt.Horizontal)
        root.addWidget(self.splitter, 1)

        self.nav = QListWidget()
        self.nav.setMaximumWidth(220)
        self.nav.setMinimumWidth(140)
        for name in ["Status", "Wake word", "Speech", "Commands", "Injection",
                     "Hotkeys", "Vocabulary", "Permissions", "Logs"]:
            QListWidgetItem(name, self.nav)
        self.nav.setCurrentRow(0)
        self.splitter.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, False)

        self._build_status()
        self._build_wake()
        self._build_speech()
        self._build_commands()
        self._build_injection()
        self._build_hotkeys()
        self._build_vocab()
        self._build_permissions()
        self._build_logs()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

        # Live refresh timers (cheap: 4 Hz meter, 1 Hz context).
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._tick_meter)
        self._meter_timer.start(250)
        self._ctx_timer = QTimer(self)
        self._ctx_timer.timeout.connect(self._tick_context)
        self._ctx_timer.start(1000)
        self._tick_context()

    # ================= Status dashboard =================
    def _build_status(self):
        page = _Page("Status dashboard")
        box, form = _group("Microphone & engine")
        self.mic_meter = QProgressBar()
        self.mic_meter.setRange(0, 100)
        self.mic_meter.setFormat("mic level %p%")
        form.addRow("Mic level", self.mic_meter)
        self.listen_toggle = QPushButton("Pause listening")
        self.listen_toggle.setObjectName("primary")
        self.listen_toggle.clicked.connect(self.toggle_listening)
        form.addRow("Engine", self.listen_toggle)
        self.wake_led = QLabel("● idle")
        self.wake_led.setStyleSheet("color: #8a93a3; font-size: 16px;")
        form.addRow("Wake word", self.wake_led)
        page.layout.addWidget(box)

        box2, form2 = _group("Now")
        self.last_label = QLabel("—")
        self.last_label.setWordWrap(True)
        self.last_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form2.addRow("Last transcription", self.last_label)
        self.app_label = QLabel("unknown")
        form2.addRow("Active app", self.app_label)
        page.layout.addWidget(box2)
        self.stack.addWidget(page)

    def _tick_meter(self):
        try:
            import numpy as np
            chunk = self.pipeline.ring.read_latest(1600)
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))) if chunk.size else 0.0
            self.mic_meter.setValue(max(0, min(100, int(rms * 350))))
        except Exception:
            pass
        # Expire wake flash.
        if self._wake_flash_until and time.time() > self._wake_flash_until:
            self._wake_flash_until = 0.0
            try:
                self.wake_led.setText("● idle")
                self.wake_led.setStyleSheet("color: #8a93a3; font-size: 16px;")
            except Exception:
                pass

    def _tick_context(self):
        try:
            info = self.pipeline.context.current()
            app = info.get("app", "unknown") or "unknown"
            title = info.get("title", "")
            self.app_label.setText(f"{app}" + (f" — {title[:60]}" if title and title != app else ""))
        except Exception:
            pass

    def toggle_listening(self):
        on = not bool(getattr(self.pipeline, "listening", True))
        self.pipeline.set_listening(on)
        self.listen_toggle.setText("Pause listening" if on else "Resume listening")
        self.statusBar().showMessage(f"Listening {'on' if on else 'paused'} — {_now()}")

    # ================= Wake word =================
    def _build_wake(self):
        page = _Page("Wake word")
        box, form = _group("Detection")
        self.wake_phrase = QLineEdit(str(self.store.get("wake_word.phrase", "Hey VoiceType")))
        self.wake_phrase.setPlaceholderText("Hey VoiceType")
        self.wake_phrase.editingFinished.connect(
            lambda: self.store.set("wake_word.phrase", self.wake_phrase.text().strip() or "Hey VoiceType"))
        form.addRow("Phrase", self.wake_phrase)

        thr = float(self.store.get("wake_word.threshold", 0.5))
        self.wake_slider = QSlider(Qt.Horizontal)
        self.wake_slider.setRange(5, 95)
        self.wake_slider.setValue(int(round((1.0 - thr) * 100)))
        self.wake_value = QLabel(f"{thr:.2f}")
        self.wake_value.setObjectName("muted")
        self.wake_slider.valueChanged.connect(self._wake_slider_changed)
        row = QHBoxLayout()
        row.addWidget(self.wake_slider, 1)
        row.addWidget(self.wake_value)
        wrap = QWidget()
        wrap.setLayout(row)
        form.addRow("Sensitivity", wrap)
        hint = QLabel("Higher sensitivity = easier to trigger (lower threshold).")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("", hint)

        self.wake_model = QLineEdit(str(self.store.get("wake_word.model_name", "hey_jarvis")))
        self.wake_model.editingFinished.connect(
            lambda: self.store.set("wake_word.model_name", self.wake_model.text().strip()))
        form.addRow("Model name", self.wake_model)
        self.wake_path = QLineEdit(str(self.store.get("wake_word.model_path", "")))
        self.wake_path.setPlaceholderText("~/.config/voicetype/hey_voicetype.onnx (optional)")
        self.wake_path.editingFinished.connect(
            lambda: self.store.set("wake_word.model_path", self.wake_path.text().strip()))
        form.addRow("Custom model", self.wake_path)

        test = QPushButton("Test wake word")
        test.clicked.connect(self._test_wake)
        form.addRow("", test)
        page.layout.addWidget(box)
        self.stack.addWidget(page)

    def _wake_slider_changed(self, v: int):
        thr = round(1.0 - v / 100.0, 2)
        self.wake_value.setText(f"{thr:.2f}")
        self.store.set("wake_word.threshold", thr)

    def _test_wake(self):
        try:
            self.pipeline.wake.simulate_wake()
        except Exception as exc:
            QMessageBox.warning(self, "Wake test", f"Could not trigger wake: {exc}")

    # ================= Speech =================
    def _build_speech(self):
        page = _Page("Speech-to-text")
        box, form = _group("faster-whisper")
        self.stt_model = QComboBox()
        self.stt_model.setEditable(True)
        for m in ["large-v3", "medium", "small"]:
            self.stt_model.addItem(m)
        cur = str(self.store.get("stt.model", "large-v3"))
        self.stt_model.setCurrentText(cur)
        self.stt_model.currentTextChanged.connect(lambda t: self.store.set("stt.model", t.strip()))
        form.addRow("Model", self.stt_model)

        self.stt_lang = QComboBox()
        self.stt_lang.setEditable(True)
        for lang in ["en", "es", "fr", "de", "it", "pt", "zh", "ja"]:
            self.stt_lang.addItem(lang)
        self.stt_lang.setCurrentText(str(self.store.get("stt.language", "en")))
        self.stt_lang.currentTextChanged.connect(lambda t: self.store.set("stt.language", t.strip() or "en"))
        form.addRow("Language", self.stt_lang)

        self.vad_check = QCheckBox("Enable VAD (skip silence)")
        self.vad_check.setChecked(bool(self.store.get("stt.vad_filter", True)))
        self.vad_check.toggled.connect(lambda v: self.store.set("stt.vad_filter", bool(v)))
        form.addRow("VAD", self.vad_check)

        self.vad_aggro = QSlider(Qt.Horizontal)
        self.vad_aggro.setRange(0, 3)
        self.vad_aggro.setTickPosition(QSlider.TicksBelow)
        self.vad_aggro.setValue(int(self.store.get("stt.vad_aggressiveness", 2)))
        self.vad_aggro_label = QLabel(str(self.vad_aggro.value()))
        self.vad_aggro_label.setObjectName("muted")
        self.vad_aggro.valueChanged.connect(self._vad_aggro_changed)
        row = QHBoxLayout()
        row.addWidget(self.vad_aggro, 1)
        row.addWidget(self.vad_aggro_label)
        wrap = QWidget()
        wrap.setLayout(row)
        form.addRow("VAD aggressiveness", wrap)

        self.sil_spin = QSpinBox()
        self.sil_spin.setRange(200, 3000)
        self.sil_spin.setSuffix(" ms")
        self.sil_spin.setValue(int(self.store.get("stt.vad_min_silence_ms", 500)))
        self.sil_spin.valueChanged.connect(lambda v: self.store.set("stt.vad_min_silence_ms", int(v)))
        form.addRow("Min silence", self.sil_spin)

        self.end_sil = QSpinBox()
        self.end_sil.setRange(300, 5000)
        self.end_sil.setSuffix(" ms")
        self.end_sil.setValue(int(self.store.get("utterance.end_silence_ms", 900)))
        self.end_sil.valueChanged.connect(lambda v: self.store.set("utterance.end_silence_ms", int(v)))
        form.addRow("End-of-speech silence", self.end_sil)
        page.layout.addWidget(box)
        self.stack.addWidget(page)

    def _vad_aggro_changed(self, v: int):
        self.vad_aggro_label.setText(str(v))
        self.store.set("stt.vad_aggressiveness", int(v))

    # ================= Commands =================
    def _build_commands(self):
        page = _Page("Command parsing — Dolphin Llama 3")
        box, form = _group("Ollama")
        self.llm_enabled = QCheckBox("Enable Dolphin Llama 3 (off = heuristic fallback)")
        self.llm_enabled.setChecked(bool(self.store.get("llm.enabled", True)))
        self.llm_enabled.toggled.connect(lambda v: self.store.set("llm.enabled", bool(v)))
        form.addRow("Enabled", self.llm_enabled)
        self.llm_model = QLineEdit(str(self.store.get("llm.model", "dolphin-llama3:8b")))
        self.llm_model.editingFinished.connect(lambda: self.store.set("llm.model", self.llm_model.text().strip()))
        form.addRow("Model", self.llm_model)
        self.llm_path = QLineEdit(str(self.store.get("llm.model_path", "")))
        self.llm_path.setPlaceholderText("/models/dolphin-llama3.gguf (optional)")
        self.llm_path.editingFinished.connect(lambda: self.store.set("llm.model_path", self.llm_path.text().strip()))
        form.addRow("Model path", self.llm_path)
        self.llm_host = QLineEdit(str(self.store.get("llm.host", "http://localhost:11434")))
        self.llm_host.editingFinished.connect(
            lambda: self.store.set("llm.host", self.llm_host.text().strip()))
        form.addRow("Host", self.llm_host)
        self.temp_slider = QSlider(Qt.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(int(round(float(self.store.get("llm.temperature", 0.1)) * 100)))
        self.temp_label = QLabel(f"{self.temp_slider.value() / 100:.2f}")
        self.temp_label.setObjectName("muted")
        self.temp_slider.valueChanged.connect(self._temp_changed)
        row = QHBoxLayout()
        row.addWidget(self.temp_slider, 1)
        row.addWidget(self.temp_label)
        wrap = QWidget()
        wrap.setLayout(row)
        form.addRow("Temperature", wrap)
        self.cmd_vocab = QLineEdit(", ".join(self.store.get("llm.command_vocabulary", []) or []))
        self.cmd_vocab.setPlaceholderText("comma-separated command phrases, e.g. scroll down, delete line")
        self.cmd_vocab.editingFinished.connect(self._cmd_vocab_saved)
        form.addRow("Command vocabulary", self.cmd_vocab)
        check = QPushButton("Check Ollama connection")
        check.clicked.connect(self._check_ollama)
        form.addRow("", check)
        page.layout.addWidget(box)
        self.stack.addWidget(page)

    def _temp_changed(self, v: int):
        self.temp_label.setText(f"{v / 100:.2f}")
        self.store.set("llm.temperature", round(v / 100.0, 2))

    def _cmd_vocab_saved(self):
        words = [w.strip() for w in self.cmd_vocab.text().split(",") if w.strip()]
        self.store.set("llm.command_vocabulary", words)

    def _check_ollama(self):
        try:
            ok = self.pipeline.parser.ollama_available()
        except Exception:
            ok = False
        QMessageBox.information(self, "Ollama", "Reachable." if ok else "Not reachable — heuristic fallback is active.")

    # ================= Injection =================
    def _build_injection(self):
        page = _Page("Text injection")
        box, form = _group("Typing")
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(0, 100)
        self.speed_slider.setValue(int(self.store.get("injection.type_delay_ms", 0)))
        self.speed_label = QLabel(f"{self.speed_slider.value()} ms/chunk")
        self.speed_label.setObjectName("muted")
        self.speed_slider.valueChanged.connect(self._speed_changed)
        row = QHBoxLayout()
        row.addWidget(self.speed_slider, 1)
        row.addWidget(self.speed_label)
        wrap = QWidget()
        wrap.setLayout(row)
        form.addRow("Typing delay", wrap)
        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(1, 256)
        self.chunk_spin.setValue(int(self.store.get("injection.type_chunk_size", 32)))
        self.chunk_spin.valueChanged.connect(lambda v: self.store.set("injection.type_chunk_size", int(v)))
        form.addRow("Chunk size", self.chunk_spin)
        self.autopunct = QCheckBox("Auto-punctuation (capitalize + trailing period for bare sentences)")
        self.autopunct.setChecked(bool(self.store.get("injection.auto_punctuation", False)))
        self.autopunct.toggled.connect(lambda v: self.store.set("injection.auto_punctuation", bool(v)))
        form.addRow("Punctuation", self.autopunct)
        self.undo_combo = QComboBox()
        self.undo_combo.addItems(["single", "stacked"])
        self.undo_combo.setCurrentText(str(self.store.get("injection.undo_behavior", "single")))
        self.undo_combo.currentTextChanged.connect(lambda t: self.store.set("injection.undo_behavior", t))
        form.addRow("Undo behavior", self.undo_combo)
        self.clip_fallback = QCheckBox("Clipboard fallback for unicode")
        self.clip_fallback.setChecked(bool(self.store.get("injection.use_clipboard_for_unicode", True)))
        self.clip_fallback.toggled.connect(
            lambda v: self.store.set("injection.use_clipboard_for_unicode", bool(v)))
        form.addRow("Clipboard", self.clip_fallback)
        page.layout.addWidget(box)
        self.stack.addWidget(page)

    def _speed_changed(self, v: int):
        self.speed_label.setText(f"{v} ms/chunk")
        self.store.set("injection.type_delay_ms", int(v))

    # ================= Hotkeys =================
    def _build_hotkeys(self):
        page = _Page("Hotkeys")
        box, form = _group("Global shortcuts")
        self.hot_edits: dict[str, QKeySequenceEdit] = {}
        for key, label in [("push_to_talk", "Push-to-talk"), ("toggle_listening", "Toggle listening"),
                           ("cancel_utterance", "Cancel utterance"), ("emergency_stop", "Emergency stop")]:
            edit = QKeySequenceEdit(QKeySequence(str(self.store.get(f"hotkeys.{key}", ""))))
            edit.editingFinished.connect(lambda k=key, e=edit: self._hotkey_saved(k, e))
            self.hot_edits[key] = edit
            form.addRow(label, edit)
        stop = QPushButton("Emergency stop now")
        stop.setObjectName("danger")
        stop.clicked.connect(self._emergency_stop)
        form.addRow("", stop)
        page.layout.addWidget(box)
        self.stack.addWidget(page)

    def _hotkey_saved(self, key: str, edit: QKeySequenceEdit):
        self.store.set(f"hotkeys.{key}", edit.keySequence().toString().lower() or "")

    def _emergency_stop(self):
        self.pipeline.set_listening(False)
        self.listen_toggle.setText("Resume listening")
        self.statusBar().showMessage(f"EMERGENCY STOP — listening off ({_now()})")

    # ================= Vocabulary =================
    def _build_vocab(self):
        page = _Page("Vocabulary")
        box = QGroupBox("Custom words & phrases")
        lay = QVBoxLayout(box)
        self.vocab_list = QListWidget()
        self.vocab_list.setAlternatingRowColors(True)
        self._reload_vocab_list()
        lay.addWidget(self.vocab_list, 1)
        btns = QHBoxLayout()
        add_b = QPushButton("Add")
        add_b.setObjectName("primary")
        add_b.clicked.connect(self._vocab_add)
        edit_b = QPushButton("Edit")
        edit_b.clicked.connect(self._vocab_edit)
        del_b = QPushButton("Delete")
        del_b.setObjectName("danger")
        del_b.clicked.connect(self._vocab_delete)
        imp_b = QPushButton("Import...")
        imp_b.clicked.connect(self._vocab_import)
        exp_b = QPushButton("Export...")
        exp_b.clicked.connect(self._vocab_export)
        for b in (add_b, edit_b, del_b, imp_b, exp_b):
            btns.addWidget(b)
        lay.addLayout(btns)
        page.layout.addWidget(box)
        self.stack.addWidget(page)

    def _vocab_words(self) -> list[str]:
        return list(self.store.get("vocabulary", []) or [])

    def _reload_vocab_list(self):
        self.vocab_list.clear()
        for w in self._vocab_words():
            QListWidgetItem(w, self.vocab_list)

    def _vocab_add(self):
        text, ok = QInputDialog.getText(self, "Add word", "Word or phrase:")
        if ok and text.strip():
            words = self._vocab_words()
            if text.strip() not in words:
                words.append(text.strip())
                self.store.set("vocabulary", words)
                self._reload_vocab_list()

    def _vocab_edit(self):
        item = self.vocab_list.currentItem()
        if item is None:
            return
        text, ok = QInputDialog.getText(self, "Edit word", "Word or phrase:", text=item.text())
        if ok and text.strip():
            words = self._vocab_words()
            words[words.index(item.text())] = text.strip()
            self.store.set("vocabulary", words)
            self._reload_vocab_list()

    def _vocab_delete(self):
        item = self.vocab_list.currentItem()
        if item is None:
            return
        words = [w for w in self._vocab_words() if w != item.text()]
        self.store.set("vocabulary", words)
        self._reload_vocab_list()

    def _vocab_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import vocabulary", "",
                                              "Vocabulary (*.txt *.json);;All (*)")
        if path:
            try:
                self.store.import_vocabulary(path, merge=True)
                self._reload_vocab_list()
                self.statusBar().showMessage(f"Vocabulary imported — {_now()}")
            except Exception as exc:
                QMessageBox.warning(self, "Import", f"Failed: {exc}")

    def _vocab_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export vocabulary", "voicetype-vocabulary.txt",
                                              "Text (*.txt);;JSON (*.json)")
        if path:
            try:
                self.store.export_vocabulary(path)
                self.statusBar().showMessage(f"Vocabulary exported — {_now()}")
            except Exception as exc:
                QMessageBox.warning(self, "Export", f"Failed: {exc}")

    # ================= Permissions =================
    def _build_permissions(self):
        page = _Page("Platform permissions")
        box = QGroupBox("One-click check & fix")
        lay = QVBoxLayout(box)
        self.perm_rows: dict[str, tuple[QLabel, QLabel]] = {}
        import platform as _plat
        plats = {"Darwin": [("microphone", "Microphone"), ("mac_accessibility", "macOS Accessibility")],
                 "Windows": [("microphone", "Microphone"), ("windows_uia", "Windows UI Automation")],
                 "Linux": [("microphone", "Microphone"), ("linux_uinput", "Linux uinput")]}.get(
                     _plat.system(), [("microphone", "Microphone")])
        for key, label in plats:
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(180)
            status = QLabel("not checked")
            status.setObjectName("muted")
            status.setWordWrap(True)
            check_b = QPushButton("Check")
            fix_b = QPushButton("Fix")
            check_b.clicked.connect(lambda _=False, k=key: self._perm_check(k))
            fix_b.clicked.connect(lambda _=False, k=key: self._perm_fix(k))
            row.addWidget(name)
            row.addWidget(status, 1)
            row.addWidget(check_b)
            row.addWidget(fix_b)
            lay.addLayout(row)
            self.perm_rows[key] = (status, status)
        hint = QLabel("Tip: on macOS enable Accessibility for your terminal; on Linux join the "
                      "input group for /dev/uinput; on Windows allow mic access.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        page.layout.addWidget(box)
        self.stack.addWidget(page)

    def _perm_check(self, key: str):
        fn = {"microphone": perm.check_microphone,
              "mac_accessibility": perm.check_mac_accessibility,
              "windows_uia": perm.check_windows_uia,
              "linux_uinput": perm.check_linux_uinput}[key]
        st = fn()
        label = self.perm_rows[key][0]
        label.setText(("OK — " if st.ok else "FAIL — ") + st.detail)
        label.setStyleSheet(f"color: {'#6fdc73' if st.ok else '#ff7b72'};")
        self.statusBar().showMessage(f"{st.name}: {'ok' if st.ok else 'needs attention'} — {_now()}")
        return st

    def _perm_fix(self, key: str):
        fn = {"microphone": None,
              "mac_accessibility": perm.fix_mac_accessibility,
              "windows_uia": perm.fix_windows_uia,
              "linux_uinput": perm.fix_linux_uinput}[key]
        if fn is None:
            QMessageBox.information(self, "Microphone",
                                    "Open your OS sound/privacy settings and allow microphone access.")
            return
        ok, msg = fn()
        QMessageBox.information(self, "Fix", msg)
        self._perm_check(key)

    # ================= Logs =================
    def _build_logs(self):
        page = _Page("Transcription history")
        box = QGroupBox("Logs")
        lay = QVBoxLayout(box)
        top = QHBoxLayout()
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("Search transcriptions...")
        self.log_search.textChanged.connect(self._filter_logs)
        exp_b = QPushButton("Export CSV...")
        exp_b.clicked.connect(self._export_logs)
        clear_b = QPushButton("Clear")
        clear_b.clicked.connect(self._clear_logs)
        top.addWidget(self.log_search, 1)
        top.addWidget(exp_b)
        top.addWidget(clear_b)
        lay.addLayout(top)
        self.log_table = QTableWidget(0, 3)
        self.log_table.setHorizontalHeaderLabels(["Time", "Type", "Text"])
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.log_table.setAlternatingRowColors(True)
        self.log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.log_table, 1)
        page.layout.addWidget(box)
        self.stack.addWidget(page)

    def _add_log(self, kind: str, text: str):
        entry = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "text": text}
        self.history.append(entry)
        self._append_log_row(entry)
        self._filter_logs()

    def _append_log_row(self, entry: dict):
        r = self.log_table.rowCount()
        self.log_table.insertRow(r)
        self.log_table.setItem(r, 0, QTableWidgetItem(entry["time"]))
        self.log_table.setItem(r, 1, QTableWidgetItem(entry["kind"]))
        item = QTableWidgetItem(entry["text"])
        item.setToolTip(entry["text"])
        self.log_table.setItem(r, 2, item)

    def _filter_logs(self):
        q = self.log_search.text().lower() if hasattr(self, "log_search") else ""
        for r in range(self.log_table.rowCount()):
            item = self.log_table.item(r, 2)
            show = (not q) or (item and q in (item.text() or "").lower())
            self.log_table.setRowHidden(r, not show)

    def _export_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export logs", "voicetype-logs.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=["time", "kind", "text"])
                w.writeheader()
                w.writerows(self.history)
            self.statusBar().showMessage(f"Logs exported — {_now()}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed: {exc}")

    def _clear_logs(self):
        self.history.clear()
        self.log_table.setRowCount(0)

    # ================= pipeline events =================
    def _on_event(self, event: str, payload: dict):
        try:
            if event == "wake":
                self._wake_flash_until = time.time() + 3.0
                self.wake_led.setText(f"● heard ({float(payload.get('score', 0)):.2f})")
                self.wake_led.setStyleSheet("color: #6fdc73; font-size: 16px;")
                self.statusBar().showMessage(f"Wake word heard — {_now()}")
            elif event in ("transcribed", "dictation", "command"):
                text = payload.get("text") or str(payload.get("parsed", ""))[:120]
                kind = "dictation" if event != "command" else "command"
                if event == "command":
                    p = payload.get("parsed", {})
                    text = f"{p.get('action', '')} {p.get('target', '')} {p.get('value', '')}".strip()
                self.last_label.setText(text or "—")
                self._add_log(kind, text or "")
                self.statusBar().showMessage(f"{kind}: {(text or '')[:80]}")
            elif event == "listening":
                on = bool(payload.get("on", True))
                self.listen_toggle.setText("Pause listening" if on else "Resume listening")
        except Exception:
            pass


def create_main_window(store, pipeline) -> MainWindow:
    return MainWindow(store, pipeline)
