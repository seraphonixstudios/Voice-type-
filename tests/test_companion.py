"""Companion (mobile-over-LAN) tests: pairing, TLS identity, server protocol."""
from __future__ import annotations

import asyncio
import json
import sys
import types

import numpy as np
import pytest

from voicetype.companion.discovery import Advertiser, advertise
from voicetype.companion.pairing import PairingStore, QRPayload, lan_ip
from voicetype.companion.server import (
    CompanionServer,
    pcm16_to_float32,
    ssl_context_for,
    start_companion_services,
)


@pytest.fixture
def store(tmp_path):
    return PairingStore(config_dir=tmp_path / "comp", token_ttl_seconds=600)


class FakePipeline:
    def __init__(self):
        self.listening = True
        self.last_audio = None

    def _process_utterance(self, audio):
        self.last_audio = np.asarray(audio)
        return {"kind": "dictation", "text": "hello phone"}

    def set_listening(self, on):
        self.listening = bool(on)

    @property
    def executor(self):
        class Ex:
            @staticmethod
            def execute(cmd):
                return {"ok": True, "action": cmd.get("action", ""),
                        "target": "", "value": "", "error": ""}
        return Ex()


class FakeWS:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent: list = []
        self.closed = False

    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m
        return gen()

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def close(self):
        self.closed = True


def replies(ws):
    return {m["type"]: m for m in ws.sent}


# ---- tokens ----
def test_pairing_token_single_use_and_expiry(store):
    t = store.issue_pairing_token()
    assert store.consume_pairing_token(t) is True
    assert store.consume_pairing_token(t) is False  # single use
    assert store.consume_pairing_token("bogus") is False
    assert store.consume_pairing_token("") is False


def test_pairing_token_expiry(tmp_path):
    fast = PairingStore(config_dir=tmp_path / "c", token_ttl_seconds=-1)
    assert fast.consume_pairing_token(fast.issue_pairing_token()) is False
    assert fast.purge_expired() >= 0


def test_device_credential_roundtrip_and_revoke(store):
    did, secret = store.issue_device_credential()
    assert store.verify_device(did, secret) is True
    assert store.verify_device(did, "wrong") is False
    assert store.verify_device("nope", secret) is False
    assert store.revoke_device(did) is True
    assert store.verify_device(did, secret) is False
    assert store.revoke_device(did) is False


def test_devices_survive_reload(tmp_path):
    s1 = PairingStore(config_dir=tmp_path / "c")
    did, secret = s1.issue_device_credential()
    s2 = PairingStore(config_dir=tmp_path / "c")
    assert s2.verify_device(did, secret) is True


# ---- TLS identity ----
def test_cert_generated_once_and_fingerprint_stable(store):
    cert, key = store.cert_paths()
    assert cert.exists() and key.exists()
    fp1 = store.certificate_fingerprint()
    assert len(fp1) == 64 and all(c in "0123456789abcdef" for c in fp1)
    assert store.certificate_fingerprint() == fp1  # reused, not regenerated


def test_lan_ip_is_string():
    assert isinstance(lan_ip(), str) and lan_ip()


# ---- QR payload ----
def test_qr_uri_roundtrip(store):
    p = store.pairing_payload("192.168.1.5", 8765)
    uri = p.to_uri()
    assert uri.startswith("voicetype://pair?")
    back = QRPayload.from_uri(uri)
    assert (back.host, back.port, back.token, back.fingerprint) == \
        (p.host, p.port, p.token, p.fingerprint)
    with pytest.raises(ValueError):
        QRPayload.from_uri("https://example.com/x")
    with pytest.raises(ValueError):
        QRPayload.from_uri("voicetype://pair?host=x")


def test_qr_ascii_fallback_and_png_missing(store, monkeypatch):
    p = store.pairing_payload("h", 1)
    art = store.qr_ascii(p)
    assert "voicetype://pair?" in art
    monkeypatch.setitem(sys.modules, "qrcode", None)
    with pytest.raises(Exception):
        store.qr_png(p)


# ---- discovery ----
def test_discovery_without_zeroconf(monkeypatch):
    monkeypatch.setitem(sys.modules, "zeroconf", None)
    adv = advertise(8765, "ab" * 32)
    assert adv.ok is False and "QR" in adv.reason
    adv.close()  # safe no-op


def test_discovery_with_stub_zeroconf(monkeypatch):
    registered: list = []
    closed: list = []

    class FakeInfo:
        def __init__(self, *a, **k):
            self.args, self.kwargs = a, k

    class FakeZC:
        def register_service(self, info):
            registered.append(info)

        def unregister_service(self, info):
            closed.append(("unreg", info))

        def close(self):
            closed.append(("close",))

    mod = types.SimpleNamespace(ServiceInfo=FakeInfo, Zeroconf=FakeZC)
    monkeypatch.setitem(sys.modules, "zeroconf", mod)
    adv = advertise(8765, "ab" * 32)
    assert adv.ok is True and registered
    with adv:
        pass
    assert len(closed) == 2


