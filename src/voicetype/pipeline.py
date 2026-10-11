"""End-to-end pipeline: wake -> record utterance -> transcribe -> parse -> act."""
from __future__ import annotations

import subprocess
import threading
import time

import numpy as np

from voicetype.audio_ring import AudioRingBuffer
from voicetype.capture import AudioCapture
from voicetype.context_layer import ContextLayer
from voicetype.injector import BaseInjector, get_injector
from voicetype.ollama_parser import CommandParser
from voicetype.transcriber import Transcriber
from voicetype.wake import WakeWordDetector


class CommandExecutor:
    """Runs COMMAND actions via injector / OS."""

    def __init__(self, injector: BaseInjector, context: ContextLayer):
        self.injector = injector
        self.context = context
        self.history: list[dict] = []

    def execute(self, cmd: dict) -> dict:
        action = (cmd.get("action") or "").lower()
        target = (cmd.get("target") or "")
        value = (cmd.get("value") or "")
        result = {"action": action, "target": target, "value": value, "ok": True}
        try:
            if action == "delete":
                self.injector.delete(target or "selection", value)
            elif action == "new_paragraph":
                self.injector.new_paragraph()
            elif action == "switch_app" or action == "open_app":
                self._switch_app(target or value)
            elif action == "scroll":
                try:
                    amt = int(value) if str(value).strip().isdigit() else 3
                except Exception:
                    amt = 3
                self.injector.scroll(target or "down", amt)
            elif action == "undo":
                self.injector.undo()
            elif action == "redo":
                self.injector.redo()
            elif action == "copy":
                self.injector.press("command" if self._is_mac() else "ctrl", "c")
            elif action == "paste":
                self.injector.press("command" if self._is_mac() else "ctrl", "v")
            elif action == "select_all":
                self.injector.press("command" if self._is_mac() else "ctrl", "a")
            elif action == "press_key":
                self.injector.press(*[p for p in (target or value).split("+") if p])
            elif action == "type_text":
                self.injector.type_text(value or target)
            else:
                result["ok"] = False
                result["error"] = f"unknown action: {action}"
        except Exception as exc:
            result["ok"] = False
            result["error"] = str(exc)
        self.history.append({**result, "at": time.time()})
        return result

    def _is_mac(self) -> bool:
        import platform
        return platform.system() == "Darwin"

    def _switch_app(self, name: str) -> None:
        import platform
        s = platform.system()
        try:
            if s == "Darwin":
                subprocess.run(["open", "-a", name], check=False, timeout=5)
            elif s == "Windows":
                subprocess.run(["cmd", "/c", "start", "", name], check=False, timeout=5)
            else:
                subprocess.run(["xdg-open", name], check=False, timeout=5)
        except Exception:
            pass


