"""Universal integration matrix: 20+ representative apps per platform.

Every case runs on any OS: platform APIs are mocked, and routing is pure
logic over (app, bundle, title, role). Behavior tests cover raw terminals,
plain secure fields, RTF office, code-aware IDEs, anti-cheat blocks, the
clipboard fallback chain, unicode, and multiline input.
"""
from __future__ import annotations

import pytest

from voicetype.injector import FallbackInjector
from voicetype.integration.categories import (
    BROWSER,
    GAME,
    GENERIC,
    IDE,
    OFFICE,
    PASSWORD_MANAGER,
    TERMINAL,
    classify_app,
    classify_field,
    is_secure_context,
)
from voicetype.integration.detectors import (
    detect_anticheat,
    should_block_injection,
)
from voicetype.integration.router import IntegrationRouter
from voicetype.integration.strategies import (
    preserve_indentation,
    to_plain_text,
    to_rtf,
)

# (app, bundle, expected_category)
MAC_APPS = [
    ("Terminal", "com.apple.Terminal", TERMINAL),
    ("iTerm2", "com.googlecode.iterm2", TERMINAL),
    ("WezTerm", "com.wez.wezterm", TERMINAL),
    ("Alacritty", "org.alacritty", TERMINAL),
    ("kitty", "net.kovidgoyal.kitty", TERMINAL),
    ("Safari", "com.apple.Safari", BROWSER),
    ("Google Chrome", "com.google.Chrome", BROWSER),
    ("Firefox", "org.mozilla.firefox", BROWSER),
    ("Arc", "company.thebrowser.arc", BROWSER),
    ("Microsoft Word", "com.microsoft.Word", OFFICE),
    ("Pages", "com.apple.iWork.Pages", OFFICE),
    ("Notion", "notion.id", OFFICE),
    ("Obsidian", "md.obsidian", OFFICE),
    ("Xcode", "com.apple.dt.Xcode", IDE),
    ("Visual Studio Code", "com.microsoft.VSCode", IDE),
    ("Sublime Text", "com.sublimetext.4", IDE),
    ("Steam", "com.valvesoftware.steam", GAME),
    ("Minecraft", "com.mojang.minecraft", GAME),
    ("1Password", "com.1password.1password", PASSWORD_MANAGER),
    ("Keychain Access", "com.apple.keychainaccess", PASSWORD_MANAGER),
    ("TextEdit", "com.apple.TextEdit", GENERIC),
    ("Mail", "com.apple.mail", GENERIC),
]

WINDOWS_APPS = [
    ("Windows Terminal", "wt.exe", TERMINAL),
    ("Windows PowerShell", "powershell.exe", TERMINAL),
    ("Command Prompt", "cmd.exe", TERMINAL),
    ("PuTTY", "putty.exe", TERMINAL),
    ("Microsoft Edge", "msedge.exe", BROWSER),
    ("Google Chrome", "chrome.exe", BROWSER),
    ("Firefox", "firefox.exe", BROWSER),
    ("Brave", "brave.exe", BROWSER),
    ("Microsoft Word", "winword.exe", OFFICE),
    ("Outlook", "outlook.exe", OFFICE),
    ("Notion", "notion.exe", OFFICE),
    ("Visual Studio Code", "code.exe", IDE),
    ("PyCharm", "pycharm64.exe", IDE),
    ("Notepad++", "notepad++.exe", IDE),
    ("Notepad", "notepad.exe", IDE),
    ("Steam", "steam.exe", GAME),
    ("Valorant", "valorant.exe", GAME),
    ("Epic Games Launcher", "epicgameslauncher.exe", GAME),
    ("Bitwarden", "bitwarden.exe", PASSWORD_MANAGER),
    ("KeePassXC", "keepassxc.exe", PASSWORD_MANAGER),
    ("WordPad", "wordpad.exe", OFFICE),  # rich-capable editor -> RTF strategy
    ("Calculator", "calculator.exe", GENERIC),
]

LINUX_APPS = [
    ("gnome-terminal", "gnome-terminal-server", TERMINAL),
    ("Konsole", "konsole", TERMINAL),
    ("Alacritty", "alacritty", TERMINAL),
    ("kitty", "kitty", TERMINAL),
    ("foot", "foot", TERMINAL),
    ("Tilix", "tilix", TERMINAL),
    ("Firefox", "firefox", BROWSER),
    ("Chromium", "chromium", BROWSER),
    ("Brave", "brave", BROWSER),
    ("LibreOffice Writer", "soffice.bin", OFFICE),
    ("Obsidian", "obsidian", OFFICE),
    ("Code - OSS", "code-oss", IDE),
    ("PyCharm", "pycharm", IDE),
    ("Vim", "vim", IDE),
    ("Emacs", "emacs", IDE),
    ("gedit", "gedit", IDE),
    ("Steam", "steam", GAME),
    ("Minecraft", "minecraft", GAME),
    ("KeePassXC", "keepassxc", PASSWORD_MANAGER),
    ("Bitwarden", "bitwarden", PASSWORD_MANAGER),
    ("gedit-scratch", "gedit", IDE),
    ("Files", "nautilus", GENERIC),
]


