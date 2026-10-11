"""Text strategies: plain, raw-terminal, RTF, code-aware, markdown, IME."""
from __future__ import annotations

import re

_MARKDOWN_CHARS = re.compile(r"(\*\*|__|\*|_|`|#{1,6}\s|\[(.*?)\]\(.*?\)|~~)")


def normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def to_plain_text(text: str) -> str:
    """Strip markdown for address bars / password fields (no formatting)."""
    t = normalize_newlines(text or "")

    def _link(m: re.Match) -> str:
        return m.group(1) or ""

    t = re.sub(r"\[(.*?)\]\(.*?\)", _link, t)
    t = re.sub(r"(\*\*|__)(.*?)\1", r"\2", t)
    t = re.sub(r"(?<!\w)(\*|_|`|~~)(?!\s)(.+?)(?<!\s)\1(?!\w)", r"\2", t)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^>\s?", "", t, flags=re.MULTILINE)
    return t


def to_rtf(text: str) -> str:
    """Minimal RTF wrapper for rich editors (Word/Outlook via CF_RTF)."""
    t = normalize_newlines(text or "")
    esc = (t.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
             .replace("\n", "\\par\n"))
    out = []
    bold = italic = code = False
    i = 0
    while i < len(esc):
        if esc.startswith("**", i):
            out.append("\\b0 " if bold else "\\b ")
            bold = not bold
            i += 2
        elif esc.startswith("`", i):
            out.append("\\f1 " if not code else "\\f0 ")
            code = not code
            i += 1
        elif esc.startswith("*", i) and not esc.startswith("\\", max(0, i - 1)):
            out.append("\\i0 " if italic else "\\i ")
            italic = not italic
            i += 1
        else:
            out.append(esc[i])
            i += 1
    if bold:
        out.append("\\b0 ")
    if italic:
        out.append("\\i0 ")
    return "{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Consolas;}{\\f1 Consolas;}}\n" + "".join(out) + "\n}"


def preserve_indentation(text: str, base_indent: str = "") -> str:
    """Re-anchor code for IDEs: keep every line's relative indent, add base.

    The least-indented non-empty line becomes the anchor (base_indent), all
    others keep their offset from it — so pasted code never flattens.
    """
    lines = normalize_newlines(text or "").split("\n")
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        return ""
    min_indent = min(len(ln) - len(ln.lstrip(" ")) for ln in nonempty)
    out = []
    for ln in lines:
        if not ln.strip():
            out.append("")
            continue
        rel = max(0, (len(ln) - len(ln.lstrip(" "))) - min_indent)
        out.append(base_indent + " " * rel + ln.strip())
    return "\n".join(out)


def terminal_chunks(text: str) -> list[tuple[str, str]]:
    """Split terminal input into ("key"|"text", payload) preserving escapes."""
    chunks: list[tuple[str, str]] = []
    buf = ""
    for ch in normalize_newlines(text or ""):
        if ch == "\n":
            if buf:
                chunks.append(("text", buf))
                buf = ""
            chunks.append(("key", "enter"))
        elif ch == "\t":
            if buf:
                chunks.append(("text", buf))
                buf = ""
            chunks.append(("key", "tab"))
        elif ch == "\x1b":
            if buf:
                chunks.append(("text", buf))
                buf = ""
            chunks.append(("key", "escape"))
        else:
            buf += ch
    if buf:
        chunks.append(("text", buf))
    return chunks


def ime_events(text: str, chunk_size: int = 32) -> list[tuple[str, str]]:
    """IME composition event stream: (preedit, chunk) then (commit, chunk)."""
    events: list[tuple[str, str]] = []
    for i in range(0, len(text or ""), max(1, chunk_size)):
        chunk = text[i:i + max(1, chunk_size)]
        events.append(("preedit", chunk))
        events.append(("commit", chunk))
    return events
