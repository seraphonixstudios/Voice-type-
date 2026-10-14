"""End-to-end pipeline: wake -> record utterance -> transcribe -> parse -> act."""
from __future__ import annotations

import re
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

    # Targets that must never reach a shell launcher: URLs (would open a
    # browser to an arbitrary page), paths, and executables. Hallucinated
    # transcripts have produced all three.
    _UNSAFE_APP_TARGET = re.compile(
        r"(?i)^\s*(https?://|ftp://|file://|www\.|\\\\|[a-z]:[\\/]|"
        r".*\.(exe|bat|cmd|ps1|msi|lnk)\s*$)")

    # Everyday names -> real launch targets, per OS where they differ.
    _APP_ALIASES = {
        "microsoft edge": {"Darwin": "Microsoft Edge", "Windows": "msedge", "Linux": "microsoft-edge"},
        "edge": {"Darwin": "Microsoft Edge", "Windows": "msedge", "Linux": "microsoft-edge"},
        "chrome": {"Darwin": "Google Chrome", "Windows": "chrome", "Linux": "google-chrome"},
        "google chrome": {"Darwin": "Google Chrome", "Windows": "chrome", "Linux": "google-chrome"},
        "firefox": {"Darwin": "Firefox", "Windows": "firefox", "Linux": "firefox"},
        "vscode": {"Darwin": "Visual Studio Code", "Windows": "code", "Linux": "code"},
        "visual studio code": {"Darwin": "Visual Studio Code", "Windows": "code", "Linux": "code"},
        "notepad": {"Darwin": "TextEdit", "Windows": "notepad", "Linux": "gedit"},
        "calculator": {"Darwin": "Calculator", "Windows": "calc", "Linux": "gnome-calculator"},
        "terminal": {"Darwin": "Terminal", "Windows": "wt", "Linux": "gnome-terminal"},
        "safari": {"Darwin": "Safari", "Windows": "msedge", "Linux": "firefox"},
        "word": {"Darwin": "Microsoft Word", "Windows": "winword", "Linux": "libreoffice --writer"},
    }

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
                if not self._switch_app(target or value):
                    result["ok"] = False
                    result["error"] = (
                        f"refused unsafe app target {target or value!r}")
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

    @classmethod
    def resolve_app_name(cls, name: str) -> str:
        import platform
        key = (name or "").strip().lower()
        return cls._APP_ALIASES.get(key, {}).get(platform.system(), (name or "").strip())

    @classmethod
    def is_unsafe_target(cls, name: str) -> bool:
        return bool(cls._UNSAFE_APP_TARGET.match(name or ""))

    def _switch_app(self, name: str) -> bool:
        """Launch/focus an app. Returns False when the target is refused."""
        import platform
        if self.is_unsafe_target(name):
            return False
        s = platform.system()
        target = self.resolve_app_name(name)
        if not target:
            return False
        try:
            if s == "Darwin":
                subprocess.run(["open", "-a", target], check=False, timeout=5)
            elif s == "Windows":
                subprocess.run(["cmd", "/c", "start", "", target], check=False, timeout=5)
            else:
                subprocess.run(["xdg-open", target], check=False, timeout=5)
        except Exception:
            pass
        return True


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
        pvcfg = config.get("preview", {})
        self.preview_enabled = bool(pvcfg.get("enabled", True))
        self.preview_interval = float(pvcfg.get("interval_seconds", 2.5))
        self.preview_min_seconds = float(pvcfg.get("min_seconds", 1.5))
        self._last_preview_at = 0.0
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
            self._maybe_preview(chunks, collected)
            if collected > sr * 0.8 and silent >= silence_needed:
                break
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def _maybe_preview(self, chunks: list, collected: int) -> None:
        """Emit a display-only partial transcript while recording.

        Never injected, never logged as final: just the "it's hearing me"
        feedback. Reuses the main transcriber (no extra VRAM); failures and
        empty partials are silently skipped.
        """
        if not self.preview_enabled:
            return
        now = time.time()
        if now - self._last_preview_at < self.preview_interval:
            return
        if collected < int(self.ring.sample_rate * self.preview_min_seconds):
            return
        self._last_preview_at = now
        try:
            audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
            tx = self.transcriber.transcribe(audio)
            text = (tx.get("text") or "").strip()
            if text:
                self._emit("preview", {"text": text})
        except Exception:
            pass

    @staticmethod
    def spectral_diagnosis(audio: np.ndarray, sample_rate: int = 16000) -> str:
        """One-line spectral fingerprint: hum vs hiss vs voice vs clipping."""
        mono = np.asarray(audio, dtype=np.float32).reshape(-1)
        if mono.size < 1600:
            return "too short to analyze"
        peak = float(np.max(np.abs(mono)))
        if peak < 1e-4:
            return "digital silence"
        try:
            spectrum = np.abs(np.fft.rfft(mono - np.mean(mono)))
            freqs = np.fft.rfftfreq(mono.size, 1.0 / sample_rate)
            total = float(np.sum(spectrum) + 1e-12)

            def band(lo: float, hi: float) -> float:
                sel = (freqs >= lo) & (freqs < hi)
                return float(np.sum(spectrum[sel])) / total

            hum, voice = band(45, 65), band(300, 3400)
            hiss = band(6000, 8000)
            clip = float(np.mean(np.abs(mono) > 0.98))
            notes = []
            if hum > 0.35:
                notes.append(f"mains hum {hum:.0%}")
            if hiss > 0.25:
                notes.append(f"hiss/noise {hiss:.0%}")
            if clip > 0.02:
                notes.append(f"clipping {clip:.0%}")
            notes.append(f"voice-band {voice:.0%}")
            return "peak {:.2f}, {}".format(peak, ", ".join(notes))
        except Exception:
            return "analysis failed"

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

    def _empty_result(self, text: str = "", detail: str = "") -> dict:
        self.last_transcription = text
        self._emit("transcribed", {"text": text, "detail": detail})
        self.last_result = {"kind": "empty", "text": "", "detail": detail}
        return self.last_result

    def _process_utterance(self, audio: np.ndarray) -> dict:
        self._emit("transcribing", {"samples": int(audio.size)})
        if self.speech_fraction(audio) < self.min_speech_fraction:
            return self._empty_result()
        try:
            tx = self.transcriber.transcribe(audio)
        except Exception as exc:
            # Never die silently: surface engine failures in UI + logs so a
            # broken model install looks broken instead of "nothing happens".
            message = f"Transcription failed: {exc}"
            self.last_transcription = ""
            self.last_result = {"kind": "error", "text": "", "error": message}
            self._emit("error", {"text": message})
            return self.last_result
        text = (tx.get("text") or "").strip()
        self.last_transcription = text
        self._emit("transcribed", {"text": text})
        if not text:
            dur = audio.size / float(self.ring.sample_rate)
            frac = self.speech_fraction(audio)
            info = getattr(self.transcriber, "last_info", {}) or {}
            detail = (f"no speech recognized from {dur:.1f}s audio "
                      f"({frac:.0%} speech, "
                      f"{info.get('n_raw_segments', '?')} raw / "
                      f"{info.get('n_segments', '?')} kept segments; "
                      f"{self.spectral_diagnosis(audio, self.ring.sample_rate)})")
            return self._empty_result(text, detail)
        parsed = self.parser.classify(text)
        if parsed.get("kind") == "command":
            if self._is_risky_low_confidence_command(parsed):
                # Uncertain app-launch: type the words instead of launching.
                parsed = {"kind": "dictation", "text": text,
                          "demoted": True, "raw": text}
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
            delivery = getattr(self.injector, "last_delivery", None)
            warning = result.warning
            if delivery is not None and delivery[1] > 0 and delivery[0] == 0:
                warning = ((warning + " ") if warning else "") + (
                    "OS accepted 0 keystrokes — the target window likely has "
                    "no text caret (click inside the text box), or it blocks "
                    "synthetic input.")
            self.last_result = {"kind": "dictation", "text": clean,
                                "context": self.context.current(),
                                "strategy": {"mode": result.strategy.mode,
                                             "category": result.strategy.category,
                                             "field_role": result.strategy.field_role,
                                             "fallback_used": result.strategy.fallback_used,
                                             "redacted": result.redacted},
                                "delivery": delivery,
                                "warning": warning}
            self._emit("dictation", self.last_result)
        return self.last_result

    def _is_risky_low_confidence_command(self, parsed: dict) -> bool:
        """True for app-launch commands we should not trust.

        Launching applications from a misheard transcript is the one
        command class with side effects outside the text field, so it gets
        two extra gates: an explicit confidence floor, and refusal of
        URL/path/executable-looking targets (which would otherwise open a
        browser to an arbitrary page or run a file).
        """
        action = (parsed.get("action") or "").lower()
        if action not in ("switch_app", "open_app"):
            return False
        target = parsed.get("target") or parsed.get("value") or ""
        if CommandExecutor.is_unsafe_target(target):
            return True
        try:
            conf = float((getattr(self.transcriber, "last_info", {}) or {}).get(
                "confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        floor = float((self.config.get("commands", {}) or {}).get(
            "min_confidence", -0.5))
        return conf < floor

    # Synchronous helper used by tests / push-to-talk.
    def handle_audio(self, audio: np.ndarray) -> dict:        # Paused means paused: explicit push-to-talk must not bypass the
        # listening switch, or a stuck hotkey keeps injecting forever.
        if not self.listening:
            return {"kind": "empty", "text": ""}
        return self._process_utterance(np.asarray(audio, dtype=np.float32).reshape(-1))

    def dictate_once(self) -> dict:
        """Manual trigger: record one utterance now, no wake word needed.

        Deterministic fallback for environments where the energy detector
        can't hear the phrase reliably. Honors pause/busy like _on_wake.
        """
        if not self.listening:
            return {"kind": "empty", "text": ""}
        with self._lock:
            if self._recording:
                return {"kind": "busy", "text": ""}
            self._recording = True
        try:
            self._emit("wake", {"score": 1.0, "backend": "manual", "manual": True})
            result = self._process_utterance(self.record_utterance())
            import logging
            logging.getLogger("voicetype.pipeline").info(
                "manual dictation finished: kind=%s text=%r",
                result.get("kind"), (result.get("text") or "")[:80])
            return result
        finally:
            with self._lock:
                self._recording = False
