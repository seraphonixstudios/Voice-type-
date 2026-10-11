"""Config tests: YAML load/save, invalid recovery, live reload, auto-detect."""
from __future__ import annotations

import pytest
import yaml

from voicetype.config_manager import (
    ConfigManager,
    _auto_compute,
    _auto_context_backend,
    _auto_device,
    _auto_injection_backend,
    load_config,
)


def test_defaults_resolve(tmp_path):
    mgr = ConfigManager(user_path=tmp_path / "config.yaml")
    assert mgr.get("sample_rate") == 16000
    assert mgr.get("stt.batch_size") == 16
    assert mgr.get("injection.backend") in ("cg_event", "sendinput", "uinput")
    assert mgr.get("stt.device") in ("cpu", "cuda")
    assert mgr.get("missing.key", "dflt") == "dflt"


def test_yaml_save_and_reload_roundtrip(tmp_path):
    user = tmp_path / "config.yaml"
    mgr = ConfigManager(user_path=user)
    mgr.config["wake_word"]["threshold"] = 0.7
    mgr.config["vocabulary"] = ["Zebra"]
    saved = mgr.save_user_config()
    assert saved == user
    reloaded = ConfigManager(user_path=user)
    assert reloaded.get("wake_word.threshold") == 0.7
    assert reloaded.get("vocabulary") == ["Zebra"]
    # Raw YAML is human-readable.
    assert "threshold" in user.read_text(encoding="utf-8")


def test_user_override_merges_over_defaults(tmp_path):
    user = tmp_path / "config.yaml"
    user.write_text("wake_word:\n  threshold: 0.7\n", encoding="utf-8")
    assert ConfigManager(user_path=user).get("wake_word.threshold") == 0.7


def test_corrupt_yaml_recovers_to_defaults_and_backs_up(tmp_path):
    user = tmp_path / "config.yaml"
    user.write_text("wake_word: [unclosed\n\tbad: : :\n", encoding="utf-8")
    mgr = ConfigManager(user_path=user)  # must not raise
    assert mgr.get("wake_word.threshold") == 0.5
    assert user.with_suffix(".corrupt.yaml").exists()


def test_invalid_values_rejected(tmp_path):
    with pytest.raises(ValueError):
        ConfigManager(user_path=tmp_path / "m.yaml", overrides={"sample_rate": 44100})
    with pytest.raises(ValueError):
        ConfigManager(user_path=tmp_path / "m.yaml", overrides={"channels": 2})
    with pytest.raises(ValueError):
        ConfigManager(user_path=tmp_path / "m.yaml",
                      overrides={"wake_word": {"threshold": 5.0}})
    with pytest.raises(ValueError):
        ConfigManager(user_path=tmp_path / "m.yaml",
                      overrides={"wake_word": {"model_name": "", "model_path": ""}})
    with pytest.raises(ValueError):
        ConfigManager(user_path=tmp_path / "m.yaml", overrides={"stt": {"batch_size": 0}})


def test_live_reload_via_settings_store(tmp_path):
    from voicetype.settings_store import SettingsStore
    mgr = ConfigManager(user_path=tmp_path / "config.yaml")
    store = SettingsStore(mgr, pipeline=None)
    store.set("wake_word.threshold", 0.8)
    assert ConfigManager(user_path=tmp_path / "config.yaml").get("wake_word.threshold") == 0.8
    snap = store.snapshot()
    snap["wake_word"]["threshold"] = 0.1
    assert store.get("wake_word.threshold") == 0.8  # snapshot is a copy


def test_auto_device_env_override(monkeypatch):
    monkeypatch.setenv("VOICETYPE_DEVICE", "cuda")
    assert _auto_device() == "cuda"
    monkeypatch.setenv("VOICETYPE_DEVICE", "cpu")
    assert _auto_device() == "cpu"
    monkeypatch.delenv("VOICETYPE_DEVICE")
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert _auto_device() == "cpu"
    monkeypatch.setattr("shutil.which", lambda *a, **k: "/usr/bin/nvidia-smi")
    assert _auto_device() == "cuda"


def test_auto_compute_and_backends(monkeypatch):
    assert _auto_compute("cuda") == "float16"
    assert _auto_compute("cpu") == "int8"
    import platform
    for system, inj, ctx in [("Darwin", "cg_event", "mac_accessibility"),
                             ("Windows", "sendinput", "uiautomation"),
                             ("Linux", "uinput", "atspi")]:
        monkeypatch.setattr(platform, "system", lambda s=system: s)
        assert _auto_injection_backend() == inj
        assert _auto_context_backend() == ctx


def test_load_config_helper(tmp_path):
    cfg = load_config(user_path=tmp_path / "c.yaml",
                      overrides={"hotkeys": {"push_to_talk": "f9"}})
    assert cfg["hotkeys"]["push_to_talk"] == "f9"


def test_resolve_auto_compute_pairing():
    cfg = ConfigManager._resolve_auto({"stt": {"device": "auto", "compute_type": "auto"},
                                       "injection": {"backend": "sendinput"},
                                       "context": {"backend": "uiautomation"}})
    assert cfg["stt"]["device"] in ("cpu", "cuda")
    assert cfg["stt"]["compute_type"] in ("float16", "int8")
