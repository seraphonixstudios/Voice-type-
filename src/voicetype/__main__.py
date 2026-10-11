"""VoiceType entry point: `python -m voicetype`."""
from voicetype.app import main

if __name__ == "__main__":
    raise SystemExit(main())  # pragma: no cover - trampoline; runpy path covered in test_main_module_executes
