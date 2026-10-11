"""Shared pytest fixtures: synthetic audio, mock Ollama, mock platform APIs."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SR = 16000


def _tone(freq: float, seconds: float, amp: float = 0.25, seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    t = np.arange(int(SR * seconds)) / SR
    wave = amp * np.sin(2 * np.pi * freq * t)
    wave = wave + 0.02 * rng.randn(wave.size)  # breath / room noise
    return wave.astype(np.float32)


@pytest.fixture
def sample_rate() -> int:
    return SR


@pytest.fixture
def silence_audio() -> np.ndarray:
    """Pure digital silence (VAD must skip, wake must ignore)."""
    return np.zeros(SR, dtype=np.float32)


@pytest.fixture
def wake_audio() -> np.ndarray:
    """Loud speech-like burst standing in for 'Hey VoiceType'."""
    return _tone(220.0, 1.0, amp=0.35, seed=1)


@pytest.fixture
def similar_phrase_audio() -> np.ndarray:
    """Very quiet murmur standing in for a similar-but-wrong phrase."""
    return _tone(180.0, 1.0, amp=0.005, seed=2)


@pytest.fixture
def speech_audio() -> np.ndarray:
    return _tone(440.0, 1.5, amp=0.2, seed=3)


@pytest.fixture
def stereo_audio(speech_audio) -> np.ndarray:
    return np.stack([speech_audio, speech_audio], axis=1)


@pytest.fixture
def low_rate_audio() -> np.ndarray:
    t = np.arange(8000) / 8000.0
    return (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


class FakeOllamaResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def mock_ollama(monkeypatch):
    """Capture Ollama requests; default reply is a scroll-down command."""
    calls: dict = {"post": [], "get": []}

    def fake_post(url, json=None, timeout=None):
        calls["post"].append({"url": url, "json": json, "timeout": timeout})
        return FakeOllamaResponse(
            {"response": 'COMMAND: {"action": "scroll", "target": "down", "value": "3"}'})

    def fake_get(url, timeout=None):
        calls["get"].append({"url": url})
        return FakeOllamaResponse({"models": []})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    return calls


@pytest.fixture
def mock_ollama_down(monkeypatch):
    import requests

    def boom(*a, **k):
        raise ConnectionError("ollama is down")

    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "get", boom)


@pytest.fixture
def mock_platform_mac(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    return "Darwin"


@pytest.fixture
def mock_platform_windows(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    return "Windows"


@pytest.fixture
def mock_platform_linux(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    return "Linux"


@pytest.fixture
def test_config_dict():
    from voicetype.config_manager import load_config
    return load_config(overrides={"injection": {"backend": "fallback"}})


@pytest.fixture
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        pytest.skip("PySide6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app
