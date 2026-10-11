from voicetype.injector import FallbackInjector, get_injector


def test_type_text_splits_newlines_and_logs():
    inj = FallbackInjector()
    inj.type_text("hi\nthere")
    assert inj.typed_log == ["hi\nthere"]
    kinds = [e[0] for e in inj.events_log]
    assert "text" in kinds and "combo" in kinds  # enter pressed between lines


def test_unicode_logged_even_without_os_backend():
    inj = FallbackInjector()
    inj.type_text("caf\u00e9 \u4e2d")
    assert inj.typed_log == ["caf\u00e9 \u4e2d"]


def test_delete_word_and_line_events():
    inj = FallbackInjector()
    inj.delete("word", "2")
    inj.delete("line")
    inj.delete("selection")
    assert ("delete", "word", 2) in inj.events_log
    assert any(e[0] == "delete" and e[1] == "line" for e in inj.events_log)


def test_undo_redo_paragraph_scroll():
    inj = FallbackInjector()
    inj.undo()
    inj.redo()
    inj.new_paragraph()
    inj.scroll("down", 3)
    assert ("combo", ("ctrl", "z")) in inj.events_log or \
        ("combo", ("command", "z")) in inj.events_log
    assert ("scroll", "down", 3) in inj.events_log


def test_get_injector_auto_returns_working_injector(monkeypatch):
    import ctypes
    import types
    # Hermetic: even the "auto" backend must not touch real OS input APIs.
    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(user32=types.SimpleNamespace(
                            SendInput=lambda *a: 0)), raising=False)
    inj = get_injector("auto")
    inj.type_text("hello")
    assert inj.typed_log == ["hello"]
    assert get_injector("fallback").name == "fallback"
