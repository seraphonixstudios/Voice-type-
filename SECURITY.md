# Security policy

## Threat model

VoiceType processes microphone audio and keystrokes. The design keeps both
on your machine: transcription (faster-whisper), wake word (openWakeWord),
and command parsing (Ollama) all run locally. The mobile companion stays on
your LAN with pinned TLS.

## Companion pairing safety

- The desktop uses a **self-signed certificate** generated on first run.
  Your phone **pins its SHA-256 fingerprint** (shown by `voicetype
  --companion` / `--show-qr`). Always compare the fingerprint on both
  screens before pairing — a mismatch means stop.
- Pairing tokens are **single-use and expire after 10 minutes**; never reuse
  or screenshot them into shared media.
- Device credentials are stored **hashed (SHA-256)** with `0600`
  permissions. Revoke a lost phone by deleting its entry from
  `companion_devices.json` in your VoiceType config dir.

## Reporting a vulnerability

Open a GitHub issue titled `[security]` with impact and reproduction steps,
**redacting any secrets**. Do not post pairing tokens, device credentials,
certificates, or unredacted logs. Expect acknowledgement within 7 days.

## Known hard limits (not vulnerabilities)

- Kernel-level anti-cheat (Vanguard, BattlEye, EAC…) blocks synthetic input
  by design; VoiceType refuses to inject there rather than risk your account.
- Some hardened macOS secure fields drop injected keystrokes entirely.
- These are documented in `README.md` under "Known limitations".
