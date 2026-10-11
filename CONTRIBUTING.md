# Contributing to VoiceType

Thanks for stopping by! VoiceType is a local-first, privacy-respecting
voice keyboard, and contributions that protect that ethos are welcome.

## Ground rules

1. **No cloud, no accounts, no telemetry.** Any PR introducing network calls
   outside the LAN companion feature will be rejected.
2. **No per-app plugins.** Integration goes through the OS input layer
   (`src/voicetype/integration/`); new apps are covered by classification,
   never by special-case plugins.
3. **No placeholders.** Every module must be complete and functional, with
   tests. No stubs, no TODO comments.
4. **Never log secrets.** Secure-field text must only ever appear behind the
   redaction marker (see `BaseInjector.REDACTED`).

## Workflow

```bash
pip install -e ".[test]"
python -m pytest tests/ -q   # 90% coverage gate enforced via pytest-cov
```

- Put shared fixtures in `tests/conftest.py`; mock platform APIs so tests
  run on any OS (see `mock_platform_*`).
- Match the existing style: type hints, docstrings, `from __future__ import
  annotations` at the top of new modules.
- Update `README.md` and `docs/WHITEPAPER.md` when behavior changes.

## Reporting issues

Include OS + version, input device (`voicetype --list-mics`), the tray Logs
entry (redact secrets yourself), and whether the mic meter moves while your
mic is muted (that means the wrong input device — set `audio.device`).