def test_advertiser_close_never_raises():
    Advertiser(False, "x", _close=lambda: (_ for _ in ()).throw(RuntimeError())).close()


# ---- server protocol ----
@pytest.mark.asyncio
async def test_pair_then_ptt_flow(tmp_path):
    store = PairingStore(config_dir=tmp_path / "c")
    srv = CompanionServer(FakePipeline(), store)
    token = store.issue_pairing_token()
    pcm = (np.zeros(3200, dtype=np.float32) + 0.2).astype(np.float32)
    raw = (np.clip(pcm * 32768, -32768, 32767)).astype(np.int16).tobytes()
    ws = FakeWS([json.dumps({"type": "pair", "token": token}),
                 json.dumps({"type": "ptt_start", "id": "u1"}),
                 raw,
                 json.dumps({"type": "ptt_stop"})])
    await srv.handle(ws)
    r = replies(ws)
    assert r["paired"]["device_token"]
    assert r["recording"]["id"] == "u1"
    assert r["transcript"] == {"type": "transcript", "id": "u1",
                               "text": "hello phone", "kind": "dictation"}
    assert ws.closed


@pytest.mark.asyncio
async def test_auth_with_device_credential_and_commands(tmp_path):
    store = PairingStore(config_dir=tmp_path / "c")
    did, secret = store.issue_device_credential()
    srv = CompanionServer(FakePipeline(), store)
    ws = FakeWS([json.dumps({"type": "auth", "device_id": did, "secret": secret}),
                 json.dumps({"type": "command", "action": "scroll",
                             "target": "down", "value": "2"}),
                 json.dumps({"type": "set_listening", "on": False}),
                 json.dumps({"type": "ping"})])
    await srv.handle(ws)
    r = replies(ws)
    assert r["authed"] == {"type": "authed"}
    assert r["command_result"]["ok"] is True
    assert r["listening"] == {"type": "listening", "on": False}
    assert r["pong"] == {"type": "pong"}
    assert srv.pipeline.listening is False


@pytest.mark.asyncio
async def test_rejects_bad_auth_and_strangers(tmp_path):
    store = PairingStore(config_dir=tmp_path / "c")
    srv = CompanionServer(FakePipeline(), store)
    ws = FakeWS([json.dumps({"type": "pair", "token": "nope"}),
                 json.dumps({"type": "auth", "device_id": "x", "secret": "y"}),
                 json.dumps({"type": "ptt_start"}),
                 "not json",
                 json.dumps({"no": "type"}),
                 json.dumps({"type": "weird"})])
    await srv.handle(ws)
    kinds = [m["type"] for m in ws.sent]
    assert kinds.count("error") >= 5  # every unauthenticated step refused


@pytest.mark.asyncio
async def test_empty_ptt_and_binary_cap(tmp_path):
    store = PairingStore(config_dir=tmp_path / "c")
    srv = CompanionServer(FakePipeline(), store)
    token = store.issue_pairing_token()
    ws = FakeWS([json.dumps({"type": "pair", "token": token}),
                 json.dumps({"type": "ptt_stop"}),  # nothing recorded
                 json.dumps({"type": "ptt_start"}),
                 b"\x00" * 100,  # binary while recording: kept
                 json.dumps({"type": "ptt_stop"})])
    await srv.handle(ws)
    assert ws.sent[1]["kind"] == "empty"
    assert ws.sent[-1]["kind"] == "dictation"


def test_pcm_conversion():
    out = pcm16_to_float32(np.array([0, 16384, -32768], dtype=np.int16).tobytes())
    assert out.dtype == np.float32
    assert abs(float(out[0])) < 1e-9 and abs(float(out[1]) - 0.5) < 1e-4


def test_ssl_context_and_services(tmp_path, monkeypatch):
    store = PairingStore(config_dir=tmp_path / "c")
    ctx = ssl_context_for(store)
    import ssl as _ssl
    assert isinstance(ctx, _ssl.SSLContext)
    monkeypatch.setitem(sys.modules, "zeroconf", None)
    handles = start_companion_services({"companion": {"port": 18765}}, FakePipeline(),
                                       store=store)
    assert handles["server"].port == 18765
    assert len(handles["fingerprint"]) == 64
    assert handles["payload"].port == 18765
    assert handles["advertiser"].ok is False
    handles["advertiser"].close()


def test_serve_without_websockets_errors(tmp_path, monkeypatch):
    import asyncio as _aio
    store = PairingStore(config_dir=tmp_path / "c")
    srv = CompanionServer(FakePipeline(), store)
    monkeypatch.setitem(sys.modules, "websockets", None)
    with pytest.raises(RuntimeError):
        _aio.run(srv.serve())
