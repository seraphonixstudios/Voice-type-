"""IntegrationRouter: pick the OS-level strategy per context, with fallbacks.

Fallback chain: primary injection -> clipboard paste -> user warning.
If context detection fails, default to plain-text injection with no formatting.
Secure contexts (passwords, managers) use plain-only injection and are never
logged or stored. Games with anti-cheat are blocked with a clear warning.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from voicetype.integration import detectors
from voicetype.integration.categories import (
    BROWSER,
    GAME,
    GENERIC,
    IDE,
    OFFICE,
    PASSWORD_MANAGER,
    ROLE_ADDRESS_BAR,
    ROLE_CODE,
    ROLE_CONTENTEDITABLE,
    ROLE_PASSWORD,
    ROLE_RICH_TEXT,
    ROLE_SECURE,
    TERMINAL,
    classify_app,
    classify_field,
    is_secure_context,
)
from voicetype.integration.strategies import (
    ime_events,
    normalize_newlines,
    preserve_indentation,
    to_plain_text,
    to_rtf,
)


@dataclass
class Strategy:
    mode: str  # raw | plain | rich | code | ime | blocked
    category: str = GENERIC
    field_role: str = "unknown"
    secure: bool = False
    reason: str = ""
    fallback_used: bool = False


@dataclass
class InjectionResult:
    ok: bool
    strategy: Strategy
    warning: str = ""
    redacted: bool = False
    audit: list[tuple] = field(default_factory=list)


class IntegrationRouter:
    """Routes text through the OS input layer based on app + field context."""

    def __init__(self, injector, context=None, platform_name: str | None = None,
                 clipboard_fallback: bool = True,
                 warn_callback: Callable[[str], None] | None = None,
                 rich_text: bool = True, ime_composition: bool = True,
                 base_indent: str = ""):
        self.injector = injector
        self.context = context
        self.clipboard_fallback = bool(clipboard_fallback)
        self.warn_callback = warn_callback
        self.rich_text = bool(rich_text)
        self.ime_composition = bool(ime_composition)
        self.base_indent = base_indent
        self.history: list[InjectionResult] = []
        self._platform_override = platform_name

    # ---- context ----
    def _platform(self) -> str:
        if self._platform_override:
            return self._platform_override
        import platform
        return platform.system()

    def current_enriched_context(self) -> dict:
        raw: dict = {}
        if self.context is not None:
            try:
                self.context.refresh()
            except Exception:
                pass
            try:
                raw = dict(self.context.current())
            except Exception:
                raw = {}
        category = classify_app(raw.get("app", ""), raw.get("bundle", ""),
                                raw.get("title", ""))
        try:
            role_info = detectors.get_focused_role()
        except Exception:
            role_info = {"role": "unknown", "is_secure": False,
                         "is_password": False, "control_type": ""}
        field_role = classify_field(role_info.get("role", ""),
                                    raw.get("title", ""), category)
        if role_info.get("is_secure") or role_info.get("is_password"):
            field_role = ROLE_PASSWORD
        enriched = dict(raw)
        enriched.update({"category": category, "field_role": field_role,
                         "role_info": role_info,
                         "secure": is_secure_context(category, field_role)})
        return enriched

    # ---- strategy ----
    def resolve_strategy(self, ctx: dict | None = None) -> Strategy:
        ctx = ctx or self.current_enriched_context()
        category = ctx.get("category") or GENERIC
        field_role = ctx.get("field_role") or "unknown"
        secure = bool(ctx.get("secure"))
        plat = self._platform()
        # Games + anti-cheat: hard block.
        blocked, reason = detectors.should_block_injection(category)
        if blocked:
            return Strategy(mode="blocked", category=category,
                            field_role=field_role, secure=secure, reason=reason)
        if secure or field_role in (ROLE_PASSWORD, ROLE_SECURE):
            return Strategy(mode="plain", category=category, field_role=field_role,
                            secure=True, reason="secure field: plain-only, never logged")
        if category == TERMINAL:
            if plat == "Windows" and "powershell" not in (ctx.get("app") or "").lower() \
                    and "cmd" not in (ctx.get("app") or "").lower():
                pass
            return Strategy(mode="raw", category=category, field_role=field_role,
                            reason="terminal: raw keycodes, no printable conversion")
        if category == BROWSER:
            if field_role in (ROLE_ADDRESS_BAR, ROLE_PASSWORD):
                return Strategy(mode="plain", category=category, field_role=field_role,
                                reason="browser chrome: plain text, no markdown")
            return Strategy(mode="plain" if not self.rich_text else "rich",
                            category=category, field_role=ROLE_CONTENTEDITABLE,
                            reason="browser content: markdown preserved" if self.rich_text
                            else "browser content: rich disabled, plain fallback")
        if category == OFFICE:
            if self.rich_text:
                return Strategy(mode="rich", category=category, field_role=ROLE_RICH_TEXT,
                                reason="office: RTF where supported, plain fallback")
            return Strategy(mode="plain", category=category, field_role=field_role,
                            reason="office: rich disabled, plain fallback")
        if category == IDE or field_role == ROLE_CODE:
            return Strategy(mode="code", category=category, field_role=ROLE_CODE,
                            reason="editor: code-aware, indentation preserved")
        if plat == "Linux" and self.ime_composition and self._looks_cjk(ctx):
            return Strategy(mode="ime", category=category, field_role=field_role,
                            reason="IME-aware target: composition events")
        return Strategy(mode="plain", category=category, field_role=field_role,
                        reason="default: plain text, no formatting")

    @staticmethod
    def _looks_cjk(ctx: dict) -> bool:
        blob = f"{ctx.get('app', '')} {ctx.get('title', '')}".lower()
        return any(k in blob for k in ("ime", "pinyin", "mozc", "hangul", "chinese",
                                       "japanese", "korean", "fcitx", "ibus"))

    # ---- injection ----
    def inject_text(self, text: str, ctx: dict | None = None) -> InjectionResult:
        text = normalize_newlines(text or "")
        if not text:
            return InjectionResult(ok=True, strategy=Strategy(mode="plain",
                                                             reason="empty: nothing to do"))
        ctx = ctx or self.current_enriched_context()
        strategy = self.resolve_strategy(ctx)
        if strategy.mode == "blocked":
            self._warn(strategy.reason)
            res = InjectionResult(ok=False, strategy=strategy, warning=strategy.reason)
            self.history.append(res)
            return res
        secure = strategy.secure
        inj = self.injector
        inj.last_error = None
        audit: list[tuple] = [("strategy", strategy.mode, strategy.category,
                               strategy.field_role)]
        try:
            if strategy.mode == "raw":
                self._inject_raw_terminal(text, audit)
            elif strategy.mode == "rich":
                self._inject_rich(text, audit)
            elif strategy.mode == "code":
                inj.type_text(preserve_indentation(text, self.base_indent),
                              sensitive=secure)
                audit.append(("primary", "type_text/code"))
            elif strategy.mode == "ime":
                for kind, chunk in ime_events(text, inj.chunk_size):
                    audit.append((kind, chunk[:24]))
                ok = inj.compose_text(text)
                if not ok:
                    raise RuntimeError(inj.last_error or "composition failed")
            else:  # plain (incl. secure + address bars + password fields)
                payload = to_plain_text(text) if strategy.field_role in (
                    ROLE_ADDRESS_BAR, ROLE_PASSWORD, ROLE_SECURE) else text
                if ctx.get("category") == TERMINAL or strategy.mode == "raw":
                    inj.type_raw(payload)
                else:
                    inj.type_text(payload, sensitive=secure)
                audit.append(("primary", "type_text/plain"))
        except Exception as exc:
            inj.last_error = inj.last_error or str(exc)
        primary_error = inj.last_error  # paste_text() resets it; keep the cause
        if primary_error and self.clipboard_fallback and not secure:
            audit.append(("fallback", "clipboard-paste"))
            try:
                if inj.paste_text(text):
                    strategy.fallback_used = True
                    inj.last_error = None
                else:
                    inj.last_error = primary_error
            except Exception as exc:
                inj.last_error = primary_error or str(exc)
        if secure:
            # Defense in depth: even if a backend logged, scrub it here.
            self._scrub_secret(inj, text)
        if inj.last_error:
            warning = (f"Injection failed ({inj.last_error}). "
                       f"Copy your text manually — nothing was typed.")
            self._warn(warning)
            res = InjectionResult(ok=False, strategy=strategy, warning=warning,
                                  redacted=secure, audit=audit)
        else:
            res = InjectionResult(ok=True, strategy=strategy, redacted=secure,
                                  audit=audit)
        self.history.append(res)
        return res

    def _inject_raw_terminal(self, text: str, audit: list) -> None:
        plat = self._platform()
        inj = self.injector
        if plat == "Windows":
            # Consoles need VK codes, not Unicode: type_console presses keys.
            inj.type_console(text)
            audit.append(("primary", "type_console/vk"))
        else:
            inj.type_raw(text)
            audit.append(("primary", "type_raw/keycodes"))

    def _inject_rich(self, text: str, audit: list) -> None:
        inj = self.injector
        plat = self._platform()
        if plat == "Windows" and hasattr(inj, "paste_rich"):
            if inj.paste_rich(to_rtf(text), text):
                audit.append(("primary", "paste_rich/cf_rtf"))
                return
            raise RuntimeError(inj.last_error or "rich paste failed")
        # macOS/Linux rich editors accept styled clipboard HTML/plain equally
        # well; paste plain via clipboard keeps formatting-neutral text.
        if inj.paste_text(text):
            audit.append(("primary", "paste_text/rich"))
            return
        inj.type_text(text)
        audit.append(("primary", "type_text/rich-fallback"))

    @staticmethod
    def _scrub_secret(inj, secret: str) -> None:
        try:
            from voicetype.injector import BaseInjector
            inj.typed_log[:] = [e if e != secret else BaseInjector.REDACTED
                                for e in inj.typed_log]
            scrubbed = []
            for e in inj.events_log:
                if (isinstance(e, tuple) and len(e) == 2 and e[0] in
                        ("text", "raw", "commit", "preedit")
                        and isinstance(e[1], str) and secret and e[1] in secret):
                    continue  # drop fragments of the secret
                scrubbed.append(e)
            inj.events_log[:] = scrubbed
        except Exception:
            pass

    def _warn(self, message: str) -> None:
        try:
            if self.warn_callback is not None:
                self.warn_callback(message)
        except Exception:
            pass
