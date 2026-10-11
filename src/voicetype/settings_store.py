"""SettingsStore: YAML persistence + live pipeline reload without restart."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

from voicetype.config_manager import ConfigManager


def _get_path(cfg: dict, dotted: str, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _set_path(cfg: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


class SettingsStore:
    """Owns a ConfigManager; `set()` persists to YAML and live-applies to pipeline."""

    def __init__(self, manager: ConfigManager, pipeline=None):
        self.manager = manager
        self.pipeline = pipeline
        self._listeners: list[Callable[[str, Any], None]] = []

    @property
    def config(self) -> dict:
        return self.manager.config

    def get(self, dotted: str, default=None):
        return _get_path(self.manager.config, dotted, default)

    def on_change(self, fn: Callable[[str, Any], None]) -> None:
        self._listeners.append(fn)

    def set(self, dotted: str, value, save: bool = True) -> None:
        _set_path(self.manager.config, dotted, value)
        self.manager.validate(self.manager.config)
        if save:
            self.manager.save_user_config()
        self.apply_live(dotted, value)
        for fn in list(self._listeners):
            try:
                fn(dotted, value)
            except Exception:
                continue

    def set_many(self, items: dict[str, Any]) -> None:
        for k, v in items.items():
            _set_path(self.manager.config, k, v)
        self.manager.validate(self.manager.config)
        self.manager.save_user_config()
        for k, v in items.items():
            self.apply_live(k, v)
            for fn in list(self._listeners):
                try:
                    fn(k, v)
                except Exception:
                    continue

    # ---- live apply: touch the running pipeline, never restart it ----
    def apply_live(self, dotted: str, value) -> None:
        p = self.pipeline
        if p is None:
            return
        try:
            if dotted == "wake_word.threshold":
                p.wake.threshold = float(value)
            elif dotted == "wake_word.phrase":
                pass  # informational (custom model needed for a new phrase)
            elif dotted == "wake_word.model_path":
                pass
            elif dotted == "wake_word.trigger_frames":
                p.wake.trigger_frames = max(1, int(value))
            elif dotted.startswith("stt."):
                key = dotted.split(".", 1)[1]
                mapping = {"model": "model_name", "vad_min_silence_ms": "vad_min_silence_ms",
                           "language": "language", "batch_size": "batch_size",
                           "beam_size": "beam_size", "vad_filter": "vad_filter",
                           "no_speech_threshold": "no_speech_threshold",
                           "log_prob_threshold": "log_prob_threshold",
                           "silence_threshold": None}
                if key in mapping:
                    if key == "silence_threshold":
                        p.silence_threshold = float(value)
                    else:
                        setattr(p.transcriber, mapping[key], value)
                if key == "vad_aggressiveness":
                    p.transcriber.vad_min_silence_ms = int([800, 500, 350, 200][max(0, min(3, int(value)))])
            elif dotted.startswith("llm."):
                key = dotted.split(".", 1)[1]
                if key == "host":
                    p.parser.host = str(value).rstrip("/")
                elif key == "model":
                    p.parser.model = str(value)
                elif key == "temperature":
                    p.parser.temperature = float(value)
                elif key == "num_predict":
                    p.parser.num_predict = int(value)
                elif key == "enabled":
                    p.parser.enabled = bool(value)
                    p.config.setdefault("llm", {})["enabled"] = bool(value)
            elif dotted.startswith("injection."):
                key = dotted.split(".", 1)[1]
                if key == "type_delay_ms":
                    p.injector.delay_ms = int(value)
                elif key == "type_chunk_size":
                    p.injector.chunk_size = max(1, int(value))
                elif key == "use_clipboard_for_unicode":
                    p.injector.use_clipboard_for_unicode = bool(value)
                elif key == "undo_behavior":
                    p.injector.undo_behavior = value if value in ("single", "stacked") else "single"
                elif key == "auto_punctuation":
                    p.config.setdefault("injection", {})["auto_punctuation"] = bool(value)
            elif dotted == "utterance.end_silence_ms":
                p.end_silence_ms = int(value)
            elif dotted == "utterance.min_speech_fraction":
                p.min_speech_fraction = max(0.0, min(1.0, float(value)))
            elif dotted == "vocabulary":
                try:
                    p.transcriber.set_vocabulary(list(value or []))
                except Exception:
                    pass
        except Exception:
            pass

    def export_vocabulary(self, path: Path | str) -> Path:
        import json
        path = Path(path)
        words = list(self.get("vocabulary", []) or [])
        if path.suffix.lower() == ".json":
            path.write_text(json.dumps(words, indent=2), encoding="utf-8")
        else:
            path.write_text("\n".join(words) + ("\n" if words else ""), encoding="utf-8")
        return path

    def import_vocabulary(self, path: Path | str, merge: bool = True) -> list[str]:
        import json
        path = Path(path)
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            words = json.loads(raw)
        else:
            words = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        words = [str(w) for w in words if str(w).strip()]
        current = list(self.get("vocabulary", []) or []) if merge else []
        seen = set(current)
        for w in words:
            if w not in seen:
                current.append(w)
                seen.add(w)
        self.set("vocabulary", current)
        return current

    def snapshot(self) -> dict:
        return copy.deepcopy(self.manager.config)
