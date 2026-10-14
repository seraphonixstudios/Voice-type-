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
5. **Tests must never fire real OS input.** Backend unit tests always install
   the fakes (`_install_fake_quartz` / `_install_fake_windll` /
   `_install_fake_evdev`); a conftest guard neuters `pynput` suite-wide.
   Real `SendInput`/`CGEvent`/`uinput` calls in a test would type into the
   focused window of whoever runs the suite — `test_os_calls_intercepted_by_fakes`
   is the tripwire. This bit us once; never again.
6. **Tests must never launch OS settings, apps, or helpers.** A conftest
   guard fails any test whose `subprocess` call smells like a settings page,
   app launch, or privilege helper — a UI test once opened the Windows
   microphone settings on the dev machine on every run. Mock
   `subprocess.run` and assert on the attempted command instead.

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
