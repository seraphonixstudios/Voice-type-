# VoiceType mobile companion

Your phone becomes a remote mic + control surface for the desktop app.
No cloud, no account — everything stays on your local network (LAN).

## How it works

1. Desktop runs a TLS WebSocket server (`voicetype --companion`, port 8765).
2. Phone pairs by scanning a QR code (or automatic mDNS discovery, or manual
   token entry). The pairing token is single-use and expires after 10 minutes.
3. After pairing, the phone gets a per-device credential for later sessions.
4. Hold the on-screen button to talk: the phone streams 16 kHz PCM16 mic
   audio, the desktop transcribes and injects it into the focused app, and
   the transcript appears on the phone. A command palette sends editing
   commands (delete, scroll, undo, …).

## Desktop setup

```bash
pip install -e ".[companion]"   # websockets + cryptography (+ zeroconf, qrcode)
voicetype --companion           # start server, print pairing URI + QR
voicetype --show-qr             # print a fresh pairing URI without starting
```

- First run generates a self-signed certificate in
  `~/.config/voicetype/` and prints its SHA-256 fingerprint. The phone pins
  this fingerprint (TOFU) — verify it matches what the phone shows.
- `pip install qrcode` for a scannable `voicetype-pairing-qr.png`; otherwise
  use the printed URI / manual token entry.
- mDNS (`_voicetype._tcp.local.`) needs `zeroconf`; without it, QR pairing
  still works because it carries host + port directly.
- Config (`companion:` section): `host`, `port`, `tls`, `token_ttl_seconds`.

## Phone setup

Requires the Flutter SDK (3.2+): https://docs.flutter.dev/get-started/install

```bash
cd mobile
flutter pub get
flutter run            # debug on attached device
flutter build apk      # Android release
flutter build ipa      # iOS release (needs Xcode + signing)
```

Permissions the app requests: microphone (recording) and camera (QR scan).
Stored on-device: desktop host/port, certificate fingerprint, and your
device credential — nothing else, and nothing leaves the LAN.

## Honest caveats

- **iOS restricts background audio**: recording stops when the app
  backgrounds, so keep the app in the foreground while dictating. This is an
  iOS platform limit, not a bug.
- **Same Wi-Fi required**: phone and desktop must be on the same local
  network (guest/AP-isolated networks block LAN sockets and mDNS).
- **Live preview is per-utterance**: faster-whisper is not a streaming
  model, so the phone shows a live waveform while you hold the button and
  the transcript lands when you release.
- **First-run trust**: a self-signed certificate means *you* must compare
  the fingerprint shown by the desktop with the one on the phone. After
  that, the phone refuses any certificate that doesn't match.
