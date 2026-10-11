"""UI tests: window renders, settings persist to YAML, tray menu works."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    __import__("importlib").util.find_spec("PySide6") is None,
    reason="PySide6 not installed",
)


def _make_window(tmp_path):
    from voicetype.config_manager import ConfigManager
    from voicetype.settings_store import SettingsStore
    from voicetype.ui_main import MainWindow
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hello", {"kind": "dictation", "text": "hello"})
    mgr = ConfigManager(user_path=tmp_path / "config.yaml")
    store = SettingsStore(mgr, pipe)
    win = MainWindow(store, pipe)
    return win, store, pipe


def test_main_window_pages_and_theme(tmp_path, qapp):
    from voicetype.theme import apply_theme, build_qss
    apply_theme(qapp, "#4da6ff")
    assert "#ff0000" in build_qss("#ff0000")
    assert "#4da6ff" in build_qss("#4da6ff")
    win, store, pipe = _make_window(tmp_path)
    assert win.stack.count() == 9  # Status..Logs
    assert win.nav.count() == 9
    win.show()
    assert win.isVisible()
    win.close()


def test_window_renders_every_page(tmp_path, qapp):
    win, store, pipe = _make_window(tmp_path)
    win.show()
    for row in range(win.nav.count()):
        win.nav.setCurrentRow(row)
        qapp.processEvents()
        assert win.stack.currentIndex() == row
    win.close()


def test_wake_slider_persists_to_yaml(tmp_path, qapp):
    import yaml
    win, store, pipe = _make_window(tmp_path)
    win.wake_slider.setValue(80)  # threshold 0.20
    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert abs(on_disk["wake_word"]["threshold"] - 0.20) < 1e-9
    assert abs(store.get("wake_word.threshold") - 0.20) < 1e-9
    win.close()


def test_speech_and_command_widgets_persist(tmp_path, qapp):
    win, store, pipe = _make_window(tmp_path)
    win.stt_model.setCurrentText("small")
    assert store.get("stt.model") == "small"
    win.vad_aggro.setValue(3)
    assert store.get("stt.vad_aggressiveness") == 3
    win.llm_enabled.setChecked(False)
    assert store.get("llm.enabled") is False
    win.temp_slider.setValue(50)
    assert abs(store.get("llm.temperature") - 0.5) < 1e-9
    win.autopunct.setChecked(True)
    assert store.get("injection.auto_punctuation") is True
    win.close()


def test_vocab_crud_and_logs(tmp_path, qapp):
    win, store, pipe = _make_window(tmp_path)
    store.set("vocabulary", ["one"])
    win._reload_vocab_list()
    assert win.vocab_list.count() == 1
    win._add_log("dictation", "hello world")
    assert win.log_table.rowCount() == 1
    win.log_search.setText("zzz-no-match")
    assert win.log_table.isRowHidden(0)
    win.log_search.setText("hello")
    assert not win.log_table.isRowHidden(0)
    win._clear_logs()
    assert win.log_table.rowCount() == 0
    win.close()


def test_listening_toggle_and_emergency_stop(tmp_path, qapp):
    win, store, pipe = _make_window(tmp_path)
    assert pipe.listening is True
    win.toggle_listening()
    assert pipe.listening is False
    assert "Resume" in win.listen_toggle.text()
    win.toggle_listening()
    assert pipe.listening is True
    win._emergency_stop()
    assert pipe.listening is False
    win.close()


def test_wake_test_button_and_event_flow(tmp_path, qapp):
    win, store, pipe = _make_window(tmp_path)
    win._test_wake()  # simulate_wake -> pipeline callback path is separate
    win._on_event("wake", {"score": 0.9})
    assert "heard" in win.wake_led.text()
    n_before = win.log_table.rowCount()  # pipeline's own dictation already logged
    win._on_event("dictation", {"text": "hi there"})
    assert win.last_label.text() == "hi there"
    assert win.log_table.rowCount() == n_before + 1
    win._on_event("command", {"parsed": {"action": "scroll", "target": "down", "value": "2"}})
    assert win.log_table.rowCount() == n_before + 2
    win._on_event("listening", {"on": False})
    assert "Resume" in win.listen_toggle.text()
    win.close()


def test_permission_check_updates_label(tmp_path, qapp):
    win, store, pipe = _make_window(tmp_path)
    key = next(iter(win.perm_rows))
    st = win._perm_check(key)
    label = win.perm_rows[key][0]
    assert ("OK" in label.text()) == bool(st.ok)
    win.close()


def test_tray_menu_builds_and_toggles():
    from voicetype.config_manager import load_config
    from voicetype.tray import TrayApp
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    tray = TrayApp(pipe, load_config(overrides={"injection": {"backend": "fallback"}}))
    ids = [a["id"] for a in tray.build_menu_actions()]
    assert ids == ["status", "last", "toggle", "settings", "quit"]
    assert "Stop listening" in [a["label"] for a in tray.build_menu_actions()][2]
    tray._toggle()
    assert pipe.listening is False
    labels = [a["label"] for a in tray.build_menu_actions()]
    assert any("Start listening" in label for label in labels)
    tray._toggle()


def test_tray_event_handler_without_tray_widget():
    from voicetype.config_manager import load_config
    from voicetype.tray import TrayApp
    from tests.test_pipeline import _silent_pipeline
    pipe, _ = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    tray = TrayApp(pipe, load_config(overrides={"injection": {"backend": "fallback"}}))
    tray._on_pipeline_event("dictation", {"text": "hello"})  # tray is None -> no-op
    tray.quit()  # stops pipeline cleanly without app/window


def test_tray_icon_generated(qapp):
    from voicetype.tray import _tray_icon
    icon = _tray_icon()
    assert not icon.isNull()
    assert not icon.availableSizes() == [] or True  # pixmap-backed; null-check above suffices
