"""Integration tests: synthetic audio in, text/commands injected out."""
from __future__ import annotations

import time

import numpy as np
import pytest

from voicetype.config_manager import load_config
from voicetype.injector import FallbackInjector
from voicetype.pipeline import VoicePipeline

SR = 16000


class ScriptTranscriber:
    """Fake STT returning a scripted transcript for any loud audio."""

    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe(self, audio, sample_rate=16000):
        self.calls += 1
        return {"text": self.text, "segments": [self.text],
                "language": "en", "backend": "fake"}

    def set_vocabulary(self, words):
        pass


class ScriptParser:
    def __init__(self, result):
        self.result = result

    def classify(self, transcript):
        return dict(self.result)


class QuietContext:
    def __init__(self):
        self.refreshed = 0

    def current(self):
        return {"app": "Editor", "title": "doc", "cursor": {"x": 5, "y": 5}, "stale": False}

    def refresh(self):
        self.refreshed += 1
        return self.current()

    def start(self):
        pass

    def stop(self):
        pass


def _pipe(transcript, parsed, **overrides):
    cfg = load_config(overrides={"injection": {"backend": "fallback"},
                                 "utterance": {"max_seconds": 2.0, "end_silence_ms": 300},
                                 **overrides})
    inj = FallbackInjector()
    ctx = QuietContext()
    pipe = VoicePipeline(cfg, injector=inj, transcriber=ScriptTranscriber(transcript),
                         parser=ScriptParser(parsed), context=ctx)
    return pipe, inj, ctx


def test_full_dictation_path_audio_in_text_out(speech_audio):
    pipe, inj, ctx = _pipe("take out the trash",
                           {"kind": "dictation", "text": "Take out the trash"})
    events: list = []
    pipe.add_event_listener(lambda e, p: events.append(e))
    out = pipe.handle_audio(speech_audio)
    assert out["kind"] == "dictation"
    assert inj.typed_log == ["Take out the trash "]
    assert pipe.last_transcription == "take out the trash"
    assert ctx.refreshed >= 1
    assert {"transcribing", "transcribed", "dictation"} <= set(events)


def test_full_command_path_executes(speech_audio):
    pipe, inj, ctx = _pipe("scroll down",
                           {"kind": "command", "action": "scroll",
                            "target": "down", "value": "3"})
    out = pipe.handle_audio(speech_audio)
    assert out["kind"] == "command"
    assert out["exec"]["ok"] is True
    assert ("scroll", "down", 3) in inj.events_log
    assert inj.typed_log == []  # commands never type


def test_auto_punctuation_end_to_end(speech_audio):
    pipe, inj, _ = _pipe("buy milk",
                         {"kind": "dictation", "text": "buy milk"},
                         injection={"backend": "fallback", "auto_punctuation": True})
    out = pipe.handle_audio(speech_audio)
    assert out["text"] == "Buy milk."
    assert inj.typed_log == ["Buy milk. "]


def test_empty_transcript_injects_nothing(silence_audio):
    pipe, inj, _ = _pipe("", {"kind": "dictation", "text": ""})
    assert pipe.handle_audio(silence_audio)["kind"] == "empty"
    assert inj.typed_log == []


def test_list_mics_prints_devices(tmp_path, capsys, monkeypatch):
    import sys
    import types
    from voicetype.app import main
    fake = types.SimpleNamespace(query_devices=lambda *a, **k: [
        {"name": "USB Mic", "max_input_channels": 1, "default_samplerate": 44100.0},
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
    ])
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    assert main(["--list-mics"]) == 0
    out = capsys.readouterr().out
    assert "USB Mic" in out and "Speakers" not in out


def test_list_input_devices_survives_missing_backend(monkeypatch):
    import sys
    from voicetype.capture import AudioCapture
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    assert AudioCapture.list_input_devices() == []
    from voicetype.audio_ring import AudioRingBuffer
    cap = AudioCapture(AudioRingBuffer(seconds=1.0), device="USB Mic")
    assert cap.device == "USB Mic"
    assert cap.resolve_device_name() == "(system default input)"


