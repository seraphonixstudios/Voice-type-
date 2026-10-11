from voicetype import permissions as perm
from voicetype.ollama_parser import apply_auto_punctuation


def test_check_all_returns_platform_status():
    statuses = perm.check_all()
    assert len(statuses) >= 2
    assert all(hasattr(s, "ok") and hasattr(s, "detail") for s in statuses)
    names = [s.name for s in statuses]
    assert "microphone" in names


def test_linux_uinput_check_shape(monkeypatch):
    monkeypatch.setattr(perm.os.path, "exists", lambda p: False)
    st = perm.check_linux_uinput()
    assert st.name == "linux_uinput" and isinstance(st.ok, bool) and st.fix_hint


def test_windows_fix_attempt(monkeypatch):
    calls = []
    monkeypatch.setattr(perm.subprocess, "run", lambda *a, **k: calls.append(a) or type("R", (), {})())
    ok, msg = perm.fix_windows_uia()
    assert ok is True and calls


def test_auto_punctuation():
    assert apply_auto_punctuation("hello world") == "Hello world."
    assert apply_auto_punctuation("Hello world!") == "Hello world!"
    assert apply_auto_punctuation("") == ""
