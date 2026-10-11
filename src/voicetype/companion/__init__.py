"""VoiceType mobile companion: phone as remote mic + control surface over LAN.

No cloud, no account. Desktop runs a TLS WebSocket server; the phone pairs by
scanning a QR code (or falling back to manual token entry), streams 16 kHz
PCM16 microphone audio while push-to-talk is held, and sends editing commands.

Security: self-signed certificate generated on first run (fingerprint shown
for TOFU verification), single-use pairing token with a short expiry that is
exchanged for a per-device credential after setup.
"""
from voicetype.companion.discovery import Advertiser, advertise
from voicetype.companion.pairing import PairingStore, QRPayload
from voicetype.companion.server import CompanionServer, start_companion_services

__all__ = [
    "Advertiser",
    "CompanionServer",
    "PairingStore",
    "QRPayload",
    "advertise",
    "start_companion_services",
]
