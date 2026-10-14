"""Local faster-whisper large-v3 transcription with VAD + batching."""
from __future__ import annotations

import threading

import numpy as np


class Transcriber:
    """Lazy-loading wrapper around faster-whisper.

    `model_override` (any object with `.transcribe(audio, ...)` returning
    iterable of segments with `.text`) may be injected for tests/offline use.
    """

    def __init__(self, model_name: str = "large-v3", device: str = "cpu",
                 compute_type: str = "int8", beam_size: int = 5,
                 vad_filter: bool = True, vad_min_silence_ms: int = 500,
                 language: str = "en", batch_size: int = 16,
                 initial_prompt: str = "", model_override=None,
                 no_speech_threshold: float = 0.6,
                 log_prob_threshold: float = -1.0):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.beam_size = int(beam_size)
        self.vad_filter = bool(vad_filter)
        self.vad_min_silence_ms = int(vad_min_silence_ms)
        self.language = language
        self.batch_size = max(1, int(batch_size))
        self.initial_prompt = initial_prompt
        # Hallucination guards: drop segments the model itself flags as
        # non-speech or low-confidence, so noise never becomes typed text.
        self.no_speech_threshold = float(no_speech_threshold)
        self.log_prob_threshold = float(log_prob_threshold)
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.beam_size = int(beam_size)
        self.vad_filter = bool(vad_filter)
        self.vad_min_silence_ms = int(vad_min_silence_ms)
        self.language = language
        self.batch_size = max(1, int(batch_size))
        self.initial_prompt = initial_prompt
        self._model = model_override
        self._lock = threading.Lock()
        self.backend = "override" if model_override is not None else "faster-whisper"
        self.last_info: dict = {}

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from voicetype._dll_setup import ensure_cuda_dlls
                ensure_cuda_dlls()
            except Exception:
                pass
            from faster_whisper import WhisperModel  # type: ignore
            self._model = WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type
            )
            self.backend = "faster-whisper"
            return self._model

    @staticmethod
    def _to_mono16k(audio: np.ndarray, sample_rate: int) -> np.ndarray:
        a = np.asarray(audio, dtype=np.float32)
        if a.ndim == 2:
            a = a.mean(axis=1)
        a = a.reshape(-1)
        if sample_rate != 16000 and a.size > 1:
            # Linear resample (no scipy dependency).
            dur = a.size / float(sample_rate)
            n = int(dur * 16000)
            if n > 0:
                xp = np.linspace(0, 1, a.size)
                fp = np.linspace(0, 1, n)
                a = np.interp(fp, xp, a).astype(np.float32)
        return a

    def _transcribe_call(self, model, audio: np.ndarray):
        return model.transcribe(
            audio,
            beam_size=self.beam_size,
            language=self.language,
            vad_filter=self.vad_filter,
            vad_parameters={"min_silence_duration_ms": self.vad_min_silence_ms},
            initial_prompt=self.initial_prompt or None,
            condition_on_previous_text=False,
            no_speech_threshold=self.no_speech_threshold,
            log_prob_threshold=self.log_prob_threshold,
        )

    def _batched_segments(self, model, audio: np.ndarray):
        """Split long audio into batch_size-second windows for bounded memory."""
        win = self.batch_size  # seconds per batch window
        total = audio.size / 16000.0
        if total <= win:
            segments, info = self._transcribe_call(model, audio)
            return list(segments), info
        out, infos = [], []
        step = int(win * 16000)
        for start in range(0, audio.size, step):
            chunk = audio[start:start + step]
            if chunk.size < 16000 * 0.25:
                continue  # skip tiny tail (VAD would drop it anyway)
            segments, info = self._transcribe_call(model, chunk)
            out.extend(list(segments))
            infos.append(info)
        return out, (infos[0] if infos else None)

    def _keep_segment(self, seg) -> bool:
        """Drop hallucinations: non-speech or low-confidence segments."""
        try:
            if float(getattr(seg, "no_speech_prob", 0.0) or 0.0) > self.no_speech_threshold:
                return False
        except (TypeError, ValueError):
            pass
        try:
            if float(getattr(seg, "avg_logprob", 0.0) or 0.0) < self.log_prob_threshold:
                return False
        except (TypeError, ValueError):
            pass
        return True

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> dict:
        mono = self._to_mono16k(audio, sample_rate)
        if mono.size == 0:
            return {"text": "", "segments": [], "language": self.language, "backend": self.backend}
        # Skip pure silence without waking the model (VAD pre-check).
        rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2) + 1e-12))
        if rms < 1e-4:
            return {"text": "", "segments": [], "language": self.language, "backend": self.backend}
        model = self._ensure_model()
        segments, info = self._batched_segments(model, mono)
        kept = [s for s in segments if self._keep_segment(s)]
        texts = [(getattr(s, "text", "") or "") for s in kept]
        text = " ".join(t.strip() for t in texts if t and t.strip())
        text = " ".join(text.split())
        lang = getattr(info, "language", self.language) if info is not None else self.language
        self.last_info = {"language": lang, "n_segments": len(texts),
                            "n_raw_segments": len(segments),
                            "confidence": self._mean_confidence(kept)}
        return {"text": text, "segments": texts, "language": lang, "backend": self.backend}

    @staticmethod
    def _mean_confidence(segments) -> float:
        scores = []
        for s in segments:
            try:
                scores.append(float(getattr(s, "avg_logprob", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        return sum(scores) / len(scores) if scores else 0.0

    def set_vocabulary(self, words: list[str]) -> None:
        words = [w for w in (words or []) if w]
        self.initial_prompt = " ".join(words) if words else ""
