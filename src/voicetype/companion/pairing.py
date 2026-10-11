"""Pairing: single-use expiring token -> per-device credential + TLS identity.

- Pairing token: 32 random bytes, lives only in memory, expires after
  `token_ttl_seconds` (default 10 min), single use. Never written to disk.
- Device credential: issued over the TLS connection after a valid pairing,
  sha256 hash stored server-side (0600 file). Later sessions auth with it.
- TLS identity: self-signed RSA certificate generated on first run and stored
  in the config dir. The SHA-256 fingerprint travels in the QR payload so the
  phone can pin it (TOFU) instead of blindly trusting.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class QRPayload:
    host: str
    port: int
    token: str
    fingerprint: str
    tls: bool = True

    def to_uri(self) -> str:
        from urllib.parse import urlencode
        q = urlencode({"host": self.host, "port": self.port, "token": self.token,
                       "fp": self.fingerprint, "tls": int(self.tls)})
        return f"voicetype://pair?{q}"

    @classmethod
    def from_uri(cls, uri: str) -> "QRPayload":
        from urllib.parse import parse_qs, urlparse
        u = urlparse(uri)
        if u.scheme != "voicetype" or u.netloc != "pair":
            raise ValueError(f"not a VoiceType pairing URI: {uri[:40]}")
        q = parse_qs(u.query)
        try:
            return cls(host=q["host"][0], port=int(q["port"][0]),
                       token=q["token"][0], fingerprint=q["fp"][0],
                       tls=bool(int(q.get("tls", ["1"])[0])))
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"malformed pairing URI: {exc}") from exc


@dataclass
class _PendingToken:
    token: str
    created_at: float
    ttl: float

    def expired(self, now: float) -> bool:
        return (now - self.created_at) > self.ttl


class PairingStore:
    """Token + device-credential + certificate lifecycle."""

    def __init__(self, config_dir: Path | str | None = None,
                 token_ttl_seconds: float = 600.0):
        self.config_dir = Path(config_dir) if config_dir else default_config_dir()
        self.token_ttl = float(token_ttl_seconds)
        self._pending: dict[str, _PendingToken] = {}
        self._devices: dict[str, dict] = {}
        self._devices_path = self.config_dir / "companion_devices.json"
        self._cert_path = self.config_dir / "companion_cert.pem"
        self._key_path = self.config_dir / "companion_key.pem"
        self._load_devices()

    # ---- pairing tokens (memory only) ----
    def issue_pairing_token(self) -> str:
        self.purge_expired()
        token = secrets.token_urlsafe(32)
        self._pending[token] = _PendingToken(token, time.time(), self.token_ttl)
        return token

    def consume_pairing_token(self, token: str) -> bool:
        """Single-use: valid exactly once, then gone. Returns acceptance."""
        self.purge_expired()
        entry = self._pending.pop(token or "", None)
        return entry is not None

    def purge_expired(self) -> int:
        now = time.time()
        dead = [t for t, e in self._pending.items() if e.expired(now)]
        for t in dead:
            del self._pending[t]
        return len(dead)

    # ---- device credentials (hashed, on disk 0600) ----
    def issue_device_credential(self) -> tuple[str, str]:
        device_id = secrets.token_urlsafe(8)
        secret = secrets.token_urlsafe(32)
        self._devices[device_id] = {
            "hash": hashlib.sha256(secret.encode()).hexdigest(),
            "created_at": time.time(),
        }
        self._save_devices()
        return device_id, secret

    def verify_device(self, device_id: str, secret: str) -> bool:
        rec = self._devices.get(device_id or "")
        if not rec:
            return False
        return hmac.compare_digest(rec["hash"],
                                   hashlib.sha256((secret or "").encode()).hexdigest())

    def revoke_device(self, device_id: str) -> bool:
        if device_id in self._devices:
            del self._devices[device_id]
            self._save_devices()
            return True
        return False

    def _load_devices(self) -> None:
        try:
            if self._devices_path.exists():
                self._devices = json.loads(self._devices_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._devices = {}

    def _save_devices(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._devices_path.write_text(json.dumps(self._devices), encoding="utf-8")
        try:
            os.chmod(self._devices_path, 0o600)
        except OSError:
            pass

    # ---- TLS identity (self-signed, first run) ----
    def cert_paths(self) -> tuple[Path, Path]:
        """Return (cert, key), generating once on first run."""
        if not (self._cert_path.exists() and self._key_path.exists()):
            self._generate_self_signed()
        return self._cert_path, self._key_path

    def certificate_fingerprint(self) -> str:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        cert_pem = self.cert_paths()[0].read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)
        return cert.fingerprint(hashes.SHA256()).hex()

    def _generate_self_signed(self) -> None:
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VoiceType-Companion")])
        sans = [x509.DNSName(socket.gethostname()), x509.DNSName("localhost")]
        for ip in ("127.0.0.1", lan_ip()):
            try:
                import ipaddress
                sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
            except ValueError:
                continue
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now).not_valid_after(now + timedelta(days=825))
                .add_extension(x509.SubjectAlternativeName(sans), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
        self._cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        for p in (self._key_path, self._cert_path):
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass

    # ---- QR ----
    def pairing_payload(self, host: str, port: int) -> QRPayload:
        return QRPayload(host=host, port=int(port),
                         token=self.issue_pairing_token(),
                         fingerprint=self.certificate_fingerprint(), tls=True)

    def qr_png(self, payload: QRPayload) -> bytes:
        """QR PNG bytes. Requires the optional `qrcode` package."""
        import qrcode  # type: ignore
        img = qrcode.make(payload.to_uri())
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def qr_ascii(self, payload: QRPayload) -> str:
        """Fallback when `qrcode` is missing: framed URI for manual entry."""
        uri = payload.to_uri()
        bar = "+" + "-" * (len(uri) + 2) + "+"
        return f"{bar}\n| {uri} |\n{bar}"


def default_config_dir() -> Path:
    import os as _os
    base = _os.environ.get("VOICETYPE_CONFIG_DIR")
    if base:
        return Path(base)
    return Path.home() / ".config" / "voicetype"


def lan_ip() -> str:
    """Best-effort LAN IPv4 without sending traffic (UDP connect trick)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"
