"""Mic-chain probe: record a few seconds, report wake scores + speech fraction.

Usage: run it, then speak "Hey VoiceType ..." clearly until it finishes.
Tells you whether the mic, the wake gate, or the speech gate is dropping you.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from voicetype.capture import AudioCapture, AudioRingBuffer
from voicetype.wake import WakeWordDetector

SECONDS = 8


def main() -> int:
    from voicetype.config_manager import ConfigManager
    cfg = ConfigManager().config
    device = ((cfg.get("audio") or {}).get("device"))
    print(f"device: {device}")
    ring = AudioRingBuffer(16000, 1, 30.0)
    cap = AudioCapture(ring, 16000, 1, 80, device=device)
    backend = cap.start()
    print(f"backend: {backend} (rate={cap._capture_rate})")
    print(f"SPEAK NOW - saying 'Hey VoiceType, hello world' on repeat for {SECONDS}s...")
    time.sleep(SECONDS)
    cap.stop()
    audio = ring.read_all()
    print(f"captured {audio.size / 16000:.1f}s, overall RMS={rms(audio):.4f}")
    if audio.size < 1600:
        print("VERDICT: almost no audio arrived - mic is not delivering samples")
        return 1
    frames = [audio[i:i + 1280] for i in range(0, len(audio) - 1280 + 1, 1280)]
    scores = [WakeWordDetector._fallback_score(f) for f in frames]
    loud = sum(1 for s in scores if s >= 0.5)
    print(f"frames={len(frames)} max_score={max(scores):.2f} "
          f"frames_over_threshold={loud} (need 6 in a row to fire)")
    streak = best = 0
    for s in scores:
        streak = streak + 1 if s >= 0.5 else 0
        best = max(best, streak)
    print(f"best sustained streak: {best} frames")
    frac_frames = [audio[i:i + 1600] for i in range(0, len(audio) - 1600 + 1, 1600)]
    speech = sum(1 for f in frac_frames if rms(f) >= 0.015) / max(1, len(frac_frames))
    print(f"speech fraction: {speech:.2f} (need >= 0.15 to transcribe)")
    if best >= 6 and speech >= 0.15:
        print("VERDICT: chain would fire - if the app still types nothing, "
              "the fault is downstream (model/transcribe/inject)")
    elif best < 6:
        print("VERDICT: wake gate drops you - speak louder/closer/longer")
    else:
        print("VERDICT: speech gate drops you - too much silence vs speech")
    return 0


def rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a.astype(np.float64) ** 2) + 1e-12))


if __name__ == "__main__":
    raise SystemExit(main())
