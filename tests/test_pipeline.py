import numpy as np

from voicetype.config_manager import load_config
from voicetype.injector import FallbackInjector
from voicetype.pipeline import CommandExecutor, VoicePipeline


class FakeTranscriber:
    def __init__(self, text):
        self.text = text
        self.backend = "fake"
    def transcribe(self, audio, sample_rate=16000):
        return {"text": self.text, "segments": [self.text], "language": "en", "backend": "fake"}
    def set_vocabulary(self, words):
        pass


class FakeParser:
    def __init__(self, result):
        self.result = result
    def classify(self, transcript):
        return dict(self.result)


class FakeContext:
    def current(self):
        return {"app": "TestApp", "title": "t"}
    def refresh(self):
        return self.current()
    def start(self):
        pass
    def stop(self):
        pass


def _cfg():
    return load_config(overrides={"injection": {"backend": "fallback"}})


def _silent_pipeline(transcript, parsed):
    inj = FallbackInjector()
    pipe = VoicePipeline(_cfg(), injector=inj,
                         transcriber=FakeTranscriber(transcript),
                         parser=FakeParser(parsed), context=FakeContext())
    return pipe, inj


def _loud_audio(n: int = 1600, amp: float = 0.3) -> np.ndarray:
    t = np.arange(n) / 16000.0
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_dictation_path_types_text():
    pipe, inj = _silent_pipeline("hello world",
                                 {"kind": "dictation", "text": "Hello world"})
    res = pipe.handle_audio(_loud_audio())
    assert res["kind"] == "dictation"
    assert inj.typed_log == ["Hello world "]


def test_command_delete_executes():
    pipe, inj = _silent_pipeline("delete last 2 words",
                                 {"kind": "command", "action": "delete",
                                  "target": "word", "value": "2"})
    res = pipe.handle_audio(_loud_audio())
    assert res["kind"] == "command" and res["exec"]["ok"] is True
    assert ("delete", "word", 2) in inj.events_log


def test_command_scroll_and_paragraph():
    inj = FallbackInjector()
    from voicetype.context_layer import ContextLayer
    ctx = FakeContext()
    ex = CommandExecutor(inj, ctx)
    assert ex.execute({"action": "scroll", "target": "down", "value": "2"})["ok"]
    assert ex.execute({"action": "new_paragraph", "target": "", "value": ""})["ok"]
    assert ex.execute({"action": "bogus", "target": "", "value": ""})["ok"] is False
    assert ("scroll", "down", 2) in inj.events_log


def test_empty_transcript_does_nothing():
    pipe, inj = _silent_pipeline("", {"kind": "dictation", "text": ""})
    res = pipe.handle_audio(np.zeros(1600, dtype=np.float32))
    assert res["kind"] == "empty" and inj.typed_log == []


def test_handle_audio_respects_pause_switch():
    # Paused must mean silent: even explicit push-to-talk injects nothing,
    # so a stuck hotkey can never type on its own.
    pipe, inj = _silent_pipeline("hello world",
                                 {"kind": "dictation", "text": "Hello world"})
    pipe.set_listening(False)
    res = pipe.handle_audio(_loud_audio())
    assert res["kind"] == "empty" and res["text"] == ""
    assert inj.typed_log == []
    pipe.set_listening(True)
    assert pipe.handle_audio(_loud_audio())["kind"] == "dictation"


def test_tray_menu_builds_without_qt():
    from voicetype.tray import TrayApp
    pipe, inj = _silent_pipeline("hi", {"kind": "dictation", "text": "hi"})
    tray = TrayApp(pipe, _cfg())
    labels = [a["label"] for a in tray.build_menu_actions()]
    assert any("Mic" in label for label in labels)
    assert any("Quit" in label for label in labels)