@pytest.mark.parametrize("app,bundle,expected", MAC_APPS)
def test_mac_app_matrix(app, bundle, expected):
    assert classify_app(app, bundle, "") == expected


@pytest.mark.parametrize("app,bundle,expected", WINDOWS_APPS)
def test_windows_app_matrix(app, bundle, expected):
    assert classify_app(app, bundle, "") == expected


@pytest.mark.parametrize("app,bundle,expected", LINUX_APPS)
def test_linux_app_matrix(app, bundle, expected):
    assert classify_app(app, bundle, "") == expected


def test_unknown_app_is_generic():
    assert classify_app("Some Random App v9", "", "") == GENERIC
    assert classify_app("", "", "") == GENERIC


# ---------- field roles ----------
def test_password_roles():
    assert classify_field("AXSecureTextField", "", BROWSER) == "password"
    assert classify_field("password", "", GENERIC) == "password"
    assert classify_field("text", "Master password", PASSWORD_MANAGER) == "password"
    assert classify_field("addressbar", "Address", BROWSER) == "address_bar"
    assert classify_field("document", "Article", BROWSER) == "contenteditable"


def test_secure_contexts():
    assert is_secure_context(PASSWORD_MANAGER, "plain") is True
    assert is_secure_context(BROWSER, "password") is True
    assert is_secure_context(BROWSER, "secure") is True
    assert is_secure_context(TERMINAL, "plain") is False
    assert is_secure_context(GENERIC, "plain") is False


# ---------- anti-cheat ----------
def test_anticheat_detected_by_process_name():
    assert detect_anticheat(["game.exe", "EasyAntiCheat.exe"]) == ["easyanticheat.exe"]
    assert detect_anticheat(["vgc.exe", "notepad.exe"]) == ["vgc.exe"]
    assert detect_anticheat(["firefox", "code"]) == []


def test_games_blocked_only_with_anticheat(monkeypatch):
    import voicetype.integration.detectors as det
    monkeypatch.setattr(det, "detect_anticheat", lambda processes=None: ["vgk"])
    blocked, reason = should_block_injection(GAME)
    assert blocked is True and "Anti-cheat" in reason
    monkeypatch.setattr(det, "detect_anticheat", lambda processes=None: [])
    assert should_block_injection(GAME) == (False, "")
    assert should_block_injection(TERMINAL) == (False, "")


# ---------- router strategies ----------
def _router(**kw):
    kw.setdefault("platform_name", "Linux")
    return IntegrationRouter(FallbackInjector(), context=None, **kw)


def test_terminal_uses_raw_strategy():
    r = _router(platform_name="Darwin")
    s = r.resolve_strategy({"category": TERMINAL, "field_role": "plain",
                            "app": "Terminal", "secure": False})
    assert s.mode == "raw"


def test_windows_console_uses_vk_path(mock_platform_windows):
    inj = FallbackInjector()
    r = IntegrationRouter(inj, context=None, platform_name="Windows")
    res = r.inject_text("dir", {"category": TERMINAL, "field_role": "plain",
                                "app": "cmd.exe", "secure": False})
    assert res.ok is True
    assert ("primary", "type_console/vk") in res.audit


def test_browser_address_bar_plain_no_markdown():
    r = _router()
    res = r.inject_text("**bold** [x](http://e.com)",
                        {"category": BROWSER, "field_role": "address_bar",
                         "app": "Firefox", "secure": False})
    assert res.ok is True and res.strategy.mode == "plain"
    assert r.injector.typed_log == ["bold x"]


def test_browser_content_keeps_markdown():
    r = _router()
    res = r.inject_text("**hi**", {"category": BROWSER,
                                   "field_role": "contenteditable",
                                   "app": "Firefox", "secure": False})
    assert res.strategy.mode == "rich"
    assert r.injector.typed_log[-1] == "**hi**"


def test_office_rich_and_plain_fallback():
    r = _router(platform_name="Windows")
    res = r.inject_text("hello", {"category": OFFICE, "field_role": "rich_text",
                                  "app": "Microsoft Word", "secure": False})
    assert res.strategy.mode == "rich"
    r2 = IntegrationRouter(FallbackInjector(), context=None, rich_text=False)
    res2 = r2.inject_text("hello", {"category": OFFICE, "field_role": "rich_text",
                                    "app": "Microsoft Word", "secure": False})
    assert res2.strategy.mode == "plain"


