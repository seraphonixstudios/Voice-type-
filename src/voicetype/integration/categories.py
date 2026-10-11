"""App/field categorization: no per-app plugins, pure name matching.

VoiceType never talks to apps directly. Classification exists only to pick the
right OS-level injection strategy (raw keycodes vs plain vs rich vs blocked).
Unknown apps safely default to the "generic" category (plain text injection).
"""
from __future__ import annotations

# Categories
TERMINAL = "terminal"
BROWSER = "browser"
OFFICE = "office"
IDE = "ide"
GAME = "game"
PASSWORD_MANAGER = "password_manager"
GENERIC = "generic"

# Field roles (from Accessibility / UI Automation / AT-SPI, or heuristics)
ROLE_PASSWORD = "password"
ROLE_ADDRESS_BAR = "address_bar"
ROLE_CONTENTEDITABLE = "contenteditable"
ROLE_CODE = "code"
ROLE_RICH_TEXT = "rich_text"
ROLE_PLAIN = "plain"
ROLE_SECURE = "secure"
ROLE_UNKNOWN = "unknown"

# ---- known app fingerprints: (match, category). Substring, case-insensitive.
# Bundle IDs / exe names included where distinctive.
_APP_FINGERPRINTS: tuple[tuple[str, str], ...] = (
    # Terminals
    ("terminal", TERMINAL), ("iterm", TERMINAL), ("wezterm", TERMINAL),
    ("alacritty", TERMINAL), ("kitty", TERMINAL), ("gnome-terminal", TERMINAL),
    ("konsole", TERMINAL), ("xterm", TERMINAL), ("urxvt", TERMINAL),
    ("st-", TERMINAL), ("foot", TERMINAL), ("tilix", TERMINAL),
    ("terminator", TERMINAL), ("hyper", TERMINAL), ("tabby", TERMINAL),
    ("powershell", TERMINAL), ("cmd.exe", TERMINAL), ("command prompt", TERMINAL),
    ("putty", TERMINAL),
    ("windows terminal", TERMINAL), ("wt.exe", TERMINAL), ("wsl", TERMINAL),
    # Browsers
    ("firefox", BROWSER), ("chrome", BROWSER), ("chromium", BROWSER),
    ("safari", BROWSER), ("edge", BROWSER), ("msedge", BROWSER),
    ("opera", BROWSER), ("vivaldi", BROWSER), ("brave", BROWSER),
    ("arc", BROWSER), ("zen", BROWSER), ("librewolf", BROWSER),
    # Password managers (before office: "1password" contains "word")
    ("1password", PASSWORD_MANAGER), ("bitwarden", PASSWORD_MANAGER),
    ("keepass", PASSWORD_MANAGER), ("keepassxc", PASSWORD_MANAGER),
    ("lastpass", PASSWORD_MANAGER), ("dashlane", PASSWORD_MANAGER),
    ("keychain", PASSWORD_MANAGER),
    # Office / rich text
    ("microsoft word", OFFICE), ("winword", OFFICE), ("word", OFFICE),
    ("libreoffice writer", OFFICE), ("soffice", OFFICE), ("pages", OFFICE),
    ("google docs", OFFICE), ("notion", OFFICE), ("obsidian", OFFICE),
    ("evernote", OFFICE), ("outlook", OFFICE), ("thunderbird", OFFICE),
    ("excel", OFFICE), ("powerpoint", OFFICE),
    # IDEs / editors
    ("vscode", IDE), ("visual studio code", IDE), ("code.exe", IDE), ("code - oss", IDE),
    ("pycharm", IDE), ("intellij", IDE), ("webstorm", IDE),
    ("sublime", IDE), ("atom", IDE), ("vim", IDE), ("neovim", IDE),
    ("emacs", IDE), ("nano", IDE), ("notepad++", IDE), ("notepad", IDE),
    ("gedit", IDE), ("kate", IDE), ("xcode", IDE), ("android studio", IDE),
    ("eclipse", IDE), ("helix", IDE),
    # Games / launchers (also see anti-cheat list in detectors.py)
    ("steam", GAME), ("minecraft", GAME), ("fortnite", GAME),
    ("valorant", GAME), ("league of legends", GAME), ("overwatch", GAME),
    ("counter-strike", GAME), ("cs2", GAME), ("apex legends", GAME),
    ("elden ring", GAME), ("epic games", GAME), ("gog galaxy", GAME),
    ("battle.net", GAME), ("ubisoft connect", GAME), ("roblox", GAME),
    ("dota", GAME),
)

_PASSWORD_HINTS = ("password", "passwort", "mot de passe", "passcode", "pin",
                   "master password", "login", "credentials", "2fa", "otp")

_SECURE_ROLE_NAMES = {"password", "securetextfield", "secure text field",
                      "securefield", "axsecuretextfield"}


def classify_app(app: str = "", bundle: str = "", title: str = "") -> str:
    """Map app/bundle/title to a category. Unknown -> "generic"."""
    hay = f"{app or ''}\n{bundle or ''}".lower()
    for needle, category in _APP_FINGERPRINTS:
        if needle in hay:
            return category
    return GENERIC


def classify_field(role: str = "", title: str = "", category: str = GENERIC) -> str:
    """Map a focused-element role + title to a field role."""
    r = (role or "").strip().lower().replace("_", "").replace("-", "")
    t = (title or "").lower()
    if r in _SECURE_ROLE_NAMES or "password" in r:
        return ROLE_PASSWORD
    if r in {"secure", "securefield"}:
        return ROLE_SECURE
    if any(h in t for h in _PASSWORD_HINTS) and category in (BROWSER, PASSWORD_MANAGER, GENERIC):
        return ROLE_PASSWORD
    if r in {"addressbar", "urlbar", "omnibox", "locationbar"} or "address" in t and category == BROWSER:
        return ROLE_ADDRESS_BAR
    if r in {"contenteditable", "document", "webarea"} or category == BROWSER:
        # Address/password handled above; the rest of a browser is web content.
        return ROLE_CONTENTEDITABLE if category == BROWSER else ROLE_UNKNOWN
    if category == TERMINAL:
        return ROLE_PLAIN
    if category == IDE:
        return ROLE_CODE
    if category == OFFICE:
        return ROLE_RICH_TEXT
    if not role:
        return ROLE_UNKNOWN
    return ROLE_PLAIN


def is_secure_context(category: str, field_role: str) -> bool:
    """True when text must be treated as a secret (plain-only, never logged)."""
    return category == PASSWORD_MANAGER or field_role in (ROLE_PASSWORD, ROLE_SECURE)
