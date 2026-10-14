"""Microphone capture: sounddevice 16kHz mono -> AudioRingBuffer + listeners."""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from voicetype.audio_ring import AudioRingBuffer

log = logging.getLogger("voicetype.capture")


@dataclass
class CaptureStats:
    callbacks: int = 0
    dropped_chunks: int = 0
    last_callback_at: float = 0.0


class AudioCapture:
    """Wraps sounddevice.InputStream. Falls back to synthetic silence if unavailable."""

    def __init__(self, ring: AudioRingBuffer, sample_rate: int = 16000,
                 channels: int = 1, chunk_ms: int = 80,
                 device=None, synthetic_fallback: bool = True):
        self.ring = ring
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.chunk_samples = int(self.sample_rate * chunk_ms / 1000)
        self.device = device
        self.synthetic_fallback = synthetic_fallback
        self._listeners: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
        self.stats = CaptureStats()
        self.backend = "sounddevice"
        self.device_name: str | None = None
        self._capture_rate = int(sample_rate)  # actual device rate (resampled to sample_rate)
        self._lock = threading.Lock()

    @staticmethod
    def list_input_devices() -> list[dict]:
        """Enumerate OS input devices. Never raises (returns [] when unknown).

        Muting ONE mic never mutes the others: if VoiceType types while your
        mic is muted, it is almost certainly listening on a different,
        unmuted input (webcam mic, headset, Stereo Mix...). This list + the
        `audio.device` config pin it to the right one.
        """
        try:
            import sounddevice as sd  # type: ignore
            return [dict(d) for d in sd.query_devices()
                    if d.get("max_input_channels", 0) > 0]
        except Exception:
            return []

    def resolve_device_name(self) -> str:
        if self.device_name:
            return self.device_name
        try:
            import sounddevice as sd  # type: ignore
            info = sd.query_devices(self.device, "input")
            if isinstance(info, dict) and info.get("name"):
                self.device_name = str(info["name"])
        except Exception:
            pass
        return self.device_name or "(system default input)"

    def add_listener(self, fn) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        with self._lock:
            self._listeners = [f for f in self._listeners if f is not fn]

    def _emit(self, chunk: np.ndarray) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(chunk)
            except Exception:
                continue

    def _query_native_rate(self) -> int | None:
        try:
            import sounddevice as sd  # type: ignore
            info = sd.query_devices(self.device, "input")
        except Exception:
            return None
        if not isinstance(info, dict):
            return None
        try:
            rate = float(info.get("default_samplerate") or 0)
        except (TypeError, ValueError):
            return None
        return int(rate) if rate > 0 else None

    @staticmethod
    def _resample(mono: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if src_rate == dst_rate or mono.size < 2:
            return mono
        n = max(1, int(round(mono.size * dst_rate / src_rate)))
        xp = np.linspace(0, 1, mono.size)
        return np.interp(np.linspace(0, 1, n), xp, mono).astype(np.float32)

    def _callback(self, indata, frames, time_info, status) -> None:  # sounddevice signature
        mono = np.asarray(indata, dtype=np.float32).reshape(-1)
        if mono.size == 0:
            return
        if getattr(self, "_capture_rate", self.sample_rate) != self.sample_rate:
            mono = self._resample(mono, self._capture_rate, self.sample_rate)
        self.ring.write(mono)
        self.stats.callbacks += 1
        self.stats.last_callback_at = time.time()
        try:
            self._q.put_nowait(mono.copy())
        except queue.Full:
            self.stats.dropped_chunks += 1
        self._emit(mono.copy())

    def _dispatch_loop(self) -> None:
        # Drain queue for listeners registered late; main emit already happened
        # in _callback, so this loop only keeps stats fresh for synthetic mode.
        while not self._stop.is_set():
            if self.backend == "synthetic":
                chunk = np.zeros(self.chunk_samples, dtype=np.float32)
                self.ring.write(chunk)
                self.stats.callbacks += 1
                self.stats.last_callback_at = time.time()
                self._emit(chunk)
                time.sleep(self.chunk_samples / self.sample_rate)
            else:
                time.sleep(0.05)

    def start(self) -> str:
        self._stop.clear()
        self._capture_rate = self.sample_rate
        try:
            import sounddevice as sd  # type: ignore
            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="float32",
                    blocksize=self.chunk_samples,
                    device=self.device,
                    callback=self._callback,
                )
            except Exception:
                # Many USB mics (e.g. Samson Satellite over WASAPI) reject
                # 16 kHz outright (PaError -9997). Reopen at the device's
                # native rate and resample each chunk in the callback.
                native = self._query_native_rate()
                if not native or native == self.sample_rate:
                    raise
                native_block = max(64, int(round(
                    self.chunk_samples * native / self.sample_rate)))
                self._stream = sd.InputStream(
                    samplerate=native,
                    channels=self.channels,
                    dtype="float32",
                    blocksize=native_block,
                    device=self.device,
                    callback=self._callback,
                )
                self._capture_rate = native
                log.info("device needs %d Hz; resampling to %d Hz",
                         native, self.sample_rate)
            self._stream.start()
            self.backend = "sounddevice"
            self.resolve_device_name()
            log.info("capturing from input device: %s", self.device_name)
        except Exception as exc:
            if not self.synthetic_fallback:
                raise
            self.backend = "synthetic"
            self._stream = None
            log.warning("no microphone available (%s); synthetic silence", exc)
        self._thread = threading.Thread(target=self._dispatch_loop, name="vt-capture", daemon=True)
        self._thread.start()
        return self.backend

    def read_chunk(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        self._stream = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # Test helper: inject audio without hardware.
    def feed(self, samples: np.ndarray) -> None:
        data = np.asarray(samples, dtype=np.float32).reshape(-1)
        self.ring.write(data)
        self._emit(data.copy())
