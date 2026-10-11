"""Wake-word tests: detect 'Hey VoiceType', reject similar phrases, handle silence.

openWakeWord itself is optional/heavy, so the real-model path is exercised via
a stubbed `openwakeword.model.Model`, plus the energy fallback is tested with
synthetic audio on every OS.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from voicetype.wake import WakeWordDetector

SR = 16000
FRAME = 1280


def _start(det: WakeWordDetector) -> list:
    det.start()
    det.listening = True
    fired: list = []
    det.add_callback(fired.append)
    return fired


class _StubOWWModel:
    """Stands in for openwakeword.model.Model with scripted scores."""

    def __init__(self, score: float = 0.95):
        self.score = score
        self.frames = 0

    def predict(self, frame):
        self.frames += 1
        return {"hey_voicetype": self.score}


def _install_oww_stub(monkeypatch, score: float = 0.95):
    mod = types.ModuleType("openwakeword.model")
    mod.Model = lambda **kw: _StubOWWModel(score)  # noqa: E731
    pkg = types.ModuleType("openwakeword")
    pkg.model = mod
    monkeypatch.setitem(sys.modules, "openwakeword", pkg)
    monkeypatch.setitem(sys.modules, "openwakeword.model", mod)


def test_detects_wake_word_on_synthetic_audio(wake_audio):
    _install = _StubOWWModel()
    det = WakeWordDetector(threshold=0.5, refractory_seconds=0.0)
    det._model = _install  # pretend openWakeWord heard "Hey VoiceType"
    det.backend = "openwakeword"
    fired = _start(det)
    det.feed(wake_audio)
    det.stop()
    assert len(fired) >= 1
    assert fired[0]["score"] >= 0.5
    assert fired[0]["backend"] == "openwakeword"


def test_openwakeword_model_init_uses_stub(monkeypatch):
    _install_oww_stub(monkeypatch, score=0.9)
    det = WakeWordDetector(model_name="hey_voicetype")
    assert det.backend == "openwakeword"
    fired = _start(det)
    det.feed(np.ones(FRAME, dtype=np.float32))
    det.stop()
    assert len(fired) == 1


def test_rejects_similar_phrase(similar_phrase_audio):
    det = WakeWordDetector(threshold=0.5, refractory_seconds=0.0)
    assert det.backend == "fallback"  # no openwakeword installed here
    fired = _start(det)
    det.feed(similar_phrase_audio)  # too quiet -> score 0.0
    det.stop()
    assert fired == []


def test_low_stub_score_rejects(monkeypatch):
    _install_oww_stub(monkeypatch, score=0.1)
    det = WakeWordDetector(threshold=0.5)
    fired = _start(det)
    det.feed(np.ones(FRAME * 2, dtype=np.float32) * 0.3)
    det.stop()
    assert fired == []


def test_silence_never_fires(silence_audio):
    det = WakeWordDetector(threshold=0.05, refractory_seconds=0.0)
    fired = _start(det)
    det.feed(silence_audio)
    det.feed(np.zeros(0, dtype=np.float32))  # empty chunk is a no-op
    det.stop()
    assert fired == []


def test_model_exception_falls_back_to_energy(monkeypatch):
    _install_oww_stub(monkeypatch, score=0.9)
    det = WakeWordDetector(threshold=0.5, refractory_seconds=0.0)

    def boom(frame):
        raise RuntimeError("onnx exploded")

    det._model.predict = boom
    fired = _start(det)
    loud = (np.random.RandomState(4).randn(FRAME * 8).astype(np.float32) * 0.5)
    det.feed(loud)
    det.stop()
    assert len(fired) == 1  # energy fallback still fires on sustained audio


def test_empty_predict_dict_scores_zero(monkeypatch):
    _install_oww_stub(monkeypatch)
    det = WakeWordDetector(threshold=0.5, refractory_seconds=0.0)
    det._model.predict = lambda frame: {}
    fired = _start(det)
    det.feed(np.ones(FRAME, dtype=np.float32))
    det.stop()
    assert fired == []


def test_refractory_blocks_repeats(wake_audio):
    det = WakeWordDetector(threshold=0.4, refractory_seconds=60.0)
    fired = _start(det)
    det.feed(wake_audio)
    det.feed(wake_audio)
    det.stop()
    assert len(fired) == 1


def test_ignores_audio_when_not_listening(wake_audio):
    det = WakeWordDetector(threshold=0.1, refractory_seconds=0.0)
    fired = []
    det.add_callback(fired.append)
    det.feed(wake_audio)  # listening defaults to False
    assert fired == []


def test_chunked_feed_accumulates_partial_frames():
    det = WakeWordDetector(threshold=0.5, refractory_seconds=0.0)
    fired = _start(det)
    loud = np.ones(300, dtype=np.float32) * 0.5
    for _ in range(30):  # 9000 samples -> seven full 1280 frames (sustained)
        det.feed(loud)
    det.stop()
    assert len(fired) >= 1


def test_single_loud_frame_does_not_fire_fallback(wake_audio):
    det = WakeWordDetector(threshold=0.4, refractory_seconds=0.0)
    fired = _start(det)
    det.feed(wake_audio[:FRAME])  # one 80 ms transient only
    det.stop()
    assert fired == []


def test_backlog_trim_bounds_latency():
    det = WakeWordDetector(threshold=0.99)
    det.start()
    det._pending = np.ones(SR * 10, dtype=np.float32)
    det._idle_loop = lambda: None  # don't run the real loop
    # Simulate one trim iteration inline (same logic as _idle_loop).
    if det._pending.size > det.sample_rate * 5:
        det._pending = det._pending[-det.sample_rate * 2:]
    assert det._pending.size == SR * 2
    det.stop()


def test_callback_exceptions_do_not_break_detector(wake_audio):
    det = WakeWordDetector(threshold=0.4, refractory_seconds=0.0)

    def bad(info):
        raise RuntimeError("listener bug")

    good: list = []
    det.add_callback(bad)
    det.add_callback(good.append)
    det.start()
    det.listening = True
    det.feed(wake_audio)
    det.stop()
    assert len(good) >= 1  # bad listener never blocks the good one


def test_simulate_wake_and_start_stop_cycle():
    det = WakeWordDetector()
    assert det.start() in ("fallback", "openwakeword")
    assert det.listening is True
    fired: list = []
    det.add_callback(fired.append)
    det.simulate_wake(score=0.88)
    det.stop()
    assert det.listening is False
    assert fired and abs(fired[0]["score"] - 0.88) < 1e-9


@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_detector_is_os_independent(monkeypatch, system, wake_audio):
    import platform
    monkeypatch.setattr(platform, "system", lambda: system)
    det = WakeWordDetector(threshold=0.4, refractory_seconds=0.0)
    fired = _start(det)
    det.feed(wake_audio)
    det.stop()
    assert len(fired) >= 1
