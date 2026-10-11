"""openWakeWord daemon: always-listening wake thread, low CPU.

Uses the real openWakeWord model when installed plus a model file is present,
otherwise an energy-based fallback detector so the pipeline stays functional
(used by tests and machines without a custom 'Hey VoiceType' model).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np


class WakeWordDetector:
    def __init__(self, sample_rate: int = 16000, threshold: float = 0.5,
                 refractory_seconds: float = 2.0, model_name: str = "hey_jarvis",
                 model_path: str = "", chunk_samples: int = 1280,
                 trigger_frames: int = 6):
        self.sample_rate = sample_rate
        self.threshold = float(threshold)
        self.refractory_seconds = float(refractory_seconds)
        self.model_name = model_name
        self.model_path = model_path
        self.chunk_samples = int(chunk_samples)
        # Energy fallback only: consecutive loud frames required before firing.
        # A single clack/cough must never start a recording; ~0.5 s of
        # sustained speech-like energy (≈6 x 80 ms frames) must.
        self.trigger_frames = max(1, int(trigger_frames))
        self._loud_streak = 0
        self._callbacks: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_fire = 0.0
        self._lock = threading.Lock()
        self.backend = "fallback"
        self._model = None
        self.listening = False
        self._pending = np.zeros(0, dtype=np.float32)
        self._init_model()

    def _init_model(self) -> None:
        try:
            from openwakeword.model import Model as OWWModel  # type: ignore
            kw: dict = {}
            if self.model_path and Path(self.model_path).expanduser().exists():
                kw["wakeword_models"] = [str(Path(self.model_path).expanduser())]
            elif self.model_name:
                kw["wakeword_models"] = [self.model_name]
            else:
                return
            # inference_framework onnx keeps CPU usage low.
            self._model = OWWModel(inference_framework="onnx", **kw)
            self.backend = "openwakeword"
        except Exception:
            self._model = None
            self.backend = "fallback"

    def add_callback(self, fn) -> None:
        with self._lock:
            self._callbacks.append(fn)

    def feed(self, chunk: np.ndarray) -> None:
        """Push 16kHz mono float32 audio. Called by AudioCapture listener."""
        if not self.listening:
            return
        data = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if data.size == 0:
            return
        self._pending = np.concatenate([self._pending, data]) if self._pending.size else data.copy()
        while self._pending.size >= self.chunk_samples:
            frame, self._pending = self._pending[:self.chunk_samples], self._pending[self.chunk_samples:]
            self._score_frame(frame)

    def _score_frame(self, frame: np.ndarray) -> None:
        score = 0.0
        from_model = self._model is not None
        if from_model:
            try:
                out = self._model.predict(frame)
                if isinstance(out, dict) and out:
                    score = float(max(out.values()))
                else:
                    score = 0.0
            except Exception:
                score = self._fallback_score(frame)
                from_model = False
        else:
            score = self._fallback_score(frame)
        if score < self.threshold:
            self._loud_streak = 0
            return
        if from_model:
            # A real classifier verdict counts immediately.
            self._maybe_fire(score)
            return
        # Energy fallback: demand sustained loudness, not a single transient.
        self._loud_streak += 1
        if self._loud_streak >= self.trigger_frames:
            self._loud_streak = 0
            self._maybe_fire(score)

    def _maybe_fire(self, score: float) -> None:
        now = time.time()
        if now - self._last_fire >= self.refractory_seconds:
            self._last_fire = now
            self._fire(score)

    @staticmethod
    def _fallback_score(frame: np.ndarray) -> float:
        # Energy gate: sustained loud speech-ish frames score high.
        # Quiet frames score ~0 so idle CPU stays low and false fires are rare.
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2) + 1e-12))
        if rms < 0.02:
            return 0.0
        if rms > 0.4:
            return 0.9
        return min(0.9, (rms - 0.02) / 0.38 * 0.9)

    def _fire(self, score: float) -> None:
        with self._lock:
            cbs = list(self._callbacks)
        for fn in cbs:
            try:
                fn({"score": score, "at": time.time(), "backend": self.backend})
            except Exception:
                continue

    # ---- daemon thread (keeps "always listening" without busy-looping) ----
    def start(self) -> str:
        self._stop.clear()
        self.listening = True
        # The daemon thread itself does no audio I/O; audio arrives via feed().
        # It only trims stale buffers so idle CPU is ~0.
        self._thread = threading.Thread(target=self._idle_loop, name="vt-wake", daemon=True)
        self._thread.start()
        return self.backend

    def _idle_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.25)
            # Drop excessive backlog (e.g. after sleep/resume) to bound latency.
            if self._pending.size > self.sample_rate * 5:
                self._pending = self._pending[-self.sample_rate * 2:]

    def stop(self) -> None:
        self.listening = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    # Test helper: force a wake event.
    def simulate_wake(self, score: float = 0.99) -> None:
        self._last_fire = 0.0
        self._fire(score)
