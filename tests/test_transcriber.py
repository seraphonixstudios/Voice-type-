import numpy as np

from voicetype.transcriber import Transcriber


class FakeSeg:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self, texts):
        self.texts = texts
        self.calls = 0

    def transcribe(self, audio, **kwargs):
        self.calls += 1
        assert kwargs.get("vad_filter") is True
        segs = [FakeSeg(t) for t in self.texts]
        info = type("I", (), {"language": "en"})()
        return segs, info


def _tone(seconds=1.0):
    t = np.arange(int(16000 * seconds)) / 16000.0
    return (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_transcribe_returns_text():
    tr = Transcriber(model_override=FakeModel([" hello ", "world "]))
    out = tr.transcribe(_tone())
    assert out["text"] == "hello world"
    assert out["segments"] == [" hello ", "world "]


def test_silence_short_circuits_model():
    tr = Transcriber(model_override=FakeModel(["hi"]))
    out = tr.transcribe(np.zeros(16000, dtype=np.float32))
    assert out["text"] == ""


def test_batching_splits_long_audio():
    tr = Transcriber(batch_size=2, model_override=FakeModel(["x"]))
    out = tr.transcribe(_tone(seconds=5.0))
    assert out["text"] == "x x x" or "x" in out["text"]
    assert tr._model.calls >= 2


def test_stereo_and_vocab():
    tr = Transcriber(model_override=FakeModel(["ok"]))
    stereo = np.stack([_tone(0.5), _tone(0.5)], axis=1)
    assert tr.transcribe(stereo)["text"] == "ok"
    tr.set_vocabulary(["VoiceType"])
    assert "VoiceType" in tr.initial_prompt
