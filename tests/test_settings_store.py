from voicetype.config_manager import ConfigManager
from voicetype.settings_store import SettingsStore


class FakeWake:
    threshold = 0.5


class FakeTranscriber:
    def __init__(self):
        self.language = "en"
        self.vad_min_silence_ms = 500
        self.vocab = []

    def set_vocabulary(self, words):
        self.vocab = list(words)


class FakeParser:
    def __init__(self):
        self.host = "http://localhost:11434"
        self.model = "m"
        self.temperature = 0.1
        self.num_predict = 256
        self.enabled = True


class FakeInjector:
    def __init__(self):
        self.delay_ms = 0
        self.chunk_size = 32
        self.use_clipboard_for_unicode = True
        self.undo_behavior = "single"


class FakePipeline:
    def __init__(self):
        self.wake = FakeWake()
        self.transcriber = FakeTranscriber()
        self.parser = FakeParser()
        self.injector = FakeInjector()
        self.end_silence_ms = 900
        self.config = {"llm": {"enabled": True}, "injection": {"auto_punctuation": False}}


def _store(tmp_path, pipeline=None):
    mgr = ConfigManager(user_path=tmp_path / "config.yaml")
    return SettingsStore(mgr, pipeline)


def test_set_persists_and_live_applies(tmp_path):
    pipe = FakePipeline()
    store = _store(tmp_path, pipe)
    store.set("wake_word.threshold", 0.7)
    assert pipe.wake.threshold == 0.7
    # Reloaded from YAML on a fresh manager.
    mgr2 = ConfigManager(user_path=tmp_path / "config.yaml")
    assert mgr2.get("wake_word.threshold") == 0.7


def test_set_many_and_listeners(tmp_path):
    store = _store(tmp_path, FakePipeline())
    seen = []
    store.on_change(lambda k, v: seen.append((k, v)))
    store.set_many({"stt.language": "fr", "llm.temperature": 0.5})
    assert store.get("stt.language") == "fr"
    assert ("llm.temperature", 0.5) in seen


def test_vad_aggressiveness_mapping(tmp_path):
    pipe = FakePipeline()
    store = _store(tmp_path, pipe)
    store.set("stt.vad_aggressiveness", 3, save=False)
    assert pipe.transcriber.vad_min_silence_ms == 200
    store.set("stt.vad_aggressiveness", 0, save=False)
    assert pipe.transcriber.vad_min_silence_ms == 800


def test_llm_disable_and_undo_behavior(tmp_path):
    pipe = FakePipeline()
    store = _store(tmp_path, pipe)
    store.set("llm.enabled", False, save=False)
    assert pipe.parser.enabled is False
    store.set("injection.undo_behavior", "stacked", save=False)
    assert pipe.injector.undo_behavior == "stacked"


def test_vocabulary_import_export(tmp_path):
    store = _store(tmp_path)
    store.set("vocabulary", ["alpha"])
    p = store.export_vocabulary(tmp_path / "v.txt")
    assert p.read_text(encoding="utf-8").strip() == "alpha"
    src = tmp_path / "in.txt"
    src.write_text("beta\ngamma\n", encoding="utf-8")
    words = store.import_vocabulary(src, merge=True)
    assert words == ["alpha", "beta", "gamma"]
    import json
    jp = tmp_path / "v.json"
    store.export_vocabulary(jp)
    assert json.loads(jp.read_text(encoding="utf-8")) == words
