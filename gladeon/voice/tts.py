"""tts_text post-processor, spec 12.4 (deterministic, applied to display_text)."""
from __future__ import annotations

import datetime as _dt
import re

DEFAULT_PRONUNCIATION = "Glad-eon"   # chosen at setup from 3 candidates (12.4.5)
MAX_CHARS = 900

_FENCE_RE = re.compile(r"(^|\n)(```|~~~)[^\n]*\n.*?(\n\2[^\n]*(?=\n|$)|$)", re.S)
_HEADING_RE = re.compile(r"^#{1,6} +(.*?)\s*#*\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)]) +")
_TABLE_DELIM_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_STRONG_RE = re.compile(r"(?<![\w*_])(\*\*|__)(?=\S)(.+?)(?<=\S)\1(?![\w*_])")
_EM_STAR_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
_EM_UNDER_RE = re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])")

_AMOUNT = r"(?P<neg2>-)?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<dec>\d+))?(?P<suf>[KMB])?"
_PESO_SIGN_RE = re.compile(r"(?P<neg>-)?₱\s?" + _AMOUNT + r"(?![A-Za-z])")
_PHP_RE = re.compile(r"(?:(?<=^)|(?<=[\s(]))(?P<neg>-)?(?:PHP |Php|P)" + _AMOUNT + r"(?![A-Za-z])")
_DATE_RE = re.compile(r"\b(\d{4})[-/](\d{2})[-/](\d{2})\b")
_SUFFIX_WORD = {"K": "thousand", "M": "million", "B": "billion"}


def _say_amount(m: re.Match) -> str:
    neg = "minus " if (m.group("neg") or m.group("neg2")) else ""
    integer, dec, suf = m.group("int"), m.group("dec"), m.group("suf")
    if suf:
        num = integer + ("." + dec if dec else "")
        return f"{neg}{num} {_SUFFIX_WORD[suf]} pesos"
    if dec is not None and len(dec) == 2:
        pesos = int(integer.replace(",", ""))
        cents = int(dec)
        if pesos == 0:
            return f"{neg}{cents} centavo" + ("" if cents == 1 else "s") if cents else f"{neg}0 pesos"
        out = f"{neg}{integer} peso" + ("" if pesos == 1 else "s")
        if cents:
            out += f" and {cents} centavo" + ("" if cents == 1 else "s")
        return out
    num = integer + ("." + dec if dec else "")
    single = num == "1"
    return f"{neg}{num} peso" + ("" if single else "s")


def _php_qualifies(m: re.Match) -> bool:
    integer, dec, suf = m.group("int"), m.group("dec"), m.group("suf")
    if "," in integer or suf:
        return True
    if dec is not None:
        return len(dec) == 2
    return len(integer) >= 2


def _php_sub(m: re.Match) -> str:
    return _say_amount(m) if _php_qualifies(m) else m.group(0)


def _say_date(m: re.Match) -> str:
    try:
        d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return m.group(0)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _lexicon(pron: str):
    return [
        (re.compile(r"\b(?:Gladeon|Gladion)\b", re.I), pron),
        (re.compile(r"\bGCash\b"), "G-Cash"),
        (re.compile(r"\bBPI\b"), "B P I"),
        (re.compile(r"\bBDO\b"), "B D O"),
        (re.compile(r"\bBGC\b"), "B G C"),
        (re.compile(r"\bSSS\b"), "S S S"),
        (re.compile(r"\bParañaque\b"), "Paranaque"),
    ]


def _line(line: str) -> str | None:
    if _TABLE_DELIM_RE.match(line) and "-" in line and ("|" in line or line.strip().startswith("-")) and "|" in line:
        return None
    h = _HEADING_RE.match(line)
    if h:
        text = h.group(1).strip()
        return text if text.endswith((".", "!", "?")) else text + "."
    if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        return ", ".join(c for c in cells if c)
    return _LIST_RE.sub("", line, count=1)


def to_tts(display_text: str, pronunciation: str = DEFAULT_PRONUNCIATION, max_chars: int = MAX_CHARS) -> str:
    text = _FENCE_RE.sub(lambda m: m.group(1) + "Code omitted.", display_text)
    lines = [_line(l) for l in text.split("\n")]
    text = "\n".join(l for l in lines if l is not None)
    text = _LINK_RE.sub(r"\1", text)
    for _ in range(2):
        text = _STRONG_RE.sub(r"\2", text)
    text = _EM_STAR_RE.sub(r"\1", text)
    text = _EM_UNDER_RE.sub(r"\1", text)
    text = _PESO_SIGN_RE.sub(_say_amount, text)
    text = _PHP_RE.sub(_php_sub, text)
    text = _DATE_RE.sub(_say_date, text)
    for rx, repl in _lexicon(pronunciation):
        text = rx.sub(repl, text)
    if len(text) > max_chars:
        cut = max(text.rfind(p, 0, max_chars) for p in (". ", "! ", "? ", ".\n"))
        text = (text[: cut + 1] if cut > 0 else text[:max_chars]).rstrip() + " The rest is on your screen."
    return text
