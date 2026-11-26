"""Transcript normalization, spec 12.1 (exact order)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                         "“": '"', "”": '"', "„": '"', "‟": '"'})
_FILLER = r"(?:hey|okay|ok|uy|hi)"
_ALIAS = r"(?:gladeon|gladion|glad\s+eon|glad\s+ian|glade\s+on|gladiator)"
_GLADIATOR_OK_NEXT = {"who", "what", "when", "where", "why", "how", "tell", "give", "set", "lock",
                      "ano", "sino", "paano", "saan", "kailan"}
_WAKE_RE = re.compile(rf"^\s*(?:{_FILLER}[\s,!.]+)?(?P<alias>{_ALIAS})\b(?P<punct>[\s,.!?:]*)", re.I)
_ECHO_RE = re.compile(r"[\s,.!?:]*\b(?:gladeon|gladion)[\s,.!?:]*$", re.I)
_MODE_RE = re.compile(r"^\s*(?:think\s+harder|deep\s+mode|think\s+carefully|isipin\s+mo\s+nang\s+mabuti|pag-isipan\s+mo)\b[\s,.!?:]*", re.I)
_ENTITY_RES = [
    (re.compile(r"\b(?:g\s+c\s+a\s+s\s+h|gee\s+cash|g[\s-]cash)\b", re.I), "GCash"),
    (re.compile(r"\bpaymaya\b", re.I), "Maya"),
]
STT_ARTIFACTS: tuple[str, ...] = ()  # 12.1.3: empty by default


@dataclass
class Normalized:
    text: str          # normalized transcript sent onward
    search: str        # diacritics-folded copy, retrieval only (12.1.7)
    mode: str          # "quick" | "deep"
    empty: bool        # True -> local "Yes?" (12.1.5)


def hygiene(text: str) -> str:
    """12.1.1 (also used as 15.2 step 1 minus quote/space folding)."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(
        ch for ch in text
        if ch in "\n\t" or unicodedata.category(ch) not in ("Cf", "Cc")
    )
    text = text.translate(_QUOTES).replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _strip_wake(text: str) -> str:
    m = _WAKE_RE.match(text)
    if not m:
        return text
    alias = re.sub(r"\s+", " ", m.group("alias").casefold())
    rest = text[m.end():]
    if alias == "gladiator":
        punct = m.group("punct")
        nxt = re.match(r"\w+", rest)
        ok = (not rest.strip()) or any(c in punct for c in ",.!?:") or (
            nxt is not None and nxt.group(0).casefold() in _GLADIATOR_OK_NEXT)
        if not ok:
            return text
    return rest


def _strip_echo(text: str) -> str:
    while True:
        new = _ECHO_RE.sub("", text)
        if new == text:
            return text
        text = new


def fold_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn"))


def normalize(transcript: str, body_mode: str = "auto") -> Normalized:
    text = hygiene(transcript)                       # 1
    text = _strip_wake(text)                         # 2
    text = _strip_echo(text)                         # 3
    for art in STT_ARTIFACTS:
        if text.casefold().endswith(art.casefold()):
            text = text[: -len(art)]
    mode = "quick"
    m = _MODE_RE.match(text)                         # 4
    if m:
        mode = "deep"
        text = text[m.end():]
    if body_mode in ("quick", "deep"):
        mode = body_mode
    text = text.strip()
    if not re.search(r"\w", text):                   # 5
        return Normalized("", "", mode, True)
    for rx, repl in _ENTITY_RES:                     # 6
        text = rx.sub(repl, text)
    return Normalized(text, fold_diacritics(text), mode, False)   # 7
