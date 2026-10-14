import time

import numpy as np

from voicetype.wake import WakeWordDetector


def test_fallback_detector_fires_on_sustained_audio():
    det = WakeWordDetector(threshold=0.5, refractory_seconds=0.0)
    det.start()
    det.listening = True
    fired = []
    det.add_callback(lambda info: fired.append(info))
    rng = np.random.RandomState(0)
    for _ in range(8):  # ~0.64 s of sustained loud audio
        det.feed((rng.randn(1280).astype(np.float32) * 0.5))
    det.stop()
    assert len(fired) >= 1


def test_single_transient_does_not_fire():
    det = WakeWordDetector(threshold=0.5, refractory_seconds=0.0)
    det.start()
    det.listening = True
    fired = []
    det.add_callback(lambda info: fired.append(info))
    # One loud 80 ms clack/cough: must never start a recording.
    det.feed((np.random.RandomState(0).randn(1280).astype(np.float32) * 0.5))
    det.stop()
    assert fired == []


def test_sparse_noise_never_fires():
    det = WakeWordDetector(threshold=0.5, refractory_seconds=0.0, trigger_frames=3)
    det.start()
    det.listening = True
    fired = []
    det.add_callback(lambda info: fired.append(info))
    loud = np.ones(1280, dtype=np.float32) * 0.5
    quiet = np.zeros(1280, dtype=np.float32)
    # Isolated clacks with long quiet gaps: window never holds 3 loud frames.
    for _ in range(3):
        det.feed(loud)
        det.feed(quiet)
        det.feed(quiet)
        det.feed(quiet)
    det.stop()
    assert fired == []


def test_syllable_gaps_do_not_reset_window():
    # Mirrors real speech: loud syllables separated by quiet gaps still fire.
    det = WakeWordDetector(threshold=0.5, refractory_seconds=0.0, trigger_frames=6)
    det.start()
    det.listening = True
    fired = []
    det.add_callback(lambda info: fired.append(info))
    loud = np.ones(1280, dtype=np.float32) * 0.5
    quiet = np.zeros(1280, dtype=np.float32)
    for _ in range(3):
        det.feed(loud)
        det.feed(loud)
        det.feed(quiet)
    det.stop()
    assert len(fired) == 1


def test_detector_ignores_silence():
    det = WakeWordDetector(threshold=0.5, refractory_seconds=60.0)
    det.start()
    det.listening = True
    fired = []
    det.add_callback(lambda info: fired.append(info))
    det.feed(np.zeros(1280 * 4, dtype=np.float32))
    det.stop()
    assert fired == []


def test_refractory_period_suppresses_repeats():
    det = WakeWordDetector(threshold=0.1, refractory_seconds=60.0, trigger_frames=2)
    det.start()
    det.listening = True
    fired = []
    det.add_callback(lambda info: fired.append(info))
    loud = np.ones(1280, dtype=np.float32) * 0.5
    for _ in range(4):  # two sustained bursts inside one refractory window
        det.feed(loud)
    det.stop()
    assert len(fired) == 1


def test_simulate_wake():
    det = WakeWordDetector()
    fired = []
    det.add_callback(lambda info: fired.append(info))
    det.simulate_wake()
    assert len(fired) == 1 and fired[0]["score"] > 0.5
