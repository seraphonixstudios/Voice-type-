"""YAML config with sensible defaults, env overrides, and validation."""
from __future__ import annotations

import copy
import os
import platform
from pathlib import Path

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATHS = [
    Path(__file__).resolve().parents[2] / "config" / "default_config.yaml",
    PACKAGE_DIR / "default_config.yaml",
]

USER_CONFIG_DIR = Path(os.environ.get("VOICETYPE_CONFIG_DIR", Path.home() / ".config" / "voicetype"))
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.yaml"


def _default_config_path() -> Path:
    for p in DEFAULT_CONFIG_PATHS:
        if p.exists():
            return p
    # Fall back to a config shipped next to this file (created at install).
    return DEFAULT_CONFIG_PATHS[0]


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _auto_device() -> str:
    # NOTE: never import torch here — on some Windows machines merely
    # importing torch raises a fatal access violation (observed in tests).
    # Prefer explicit config; auto-detect CUDA via nvidia-smi presence.
    try:
        import os
        if os.environ.get("VOICETYPE_DEVICE") in ("cuda", "cpu"):
            return os.environ["VOICETYPE_DEVICE"]
        import shutil
        if shutil.which("nvidia-smi") is not None:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _auto_compute(device: str) -> str:
    if device == "cuda":
        return "float16"
    return "int8"


def _auto_injection_backend() -> str:
    s = platform.system()
    if s == "Darwin":
        return "cg_event"
    if s == "Windows":
        return "sendinput"
    return "uinput"


def _auto_context_backend() -> str:
    s = platform.system()
    if s == "Darwin":
        return "mac_accessibility"
    if s == "Windows":
        return "uiautomation"
    return "atspi"


class ConfigManager:
    def __init__(self, user_path: Path | str | None = None, overrides: dict | None = None):
        self.user_path = Path(user_path) if user_path else USER_CONFIG_PATH
        with open(_default_config_path(), "r", encoding="utf-8") as fh:
            self._defaults = yaml.safe_load(fh)
        user_cfg: dict = {}
        if self.user_path.exists():
            try:
                with open(self.user_path, "r", encoding="utf-8") as fh:
                    user_cfg = yaml.safe_load(fh) or {}
            except yaml.YAMLError:
                # Recover from a corrupt user config: back it up, use defaults.
                backup = self.user_path.with_suffix(".corrupt.yaml")
                try:
                    backup.write_bytes(self.user_path.read_bytes())
                except OSError:
                    pass
                user_cfg = {}
        cfg = _deep_merge(self._defaults, user_cfg)
        if overrides:
            cfg = _deep_merge(cfg, overrides)
        self.config = self._resolve_auto(cfg)
        self.validate(self.config)

    @staticmethod
    def _resolve_auto(cfg: dict) -> dict:
        cfg = copy.deepcopy(cfg)
        stt = cfg.get("stt", {})
        if stt.get("device") == "auto":
            stt["device"] = _auto_device()
        if stt.get("compute_type") == "auto":
            stt["compute_type"] = _auto_compute(stt.get("device", "cpu"))
        inj = cfg.get("injection", {})
        if inj.get("backend") == "auto":
            inj["backend"] = _auto_injection_backend()
        ctx = cfg.get("context", {})
        if ctx.get("backend") == "auto":
            ctx["backend"] = _auto_context_backend()
        return cfg

    @staticmethod
    def validate(cfg: dict) -> None:
        if cfg.get("sample_rate") != 16000:
            raise ValueError("sample_rate must be 16000 (models are trained at 16kHz mono)")
        if cfg.get("channels") != 1:
            raise ValueError("channels must be 1 (mono)")
        ww = cfg.get("wake_word", {})
        if not 0 < float(ww.get("threshold", 0.5)) < 1:
            raise ValueError("wake_word.threshold must be in (0, 1)")
        if not ww.get("model_name") and not ww.get("model_path"):
            raise ValueError("wake_word needs model_name or model_path")
        stt = cfg.get("stt", {})
        if int(stt.get("batch_size", 16)) < 1:
            raise ValueError("stt.batch_size must be >= 1")

    def get(self, dotted: str, default=None):
        node = self.config
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def save_user_config(self) -> Path:
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.user_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.config, fh, sort_keys=False)
        return self.user_path


def load_config(user_path: Path | str | None = None, overrides: dict | None = None) -> dict:
    return ConfigManager(user_path=user_path, overrides=overrides).config