class VoicePipeline:
    """Wires capture -> wake -> whisper -> LLM -> injector."""

    def __init__(self, config: dict, injector: BaseInjector | None = None,
                 transcriber: Transcriber | None = None,
                 parser: CommandParser | None = None,
                 context: ContextLayer | None = None,
                 capture: AudioCapture | None = None,
                 wake: WakeWordDetector | None = None):
        self.config = config
        sr = int(config.get("sample_rate", 16000))
        self.ring = AudioRingBuffer(sr, 1, float(config.get("ring_seconds", 30)))
        audio_cfg = config.get("audio", {})
        self.capture = capture or AudioCapture(
            self.ring, sr, 1, int(config.get("chunk_ms", 80)),
            device=audio_cfg.get("device"))
        ww = config.get("wake_word", {})
        self.wake = wake or WakeWordDetector(
            sr, float(ww.get("threshold", 0.5)),
            float(ww.get("refractory_seconds", 2.0)),
            model_name=ww.get("model_name", "hey_jarvis"),
            model_path=ww.get("model_path", ""),
            chunk_samples=int(sr * int(config.get("chunk_ms", 80)) / 1000),
            trigger_frames=int(ww.get("trigger_frames", 6)))
        stt = config.get("stt", {})
        self.transcriber = transcriber or Transcriber(
            stt.get("model", "large-v3"), stt.get("device", "cpu"),
            stt.get("compute_type", "int8"), int(stt.get("beam_size", 5)),
            bool(stt.get("vad_filter", True)),
            int(stt.get("vad_min_silence_ms", 500)),
            stt.get("language", "en"), int(stt.get("batch_size", 16)),
            "", None,
            float(stt.get("no_speech_threshold", 0.6)),
            float(stt.get("log_prob_threshold", -1.0)))
        self.silence_threshold = float(stt.get("silence_threshold", 0.015))
        self.transcriber.set_vocabulary(config.get("vocabulary", []))
        llm = config.get("llm", {})
        self.parser = parser or CommandParser(
            llm.get("host", "http://localhost:11434"),
            llm.get("model", "dolphin-llama3:8b"),
            int(llm.get("timeout_seconds", 30)),
            float(llm.get("temperature", 0.1)),
            int(llm.get("num_predict", 256)),
            bool(llm.get("enabled", True)))
        inj_cfg = config.get("injection", {})
        self.injector: BaseInjector = injector or get_injector(
            inj_cfg.get("backend", "auto"),
            chunk_size=int(inj_cfg.get("type_chunk_size", 32)),
            delay_ms=int(inj_cfg.get("type_delay_ms", 0)),
            use_clipboard_for_unicode=bool(inj_cfg.get("use_clipboard_for_unicode", True)),
            undo_behavior=str(inj_cfg.get("undo_behavior", "single")))
        ctx_cfg = config.get("context", {})
        self.context = context or ContextLayer(
            float(ctx_cfg.get("poll_interval_seconds", 1.0)), ctx_cfg.get("backend", "auto"))
        self.executor = CommandExecutor(self.injector, self.context)
        integ_cfg = config.get("integration", {})
        from voicetype.integration.router import IntegrationRouter
        self.router = IntegrationRouter(
            self.injector, self.context,
            clipboard_fallback=bool(integ_cfg.get("clipboard_fallback", True)),
            warn_callback=lambda msg: self._emit("integration_warning", {"warning": msg}),
            rich_text=bool(integ_cfg.get("rich_text", True)),
            ime_composition=bool(integ_cfg.get("ime_composition", True)))
        utt = config.get("utterance", {})
        self.max_seconds = float(utt.get("max_seconds", 30.0))
        self.end_silence_ms = int(utt.get("end_silence_ms", 900))
        self.pre_roll_ms = int(utt.get("pre_roll_ms", 400))
        # Anti-phantom gate: an utterance that is almost entirely room noise
        # is discarded before transcription, so Whisper never gets a chance
        # to hallucinate it into typed text.
        self.min_speech_fraction = float(utt.get("min_speech_fraction", 0.15))
        self._lock = threading.Lock()
        self._recording = False
        self.listening = True
        self.last_transcription = ""
        self.last_result: dict = {}
        self._listeners: list = []
        self.capture.add_listener(self.wake.feed)
        self.wake.add_callback(self._on_wake)

    def add_event_listener(self, fn) -> None:
        self._listeners.append(fn)

    def _emit(self, event: str, payload: dict | None = None) -> None:
        for fn in list(self._listeners):
            try:
                fn(event, payload or {})
            except Exception:
                continue

    # ---- lifecycle ----
    def start(self) -> dict:
        cap_backend = self.capture.start()
        wake_backend = self.wake.start()
        self.context.start()
        started = {"capture": cap_backend, "wake": wake_backend,
                   "device": self.capture.resolve_device_name()}
        self._emit("started", started)
        return started

    def stop(self) -> None:
        for step in (self.capture.stop, self.wake.stop, self.context.stop):
            try:
                step()
            except Exception:
                continue
        self._emit("stopped", {})

    def set_listening(self, on: bool) -> None:
        self.listening = bool(on)
        self.wake.listening = bool(on)
        self._emit("listening", {"on": self.listening})

    # ---- wake -> utterance ----
    def _on_wake(self, info: dict) -> None:
        if not self.listening:
            return
        with self._lock:
            if self._recording:
                return
            self._recording = True
        try:
            self._emit("wake", info)
            audio = self.record_utterance()
            self._process_utterance(audio)
        finally:
            with self._lock:
                self._recording = False

    def record_utterance(self) -> np.ndarray:
        sr = self.ring.sample_rate
        pre = self.ring.read_latest(int(sr * self.pre_roll_ms / 1000))
        chunks = [pre] if pre.size else []
        silence_needed = int(sr * self.end_silence_ms / 1000)
        max_n = int(sr * self.max_seconds)
        collected = int(sum(c.size for c in chunks))
        silent = 0
        frame = int(sr * 0.1)  # 100ms polling slices from the ring
        start = time.time()
        start_count = len(self.ring)
        while collected < max_n and (time.time() - start) < self.max_seconds + 5:
            time.sleep(0.1)
            fresh = self.ring.read_latest(len(self.ring) - start_count + frame)
            tail = fresh[-frame:] if fresh.size >= frame else fresh
            start_count = len(self.ring)
            if tail.size == 0:
                silent += frame
            else:
                rms = float(np.sqrt(np.mean(tail.astype(np.float64) ** 2) + 1e-12))
                if rms < self.silence_threshold:
                    silent += tail.size
                else:
                    silent = 0
                chunks.append(tail)
                collected += tail.size
            if collected > sr * 0.8 and silent >= silence_needed:
                break
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def speech_fraction(self, audio: np.ndarray) -> float:
        """Fraction of 100 ms frames at/above the silence threshold."""
        mono = np.asarray(audio, dtype=np.float32).reshape(-1)
        if mono.size == 0:
            return 0.0
        frame = int(self.ring.sample_rate * 0.1) or 1
        loud, total = 0, 0
        for start in range(0, mono.size, frame):
            chunk = mono[start:start + frame]
            total += 1
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2) + 1e-12))
            if rms >= self.silence_threshold:
                loud += 1
        return (loud / total) if total else 0.0

    def _empty_result(self, text: str = "") -> dict:
        self.last_transcription = text
        self._emit("transcribed", {"text": text})
        self.last_result = {"kind": "empty", "text": ""}
        return self.last_result

    def _process_utterance(self, audio: np.ndarray) -> dict:
        self._emit("transcribing", {"samples": int(audio.size)})
        if self.speech_fraction(audio) < self.min_speech_fraction:
            return self._empty_result()
        tx = self.transcriber.transcribe(audio)
        text = (tx.get("text") or "").strip()
        self.last_transcription = text
        self._emit("transcribed", {"text": text})
        if not text:
            return self._empty_result(text)
        parsed = self.parser.classify(text)
        if parsed.get("kind") == "command":
            exec_res = self.executor.execute(parsed)
            self.last_result = {"kind": "command", "parsed": parsed, "exec": exec_res,
                                "context": self.context.current()}
            self._emit("command", self.last_result)
        else:
            clean = (parsed.get("text") or text).strip()
            inj_cfg = self.config.get("injection", {})
            if inj_cfg.get("auto_punctuation"):
                from voicetype.ollama_parser import apply_auto_punctuation
                clean = apply_auto_punctuation(clean)
            # Universal integration layer: strategy per focused app/field,
            # primary -> clipboard -> warn fallback chain inside the router.
            result = self.router.inject_text(clean + " ", ctx=None)
            self.last_result = {"kind": "dictation", "text": clean,
                                "context": self.context.current(),
                                "strategy": {"mode": result.strategy.mode,
                                             "category": result.strategy.category,
                                             "field_role": result.strategy.field_role,
                                             "fallback_used": result.strategy.fallback_used,
                                             "redacted": result.redacted},
                                "warning": result.warning}
            self._emit("dictation", self.last_result)
        return self.last_result

    # Synchronous helper used by tests / push-to-talk.
    def handle_audio(self, audio: np.ndarray) -> dict:
        # Paused means paused: explicit push-to-talk must not bypass the
        # listening switch, or a stuck hotkey keeps injecting forever.
        if not self.listening:
            return {"kind": "empty", "text": ""}
        return self._process_utterance(np.asarray(audio, dtype=np.float32).reshape(-1))