def test_pipeline_uses_configured_device():
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    cfg = load_config(overrides={"injection": {"backend": "fallback"},
                                 "audio": {"device": "USB Mic"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=FakeTranscriber("x"),
                         parser=FakeParser({"kind": "dictation", "text": "x"}),
                         context=FakeContext())
    assert pipe.capture.device == "USB Mic"


def test_mostly_silent_utterance_skipped_before_transcription():
    pipe, inj, _ = _pipe("should never appear",
                         {"kind": "dictation", "text": "should never appear"})
    mostly_quiet = np.zeros(SR, dtype=np.float32)
    mostly_quiet[:800] = 0.3  # 50 ms blip in 1 s of room noise
    assert pipe.speech_fraction(mostly_quiet) < pipe.min_speech_fraction
    out = pipe.handle_audio(mostly_quiet)
    assert out["kind"] == "empty"
    assert inj.typed_log == []
    assert pipe.transcriber.calls == 0  # model never woken


def test_speech_fraction_unit():
    pipe, _, _ = _pipe("x", {"kind": "dictation", "text": "x"})
    assert pipe.speech_fraction(np.zeros(0, dtype=np.float32)) == 0.0
    assert pipe.speech_fraction(np.zeros(SR, dtype=np.float32)) == 0.0
    assert pipe.speech_fraction(np.ones(SR, dtype=np.float32) * 0.5) == 1.0


def test_pipeline_warns_on_zero_delivery():
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    import numpy as np
    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    inj = FallbackInjector()
    inj.last_delivery = (0, 10)  # OS accepted nothing
    pipe = VoicePipeline(cfg, injector=inj,
                         transcriber=FakeTranscriber("hello there"),
                         parser=FakeParser({"kind": "dictation", "text": "hello there"}),
                         context=FakeContext())
    loud = (np.random.RandomState(12).randn(16000).astype(np.float32) * 0.3)
    out = pipe.handle_audio(loud)
    assert out["kind"] == "dictation"
    assert "0 keystrokes" in out["warning"]


def test_empty_dictation_carries_diagnostic_detail():
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser, FakeTranscriber
    import numpy as np
    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=FakeTranscriber(""),
                         parser=FakeParser({"kind": "dictation", "text": ""}),
                         context=FakeContext())
    loud = (np.random.RandomState(11).randn(16000).astype(np.float32) * 0.3)
    out = pipe.handle_audio(loud)
    assert out["kind"] == "empty" and out["text"] == ""
    assert "speech" in out["detail"] and "segments" in out["detail"]


def test_spectral_diagnosis_labels_audio():
    import numpy as np
    from voicetype.pipeline import VoicePipeline
    assert VoicePipeline.spectral_diagnosis(
        np.zeros(100, dtype=np.float32)) == "too short to analyze"
    assert "digital silence" in VoicePipeline.spectral_diagnosis(
        np.zeros(16000, dtype=np.float32))
    t = np.arange(16000) / 16000.0
    hum = (0.5 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
    assert "mains hum" in VoicePipeline.spectral_diagnosis(hum)
    voice = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    diag = VoicePipeline.spectral_diagnosis(voice)
    assert "voice-band" in diag and "mains hum" not in diag


def test_dictate_once_paths(speech_audio):
    pipe, inj, _ = _pipe("manual words", {"kind": "dictation", "text": "manual words"})
    pipe.set_listening(False)
    out = pipe.dictate_once()
    assert out["kind"] == "empty" and out["text"] == ""
    assert inj.typed_log == []
    pipe.set_listening(True)
    pipe._recording = True
    assert pipe.dictate_once() == {"kind": "busy", "text": ""}
    pipe._recording = False
    pipe.ring.write((speech_audio[:16000] * 2).astype(np.float32))
    out = pipe.dictate_once()
    assert out["kind"] == "dictation"
    assert inj.typed_log == ["manual words "]


def test_wake_triggers_record_and_process(wake_audio):
    pipe, inj, _ = _pipe("hello world", {"kind": "dictation", "text": "hello world"})
    # Pre-fill ring with speech-like audio so the utterance passes the gate.
    pipe.ring.write((wake_audio * 2).astype(np.float32))
    pipe.wake.listening = True
    pipe._on_wake({"score": 0.9, "backend": "fallback"})
    assert inj.typed_log == ["hello world "]


def test_wake_ignored_when_paused_and_when_busy(wake_audio):
    pipe, inj, _ = _pipe("hi", {"kind": "dictation", "text": "hi"})
    pipe.set_listening(False)
    pipe.wake.listening = False
    pipe._on_wake({"score": 0.9})
    assert inj.typed_log == []
    pipe.set_listening(True)
    pipe._recording = True  # re-entrant wake while busy
    pipe._on_wake({"score": 0.9})
    pipe._recording = False
    assert inj.typed_log == []


def test_fallback_score_tuning():
    from voicetype.wake import WakeWordDetector as W
    import numpy as np
    assert W._fallback_score(np.zeros(1280, dtype=np.float32)) == 0.0
    assert W._fallback_score(np.ones(1280, dtype=np.float32) * 0.5) == 0.9
    mid = W._fallback_score(np.ones(1280, dtype=np.float32) * 0.08)
    assert 0.0 < mid < 0.9  # conversational level: counts toward a streak


def test_capture_native_rate_resample(monkeypatch):
    import sys
    import types
    import numpy as np
    from voicetype.audio_ring import AudioRingBuffer
    from voicetype.capture import AudioCapture
    calls: list = []

    class FakeStream:
        def __init__(self, **kw):
            calls.append(kw)
            if kw.get("samplerate") == 16000:
                raise RuntimeError("Invalid sample rate [PaErrorCode -9997]")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    fake = types.SimpleNamespace(
        InputStream=lambda **kw: FakeStream(**kw),
        query_devices=lambda *a, **k: {"name": "Samson", "default_samplerate": 44100.0},
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    ring = AudioRingBuffer(seconds=2.0)
    cap = AudioCapture(ring, device="Samson")
    assert cap.start() == "sounddevice"
    assert cap._capture_rate == 44100
    assert calls[-1]["samplerate"] == 44100
    cap._callback(np.ones((3528, 1), dtype=np.float32) * 0.2, 3528, None, None)
    assert abs(len(ring) - 1280) <= 2  # 3528 @44.1k -> ~1280 @16k
    assert cap.device_name == "Samson"
    cap.stop()


def test_capture_native_rate_same_retries_synthetic(monkeypatch):
    import sys
    import types
    from voicetype.audio_ring import AudioRingBuffer
    from voicetype.capture import AudioCapture

    class FakeStream:
        def __init__(self, **kw):
            raise RuntimeError("nope")

    fake = types.SimpleNamespace(
        InputStream=lambda **kw: FakeStream(**kw),
        query_devices=lambda *a, **k: {"name": "X", "default_samplerate": 16000.0},
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    cap = AudioCapture(AudioRingBuffer(seconds=1.0))
    assert cap.start() == "synthetic"  # native == target: no resample path
    cap.stop()


def test_lifecycle_start_stop_with_synthetic_capture(monkeypatch):
    import sys
    from voicetype.audio_ring import AudioRingBuffer
    from voicetype.capture import AudioCapture
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # force fallback path
    ring = AudioRingBuffer(seconds=2.0)
    cap = AudioCapture(ring, synthetic_fallback=True)
    assert cap.start() == "synthetic"
    assert cap.running is True
    time.sleep(0.15)
    assert cap.stats.callbacks >= 1
    cap.feed(np.ones(160, dtype=np.float32))
    cap.stop()
    assert cap.running is False


def test_capture_sounddevice_path_and_drop_count(monkeypatch):
    import queue as _q
    from voicetype.audio_ring import AudioRingBuffer
    from voicetype.capture import AudioCapture
    import sys, types
    ring = AudioRingBuffer(seconds=2.0)
    cap = AudioCapture(ring)

    class FakeStream:
        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    fake_sd = types.SimpleNamespace(InputStream=lambda **k: FakeStream())
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    assert cap.start() == "sounddevice"
    cap._callback(np.ones((160, 1), dtype=np.float32), 160, None, None)
    assert cap.stats.callbacks == 1
    assert cap.read_chunk(timeout=0.1) is not None
    assert cap.read_chunk(timeout=0.01) is None  # queue drained
    cap._q = _q.Queue(maxsize=1)
    cap._q.put_nowait(np.ones(3, dtype=np.float32))
    cap._callback(np.ones(160, dtype=np.float32), 160, None, None)  # queue full -> drop
    assert cap.stats.dropped_chunks == 1
    heard: list = []
    cap.add_listener(heard.append)
    cap.add_listener(lambda c: (_ for _ in ()).throw(RuntimeError()))
    cap.feed(np.ones(10, dtype=np.float32))
    assert len(heard) == 1
    cap.remove_listener(heard.append)  # different closure -> still present
    cap.stop()


def test_capture_no_fallback_raises(monkeypatch):
    import sys
    from voicetype.audio_ring import AudioRingBuffer
    from voicetype.capture import AudioCapture
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    cap = AudioCapture(AudioRingBuffer(seconds=1.0), synthetic_fallback=False)
    with pytest.raises(Exception):
        cap.start()
    cap.stop()


def test_executor_all_actions_and_unknown():
    from voicetype.pipeline import CommandExecutor
    inj = FallbackInjector()
    ex = CommandExecutor(inj, QuietContext())
    for cmd in [{"action": "delete", "target": "word", "value": "2"},
                {"action": "new_paragraph", "target": "", "value": ""},
                {"action": "scroll", "target": "up", "value": "bad"},
                {"action": "undo", "target": "", "value": ""},
                {"action": "redo", "target": "", "value": ""},
                {"action": "copy", "target": "", "value": ""},
                {"action": "paste", "target": "", "value": ""},
                {"action": "select_all", "target": "", "value": ""},
                {"action": "press_key", "target": "ctrl+s", "value": ""},
                {"action": "type_text", "target": "", "value": "yo"}]:
        assert ex.execute(cmd)["ok"] is True
    bad = ex.execute({"action": "teleport", "target": "", "value": ""})
    assert bad["ok"] is False and "unknown action" in bad["error"]
    assert len(ex.history) == 11


def test_executor_mac_modifiers(monkeypatch):
    import platform
    from voicetype.pipeline import CommandExecutor
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    inj = FallbackInjector()
    ex = CommandExecutor(inj, QuietContext())
    assert ex.execute({"action": "copy", "target": "", "value": ""})["ok"]
    assert ("combo", ("command", "c")) in inj.events_log


def test_switch_app_per_platform(monkeypatch):
    import platform
    import subprocess
    from voicetype.pipeline import CommandExecutor
    calls: list = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or types.SimpleNamespace(returncode=0))
    import types
    inj = FallbackInjector()
    ex = CommandExecutor(inj, QuietContext())
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    ex.execute({"action": "switch_app", "target": "Safari", "value": ""})
    assert calls[-1][:2] == ["open", "-a"]
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    ex.execute({"action": "open_app", "target": "notepad", "value": ""})
    assert calls[-1][0] == "cmd"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    ex.execute({"action": "switch_app", "target": "x", "value": ""})
    assert calls[-1][0] == "xdg-open"


def test_record_utterance_caps_at_max_seconds():
    pipe, _, _ = _pipe("x", {"kind": "dictation", "text": "x"})
    pipe.max_seconds = 0.3
    pipe.end_silence_ms = 10_000  # never trigger silence cutoff
    threading_feeder(pipe)
    audio = pipe.record_utterance()
    assert audio.size <= int(SR * 0.3) + SR  # pre-roll + cap


def threading_feeder(pipe):
    import threading
    stop = threading.Event()

    def feed():
        while not stop.is_set():
            pipe.ring.write(np.ones(160, dtype=np.float32) * 0.2)
            time.sleep(0.01)

    t = threading.Thread(target=feed, daemon=True)
    t.start()
    time.sleep(0.45)
    stop.set()
    t.join(timeout=1.0)


def test_low_confidence_app_launch_demoted_to_dictation():
    import numpy as np
    from voicetype.config_manager import load_config
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import VoicePipeline
    from tests.test_pipeline import FakeContext, FakeParser
    from tests.test_transcription import ScoredModel, ScoredSeg
    from voicetype.transcriber import Transcriber
    transcribed = ScoredModel([ScoredSeg("switch to edge", avg_logprob=-0.7)])
    # -0.7 survives the segment filter (floor -1.0) but trips the
    # app-launch confidence gate (floor -0.5) -> typed, not launched.
    cfg = load_config(overrides={"injection": {"backend": "fallback"}})
    pipe = VoicePipeline(cfg, injector=FallbackInjector(),
                         transcriber=Transcriber(model_override=transcribed),
                         parser=FakeParser({"kind": "command", "action": "switch_app",
                                            "target": "edge", "value": ""}),
                         context=FakeContext())
    loud = (np.random.RandomState(13).randn(16000).astype(np.float32) * 0.3)
    out = pipe.handle_audio(loud)
    assert out["kind"] == "dictation"  # launched nothing, typed the words
    assert pipe.injector.typed_log == ["switch to edge "]


def test_unsafe_app_targets_refused():
    from voicetype.injector import FallbackInjector
    from voicetype.pipeline import CommandExecutor
    from tests.test_pipeline import FakeContext
    ex = CommandExecutor(FallbackInjector(), FakeContext())
    for bad in ["https://evil.example/x", "www.evil.example",
                "C:\\Windows\\System32\\cmd.exe", "\\\\srv\\share",
                "payload.exe", "run.bat"]:
        res = ex.execute({"action": "switch_app", "target": bad, "value": ""})
        assert res["ok"] is False and "refused" in res["error"]
    assert ex.history and all(h["action"] == "switch_app" for h in ex.history)


def test_app_alias_resolution(monkeypatch):
    import platform
    from voicetype.pipeline import CommandExecutor
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert CommandExecutor.resolve_app_name("Microsoft Edge") == "msedge"
    assert CommandExecutor.resolve_app_name("  OBSIDIAN  ") == "OBSIDIAN"
    assert CommandExecutor.resolve_app_name("") == ""
    assert CommandExecutor.is_unsafe_target("https://x.example") is True
    assert CommandExecutor.is_unsafe_target("Firefox") is False
