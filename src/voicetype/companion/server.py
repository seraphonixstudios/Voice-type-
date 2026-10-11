"""TLS WebSocket server: phone streams mic audio, sends commands.

Protocol (first frame must authenticate, else the connection is closed):

  phone -> server (text JSON)
    {"type": "pair", "token": "<single-use setup token>"}
      <- {"type": "paired", "device_id": "..", "device_token": ".."}
    {"type": "auth", "device_id": "..", "secret": ".."}
      <- {"type": "authed"}
    {"type": "ptt_start", "id": "<utterance id>"}
    binary frames: PCM16 little-endian 16 kHz mono microphone audio
    {"type": "ptt_stop"}
      <- {"type": "transcript", "id": "..", "text": "..", "kind": "dictation|command|empty"}
    {"type": "command", "action": "..", "target": "..", "value": ".."}
      <- {"type": "command_result", "ok": true, ...}
    {"type": "set_listening", "on": true|false}
      <- {"type": "listening", "on": ...}
    {"type": "ping"} <- {"type": "pong"}

  The handler is duck-typed (`recv`/`send`/`close`) so it works with any
  `websockets` version and is unit-testable without a network. `serve()`
  needs the optional `websockets` package; without it you get a clear error.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("voicetype.companion")

SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * 60  # 60 s of PCM16 safety cap


@dataclass
class _Session:
    authed: bool = False
    device_id: str = ""
    recording: bool = False
    utterance_id: str = ""
    chunks: list = field(default_factory=list)
    size_bytes: int = 0


def pcm16_to_float32(raw: bytes) -> np.ndarray:
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return (pcm / 32768.0).astype(np.float32)


class CompanionServer:
    """Bridges one phone to a VoicePipeline over an authenticated socket."""

    def __init__(self, pipeline, store, host: str = "0.0.0.0", port: int = 8765,
                 ssl_context: "ssl.SSLContext | None" = None):
        self.pipeline = pipeline
        self.store = store
        self.host = host
        self.port = int(port)
        self.ssl_context = ssl_context
        self._sessions = 0

    # ---- serving (needs `websockets`; handler itself does not) ----
    async def serve(self) -> None:
        try:
            import websockets  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "the `websockets` package is required for companion mode "
                f"(pip install websockets): {exc}") from exc
        serve = getattr(websockets, "serve", None)
        if serve is None:  # websockets >= 13 asyncio API
            from websockets.asyncio.server import serve  # type: ignore
        async with serve(self.handle, self.host, self.port, ssl=self.ssl_context):
            log.info("companion server on %s:%s (tls=%s)",
                     self.host, self.port, bool(self.ssl_context))
            await asyncio.Future()  # run forever

    def serve_in_background(self) -> threading.Thread:
        t = threading.Thread(target=lambda: asyncio.run(self.serve()),
                             name="vt-companion", daemon=True)
        t.start()
        return t

    # ---- per-connection handler (duck-typed websocket) ----
    async def handle(self, ws, path=None) -> None:
        _ = path
        session = _Session()
        self._sessions += 1
        try:
            async for message in ws:
                if isinstance(message, (bytes, bytearray)):
                    self._on_binary(session, bytes(message))
                    continue
                reply = self._on_text(session, str(message))
                if reply is not None:
                    await ws.send(json.dumps(reply))
                if session.authed is False and getattr(ws, "closed", False):
                    break
        except Exception as exc:
            log.warning("companion connection error: %s", exc)
        finally:
            self._sessions -= 1
            try:
                await ws.close()
            except Exception:
                pass

    # ---- message handling (sync: easy to unit test) ----
    def _on_text(self, session: _Session, raw: str) -> dict | None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return {"type": "error", "message": "malformed JSON"}
        if not isinstance(msg, dict) or not msg.get("type"):
            return {"type": "error", "message": "message needs a 'type' field"}
        kind = msg["type"]
        if kind == "pair":
            return self._do_pair(session, msg)
        if kind == "auth":
            return self._do_auth(session, msg)
        if not session.authed:
            return {"type": "error", "message": "not paired: send pair/auth first"}
        if kind == "ptt_start":
            session.recording = True
            session.utterance_id = str(msg.get("id", ""))
            session.chunks = []
            session.size_bytes = 0
            return {"type": "recording", "id": session.utterance_id}
        if kind == "ptt_stop":
            return self._do_transcribe(session)
        if kind == "command":
            return self._do_command(msg)
        if kind == "set_listening":
            on = bool(msg.get("on", True))
            try:
                self.pipeline.set_listening(on)
            except Exception as exc:
                return {"type": "error", "message": f"set_listening failed: {exc}"}
            return {"type": "listening", "on": on}
        if kind == "ping":
            return {"type": "pong"}
        return {"type": "error", "message": f"unknown message type: {kind}"}

    def _do_pair(self, session: _Session, msg: dict) -> dict:
        if not self.store.consume_pairing_token(str(msg.get("token", ""))):
            return {"type": "error", "message": "invalid or expired pairing token"}
        device_id, secret = self.store.issue_device_credential()
        session.authed = True
        session.device_id = device_id
        return {"type": "paired", "device_id": device_id, "device_token": secret}

    def _do_auth(self, session: _Session, msg: dict) -> dict:
        if self.store.verify_device(str(msg.get("device_id", "")),
                                    str(msg.get("secret", ""))):
            session.authed = True
            session.device_id = str(msg.get("device_id", ""))
            return {"type": "authed"}
        return {"type": "error", "message": "unknown device credential"}

    def _on_binary(self, session: _Session, data: bytes) -> None:
        if not session.authed or not session.recording or not data:
            return
        room = MAX_AUDIO_BYTES - session.size_bytes
        if room <= 0:
            session.recording = False  # safety cap: stop, keep what we have
            return
        session.chunks.append(data[:room])
        session.size_bytes += len(data[:room])

    def _do_transcribe(self, session: _Session) -> dict:
        session.recording = False
        raw = b"".join(session.chunks)
        session.chunks = []
        session.size_bytes = 0
        if not raw:
            return {"type": "transcript", "id": session.utterance_id,
                    "text": "", "kind": "empty"}
        try:
            audio = pcm16_to_float32(raw)
            result = self.pipeline._process_utterance(audio)
            return {"type": "transcript", "id": session.utterance_id,
                    "text": result.get("text", "") or str(
                        (result.get("parsed") or {}).get("action", "")),
                    "kind": result.get("kind", "dictation")}
        except Exception as exc:
            log.warning("companion transcription failed: %s", exc)
            return {"type": "transcript", "id": session.utterance_id,
                    "text": "", "kind": "error", "error": str(exc)}

    def _do_command(self, msg: dict) -> dict:
        try:
            res = self.pipeline.executor.execute({
                "action": msg.get("action", ""), "target": msg.get("target", ""),
                "value": msg.get("value", "")})
            return {"type": "command_result", "ok": bool(res.get("ok")),
                    "action": res.get("action", ""), "error": res.get("error", "")}
        except Exception as exc:
            return {"type": "command_result", "ok": False, "error": str(exc)}


def ssl_context_for(store) -> ssl.SSLContext:
    cert, key = store.cert_paths()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    return ctx


def start_companion_services(config: dict, pipeline, store=None) -> dict:
    """Build (not yet serving) companion pieces from config. Never raises."""
    from voicetype.companion.discovery import advertise
    from voicetype.companion.pairing import PairingStore, lan_ip
    cfg = (config.get("companion", {}) or {})
    host = str(cfg.get("host", "0.0.0.0"))
    port = int(cfg.get("port", 8765))
    store = store or PairingStore(token_ttl_seconds=float(cfg.get("token_ttl_seconds", 600)))
    server = CompanionServer(pipeline, store, host, port)
    ssl_ctx = None
    fingerprint = ""
    tls_error = ""
    if bool(cfg.get("tls", True)):
        try:
            ssl_ctx = ssl_context_for(store)
            server.ssl_context = ssl_ctx
            fingerprint = store.certificate_fingerprint()
        except Exception as exc:  # cryptography missing -> plain ws, loud reason
            tls_error = f"TLS unavailable ({exc}); falling back to unencrypted ws"
            log.warning(tls_error)
    advertiser = advertise(port, fingerprint, tls=bool(ssl_ctx))
    payload = store.pairing_payload(lan_ip(), port) if fingerprint else None
    try:
        if payload is not None:
            qr = store.qr_png(payload)
        else:
            qr = b""
    except Exception:
        qr = b""
    return {"server": server, "store": store, "advertiser": advertiser,
            "ssl_context": ssl_ctx, "fingerprint": fingerprint,
            "tls_error": tls_error, "payload": payload, "qr_png": qr}
