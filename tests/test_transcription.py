"""Transcription tests: exact matches, VAD silence skip, batch behavior.

The real faster-whisper model is heavy, so a scripted FakeWhisperModel asserts
the exact call contract (beam/vad/prompt args) while transcript assertions use
known synthetic clips.
"""
from __future__ import annotations

import numpy as np
import pytest

from voicetype.transcriber import Transcriber


class FakeSeg:
    def __init__(self, text):
        self.text = text


class FakeWhisperModel:
    """Scripts (audio_len_bucket -> [segment texts]); records every call."""

    def __init__(self, texts=("hello world",), language="en"):
        self.texts = list(texts)
        self.language = language
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):
        self.calls.append({"n": int(np.asarray(audio).size), **kwargs})
        info = type("Info", (), {"language": self.language})()
        return [FakeSeg(t) for t in self.texts], info


def _tr(**kw):
    kw.setdefault("model_override", FakeWhisperModel())
    return Transcriber(**kw)


def test_exact_transcript_match(speech_audio):
    tr = Transcriber(model_override=FakeWhisperModel(("hello world",)))
    out = tr.transcribe(speech_audio)
    assert out["text"] == "hello world"
    assert out["segments"] == ["hello world"]
    assert out["language"] == "en"


def test_multi_segment_join_and_whitespace(speech_audio):
    tr = Transcriber(model_override=FakeWhisperModel(("  new", "paragraph  ", " ")))
    assert tr.transcribe(speech_audio)["text"] == "new paragraph"


def test_empty_segments_yield_empty_text(speech_audio):
    tr = Transcriber(model_override=FakeWhisperModel((), language="fr"))
    out = tr.transcribe(speech_audio)
    assert out == {"text": "", "segments": [], "language": "fr", "backend": "override"}


def test_vad_skips_pure_silence(silence_audio):
    model = FakeWhisperModel(("should never appear",))
    tr = Transcriber(model_override=model)
    out = tr.transcribe(silence_audio)
    assert out["text"] == ""
    assert model.calls == []  # model never woken


def test_vad_skips_empty_input():
    model = FakeWhisperModel(("x",))
    tr = Transcriber(model_override=model)
    assert tr.transcribe(np.zeros(0, dtype=np.float32))["text"] == ""
    assert model.calls == []


def test_vad_contract_args(speech_audio):
    model = FakeWhisperModel(("hi",))
    tr = Transcriber(model_override=model, beam_size=3, language="es",
                     vad_filter=True, vad_min_silence_ms=250)
    tr.transcribe(speech_audio)
    call = model.calls[0]
    assert call["beam_size"] == 3
    assert call["language"] == "es"
    assert call["vad_filter"] is True
    assert call["vad_parameters"] == {"min_silence_duration_ms": 250}
    assert call["condition_on_previous_text"] is False


def test_batch_single_window_under_limit(speech_audio):
    model = FakeWhisperModel(("a",))
    tr = Transcriber(model_override=model, batch_size=16)
    tr.transcribe(speech_audio)  # 1.5 s < 16 s window
    assert len(model.calls) == 1


def test_batch_splits_long_audio():
    model = FakeWhisperModel(("chunk",))
    tr = Transcriber(model_override=model, batch_size=2)
    audio = np.ones(16000 * 5, dtype=np.float32) * 0.2  # 5 s loud
    out = tr.transcribe(audio)
    assert len(model.calls) == 3  # 2 s + 2 s + 1 s windows
    assert out["text"] == "chunk chunk chunk"


def test_batch_skips_tiny_tail():
    model = FakeWhisperModel(("chunk",))
    tr = Transcriber(model_override=model, batch_size=2)
    audio = np.ones(16000 * 2 + 100, dtype=np.float32) * 0.2
    tr.transcribe(audio)
    assert len(model.calls) == 1  # 100-sample tail skipped


def test_stereo_downmixed_to_mono(stereo_audio):
    model = FakeWhisperModel(("stereo ok",))
    tr = Transcriber(model_override=model)
    assert tr.transcribe(stereo_audio)["text"] == "stereo ok"
    assert model.calls[0]["n"] == stereo_audio.shape[0]


def test_low_sample_rate_resampled(low_rate_audio):
    model = FakeWhisperModel(("resampled",))
    tr = Transcriber(model_override=model)
    out = tr.transcribe(low_rate_audio, sample_rate=8000)
    assert out["text"] == "resampled"
    assert abs(model.calls[0]["n"] - 16000) < 160  # ~1 s at 16 kHz


def test_vocabulary_becomes_initial_prompt(speech_audio):
    model = FakeWhisperModel(("VoiceType rocks",))
    tr = Transcriber(model_override=model)
    tr.set_vocabulary(["VoiceType", "GitHub"])
    assert tr.initial_prompt == "VoiceType GitHub"
    tr.transcribe(speech_audio)
    assert model.calls[0]["initial_prompt"] == "VoiceType GitHub"
    tr.set_vocabulary([])
    assert tr.initial_prompt == ""


def test_missing_faster_whisper_raises_helpfully(speech_audio, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    tr = Transcriber()  # no override -> lazy import on transcribe
    with pytest.raises(ImportError):
        tr.transcribe(speech_audio)


def test_last_info_tracks_language_and_segments(speech_audio):
    tr = Transcriber(model_override=FakeWhisperModel(("a", "b"), language="en"))
    tr.transcribe(speech_audio)
    assert tr.last_info == {"language": "en", "n_segments": 2}


class ScoredSeg:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=0.0):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class ScoredModel(FakeWhisperModel):
    def __init__(self, segs):
        self._segs = segs
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append({"n": int(np.asarray(audio).size), **kwargs})
        info = type("Info", (), {"language": "en"})()
        return list(self._segs), info


def test_hallucinated_segments_are_dropped(speech_audio):
    model = ScoredModel([ScoredSeg("thanks for watching", no_speech_prob=0.95),
                         ScoredSeg("real words", no_speech_prob=0.1)])
    tr = Transcriber(model_override=model)
    assert tr.transcribe(speech_audio)["text"] == "real words"


def test_low_confidence_segments_are_dropped(speech_audio):
    model = ScoredModel([ScoredSeg("mumble", avg_logprob=-2.5),
                         ScoredSeg("clear speech", avg_logprob=-0.2)])
    tr = Transcriber(model_override=model)
    assert tr.transcribe(speech_audio)["text"] == "clear speech"


def test_all_filtered_yields_empty_text(speech_audio):
    model = ScoredModel([ScoredSeg("noise", no_speech_prob=0.99)])
    tr = Transcriber(model_override=model)
    out = tr.transcribe(speech_audio)
    assert out["text"] == "" and out["segments"] == []


def test_confidence_thresholds_forwarded(speech_audio):
    model = FakeWhisperModel(("hi",))
    tr = Transcriber(model_override=model, no_speech_threshold=0.7,
                     log_prob_threshold=-0.5)
    tr.transcribe(speech_audio)
    assert model.calls[0]["no_speech_threshold"] == 0.7
    assert model.calls[0]["log_prob_threshold"] == -0.5
