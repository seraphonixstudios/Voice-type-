import numpy as np

from voicetype.audio_ring import AudioRingBuffer


def test_write_and_read_latest():
    rb = AudioRingBuffer(seconds=2.0)
    rb.write(np.arange(100, dtype=np.float32))
    tail = rb.read_latest(10)
    assert list(tail) == [float(x) for x in range(90, 100)]


def test_wraparound_preserves_order():
    rb = AudioRingBuffer(sample_rate=10, seconds=1.0)  # capacity 10
    rb.write(np.arange(7, dtype=np.float32))
    rb.write(np.arange(7, 15, dtype=np.float32))
    assert list(rb.read_all()) == [float(x) for x in range(5, 15)]


def test_oversize_write_keeps_tail():
    rb = AudioRingBuffer(sample_rate=10, seconds=1.0)
    rb.write(np.arange(25, dtype=np.float32))
    assert list(rb.read_all()) == [float(x) for x in range(15, 25)]


def test_clear():
    rb = AudioRingBuffer(seconds=1.0)
    rb.write(np.ones(100, dtype=np.float32))
    rb.clear()
    assert len(rb) == 0
    assert rb.read_all().size == 0