def test_ide_preserves_indentation():
    r = _router()
    res = r.inject_text("def f():\n    return 1",
                        {"category": IDE, "field_role": "code",
                         "app": "Visual Studio Code", "secure": False})
    assert res.strategy.mode == "code"
    assert r.injector.typed_log[-1] == "def f():\n    return 1"


def test_secure_field_plain_only_and_redacted():
    warnings: list = []
    r = IntegrationRouter(FallbackInjector(), context=None,
                          warn_callback=warnings.append)
    secret = "s3cr3t-pw"
    res = r.inject_text(secret, {"category": PASSWORD_MANAGER,
                                 "field_role": "plain",
                                 "app": "1Password", "secure": True})
    assert res.ok is True and res.redacted is True
    assert res.strategy.mode == "plain"
    assert secret not in r.injector.typed_log
    assert secret not in str(r.injector.events_log)
    assert warnings == []  # no warning needed: plain path succeeded


def test_game_block_warns_and_types_nothing(monkeypatch):
    import voicetype.integration.detectors as det
    monkeypatch.setattr(det, "detect_anticheat", lambda processes=None: ["battleye"])
    warnings: list = []
    r = IntegrationRouter(FallbackInjector(), context=None,
                          warn_callback=warnings.append)
    res = r.inject_text("hello team", {"category": GAME, "field_role": "plain",
                                       "app": "Steam Game", "secure": False})
    assert res.ok is False and res.strategy.mode == "blocked"
    assert "Anti-cheat" in res.warning and warnings == [res.warning]
    assert r.injector.typed_log == []


def test_context_failure_defaults_to_plain():
    class BrokenContext:
        def refresh(self):
            raise RuntimeError("AT-SPI down")

        def current(self):
            raise RuntimeError("AT-SPI down")

    r = IntegrationRouter(FallbackInjector(), context=BrokenContext())
    res = r.inject_text("fallback text")
    assert res.ok is True and res.strategy.mode == "plain"


def test_fallback_chain_clipboard_then_warn():
    inj = FallbackInjector()
    inj.type_text = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("uinput gone"))
    inj.paste_text = lambda text: False  # clipboard also unavailable
    warnings: list = []
    r = IntegrationRouter(inj, context=None, warn_callback=warnings.append)
    res = r.inject_text("important", {"category": GENERIC, "field_role": "plain",
                                      "app": "X", "secure": False})
    assert res.ok is False and "manually" in res.warning
    assert warnings == [res.warning]


def test_fallback_chain_clipboard_recovers():
    inj = FallbackInjector()
    inj.type_text = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("uinput gone"))
    inj.paste_text = lambda text: True
    r = IntegrationRouter(inj, context=None)
    res = r.inject_text("saved", {"category": GENERIC, "field_role": "plain",
                                  "app": "X", "secure": False})
    assert res.ok is True and res.strategy.fallback_used is True


def test_unicode_and_multiline_end_to_end():
    r = _router()
    res = r.inject_text("caf\u00e9 \u4e2d")
    assert res.ok is True
    assert r.injector.typed_log == ["caf\u00e9 \u4e2d"]
    r2 = _router()
    res2 = r2.inject_text("line1\nline2\nline3")
    assert res2.ok is True
    assert r2.injector.typed_log == ["line1\nline2\nline3"]
    assert sum(1 for e in r2.injector.events_log if e == ("key", "enter")) == 2


def test_empty_text_is_noop():
    r = _router()
    assert r.inject_text("").ok is True
    assert r.injector.typed_log == []


def test_ime_strategy_on_linux():
    r = IntegrationRouter(FallbackInjector(), context=None, platform_name="Linux")
    res = r.inject_text("hello", {"category": GENERIC, "field_role": "plain",
                                  "app": "fcitx IME", "secure": False})
    assert res.strategy.mode == "ime"
    assert ("preedit", "hello ") in res.audit or any(
        a[0] == "preedit" for a in res.audit)


def test_strategies_unit():
    assert to_plain_text("**b** and [t](http://x)") == "b and t"
    assert to_plain_text("# head\n> quote") == "head\nquote"
    rtf = to_rtf("**bold** and `code`")
    assert rtf.startswith("{\\rtf1") and "\\b " in rtf and rtf.endswith("}")
    assert preserve_indentation("def f():\n        return 1", "") == "def f():\n        return 1"
    assert preserve_indentation("def f():\n        return 1", "  ") == "  def f():\n          return 1"
    assert preserve_indentation("line", "  ") == "  line"
