"""Thread-safe ring buffer for 16kHz mono float32 audio."""
from __future__ import annotations

import threading

import numpy as np


class AudioRingBuffer:
    """Circular buffer. `write` appends, `read_latest`/`read_all` consume copies."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1, seconds: float = 30.0):
        if channels != 1:
            raise ValueError("AudioRingBuffer only supports mono (channels=1)")
        self.sample_rate = int(sample_rate)
        self.capacity = int(self.sample_rate * float(seconds))
        self._buf = np.zeros(self.capacity, dtype=np.float32)
        self._pos = 0  # next write index
        self._count = 0  # valid samples (<= capacity)
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return self._count

    @property
    def capacity_samples(self) -> int:
        return self.capacity

    def write(self, samples: np.ndarray) -> None:
        data = np.asarray(samples, dtype=np.float32).reshape(-1)
        if data.size == 0:
            return
        # Keep only the tail if chunk exceeds capacity.
        if data.size >= self.capacity:
            data = data[-self.capacity:]
        with self._lock:
            first = min(data.size, self.capacity - self._pos)
            self._buf[self._pos:self._pos + first] = data[:first]
            rest = data.size - first
            if rest:
                self._buf[0:rest] = data[first:]
            self._pos = (self._pos + data.size) % self.capacity
            self._count = min(self.capacity, self._count + data.size)

    def read_latest(self, n_samples: int) -> np.ndarray:
        """Return a copy of the most recent n_samples (or fewer if unavailable)."""
        with self._lock:
            n = min(int(n_samples), self._count)
            if n <= 0:
                return np.zeros(0, dtype=np.float32)
            start = (self._pos - n) % self.capacity
            if start + n <= self.capacity:
                return self._buf[start:start + n].copy()
            part1 = self._buf[start:].copy()
            part2 = self._buf[0:(start + n) % self.capacity].copy()
            return np.concatenate([part1, part2])

    def read_all(self) -> np.ndarray:
        with self._lock:
            if self._count == 0:
                return np.zeros(0, dtype=np.float32)
            if self._count < self.capacity:
                return self._buf[0:self._count].copy()
            # Full: oldest sample is at _pos.
            return np.concatenate([self._buf[self._pos:], self._buf[:self._pos]]).copy()

    def clear(self) -> None:
        with self._lock:
            self._pos = 0
            self._count = 0
            self._buf.fill(0)
