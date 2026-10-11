"""mDNS discovery: advertise the companion server on the LAN.

Service type `_voicetype._tcp.local.`, TXT records carry port, TLS flag, and
certificate fingerprint so the phone can verify before pairing. Requires the
optional `zeroconf` package; without it, advertisement is skipped with a
clear reason (QR pairing still works — it carries host + port directly).
"""
from __future__ import annotations

SERVICE_TYPE = "_voicetype._tcp.local."
SERVICE_PREFIX = "VoiceType"


class Advertiser:
    """Handle for one advertisement. `.ok` tells whether mDNS is live."""

    def __init__(self, ok: bool, reason: str = "", _close=None):
        self.ok = ok
        self.reason = reason
        self._close = _close

    def close(self) -> None:
        try:
            if self._close is not None:
                self._close()
        except Exception:
            pass

    def __enter__(self) -> "Advertiser":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def advertise(port: int, fingerprint: str, host: str | None = None,
              tls: bool = True) -> Advertiser:
    """Advertise on mDNS. Never raises: failure -> Advertiser(ok=False)."""
    try:
        from zeroconf import ServiceInfo, Zeroconf  # type: ignore
    except Exception as exc:
        return Advertiser(False, f"zeroconf not installed ({exc}); use QR pairing")
    try:
        import socket
        name = f"{SERVICE_PREFIX}-{socket.gethostname()}".replace(" ", "-")
        props = {b"port": str(int(port)).encode(),
                 b"tls": b"1" if tls else b"0",
                 b"fp": fingerprint.encode()}
        info = ServiceInfo(SERVICE_TYPE, f"{name}.{SERVICE_TYPE}",
                           addresses=[socket.inet_aton(host or "0.0.0.0")],
                           port=int(port), properties=props,
                           server=f"{socket.gethostname()}.local.")
        zc = Zeroconf()
        zc.register_service(info)
        return Advertiser(True, f"advertised as {name}", _close=lambda: (
            zc.unregister_service(info), zc.close()))
    except Exception as exc:
        return Advertiser(False, f"mDNS advertisement failed: {exc}")
