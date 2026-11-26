"""Deterministic router, spec 12.2 and 12.3.

`classify()` covers the parts of 12.2 that need no retrieval (steps 1-3 and the sticky test);
`route()` adds the vault state and the hybrid-search callbacks (steps 3-6).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .tokenizer import (ENCLITICS, POSS, SUBJ, TRANSPARENT_CONTRACTIONS, Token, find_phrase,
                        keys, tokenize)

# ---- routes and decision kinds -------------------------------------------------------------
LOCAL_COMMAND = "LOCAL_COMMAND"
LOCK_HINT = "LOCK_HINT"
FINANCE_DEFERRED = "FINANCE_DEFERRED"
PERSONAL = "PERSONAL"          # personal reference (12.2.3)
CANDIDATE = "CANDIDATE"        # implicit retrieval candidate, else GENERAL
BRAIN_RAG = "BRAIN_RAG"
GENERAL_CHAT = "GENERAL_CHAT"
VAULT_LOCKED = "VAULT_LOCKED"
NOT_IN_NOTES = "NOT_IN_NOTES"
STILL_READING = "STILL_READING"

# ---- 12.2.1 local commands -----------------------------------------------------------------
_FILLERS = {"please", "pls", "paki", "po", "ho", "now", "na", "gladeon"}
_COMMANDS = {
    "lock": ["lock", "lock the vault", "lock vault", "lock ang vault", "lock mo ang vault"],
    "status": ["status", "system status", "are you ready"],
    "time": ["what time is it", "what's the time", "anong oras"],
}


def _command_form(text: str) -> str:
    out = []
    for t in tokenize(text):
        k = t.raw
        if k in ("paki-lock", "i-lock"):
            k = "lock"
        if k == "yung":
            k = "ang"
        if k in _FILLERS:
            continue
        out.append(k)
    return " ".join(out)


_COMMAND_FORMS = {_command_form(p): name for name, pats in _COMMANDS.items() for p in pats}


def local_command(text: str) -> Optional[str]:
    return _COMMAND_FORMS.get(_command_form(text))


def _lock_hint(text: str) -> bool:
    pieces = set(re.split(r"[\W_]+", text.casefold()))
    return "lock" in pieces and "vault" in pieces


# ---- shared helpers ------------------------------------------------------------------------
_AUX = {"did", "do", "does", "have", "has", "had", "will", "would", "can", "could", "should",
        "am", "is", "are", "was", "were", "shall"}
_NEVER_BEFORE = {"do", "does", "should", "can", "could", "would", "to"}
_BASE_OK_AUX = {"did", "will", "have", "had"}  # 've/'ll/'d map to have/will/had


def _skip(ks: list[str], i: int, step: int, also: set[str] = frozenset()) -> int:
    """Index of the next token from i in direction step, skipping transparent pieces, enclitics, `also`."""
    j = i + step
    while 0 <= j < len(ks) and (ks[j] in TRANSPARENT_CONTRACTIONS or ks[j] in ENCLITICS or ks[j] in also):
        j += step
    return j


def _is_subj(tokens: list[Token], j: int) -> bool:
    if not (0 <= j < len(tokens)):
        return False
    t = tokens[j]
    if t.key == "i":
        return t.is_pronoun_i
    return t.key in SUBJ


def _is_poss(tokens: list[Token], j: int) -> bool:
    return 0 <= j < len(tokens) and tokens[j].key in POSS


def _gap_ok(a: int, b: int, n: int) -> bool:
    return abs(a - b) - 1 <= n


def _positions(ks: list[str], words) -> list[tuple[int, int]]:
    """(start, end) spans of any word/phrase from `words` in ks."""
    spans = []
    for w in words:
        for s in find_phrase(ks, w):
            spans.append((s, s + len(w.split()) - 1))
    return spans


# ---- 12.2.2 finance guard -----------------------------------------------------------------
MONEY_NOUNS = ["money", "balance", "savings", "salary", "debt", "debts", "budget", "bill", "bills",
               "loan", "loans", "income", "expenses", "credit card", "account balance", "pera",
               "sweldo", "sahod", "ipon", "naipon", "utang", "gastos", "bayarin", "kita", "rent",
               "upa", "renta", "tuition", "matrikula"]
WALLETS = ["gcash", "maya", "bdo", "bpi", "unionbank", "rcbc", "chinabank", "pnb", "bank", "wallet",
           "account"]
_PAST = {"spent", "paid", "owed", "borrowed", "lent", "earned", "nagastos", "gumastos", "ginastos",
         "binayad", "binayaran", "nagbayad", "inutang", "naipon", "pinahiram", "ipinahiram", "nagpahiram"}
_BASE = {"spend", "pay", "owe", "borrow", "lend", "earn"}
_HOWMUCH_EXTRA = {"save", "saved", "make", "made"}
_PREDICATES = {"bayad", "nakabayad", "nabayaran", "babayaran"}
_A_QUALIFIERS = {"have", "has", "may", "meron", "mayroon", "wala", "enough", "over", "am"}
_F_VERBS = {"owe", "owes", "owed", "pay", "pays", "paid", "lend", "lent", "utang", "bayad",
            "binayaran", "nagbayad", "nagpahiram", "pinahiram", "ipinahiram"}
_CHANNEL_NEXT = {"on", "with", "through", "via", "sa", "gamit"}


def _has_how_much(ks: list[str]) -> bool:
    return bool(find_phrase(ks, "how much")) or "magkano" in ks


def _subject_of(tokens, ks, j, also=frozenset()) -> Optional[int]:
    """Index of a SUBJ marker acting as subject of the verb at j (left, then right), else None."""
    left = _skip(ks, j, -1, also)
    if _is_subj(tokens, left):
        return left
    right = _skip(ks, j, +1, also)
    if _is_subj(tokens, right):
        return right
    return None


def _payment_channel_howto(ks: list[str]) -> bool:
    starts = ks[:2] in (["how", "do"], ["how", "can"], ["how", "to"]) or ks[:1] == ["paano"]
    if not starts:
        return False
    for s in find_phrase(ks, "get paid"):
        if s + 2 < len(ks) and ks[s + 2] in _CHANNEL_NEXT:
            return True
    return False


def finance_guard(text: str) -> bool:
    tokens = tokenize(text)
    ks = keys(tokens)
    hm = _has_how_much(ks)
    nouns = _positions(ks, MONEY_NOUNS)
    markers_s = [i for i in range(len(tokens)) if _is_subj(tokens, i)]
    markers_p = [i for i in range(len(tokens)) if _is_poss(tokens, i)]

    def near(markers, n):
        return any(_gap_ok(m, s, n) or _gap_ok(m, e, n) for m in markers for s, e in nouns)

    # (a)
    if near(markers_p, 3):
        return True
    if near(markers_s, 3) and any(k in _A_QUALIFIERS for k in ks):
        return True
    # (b)
    for j, k in enumerate(ks):
        if k in _PAST or (hm and k in _HOWMUCH_EXTRA and k.endswith(("ed", "de"))):
            also = _AUX | ({"get", "got", "gets"} if k == "paid" else set())
            if _subject_of(tokens, ks, j, also) is not None:
                if k == "paid" and _payment_channel_howto(ks):
                    continue
                return True
        if k in _BASE or (hm and k in _HOWMUCH_EXTRA):
            m = _skip(ks, j, -1, {"get", "got", "gets"} | _BASE_OK_AUX)
            if not _is_subj(tokens, m):
                continue
            before = _skip(ks, m, -1)
            before_k = ks[before] if 0 <= before < len(ks) else ""
            between = set(ks[m + 1:j])
            if hm or before_k in _BASE_OK_AUX or (between & _BASE_OK_AUX):
                return True
            if k == "owe" and before_k == "do":
                return True
            # otherwise (e.g. after do/does/should/can/could/would/to) it never counts
        if k == "afford":
            if _subject_of(tokens, ks, j, _AUX) is not None:
                return True
        if k in _PREDICATES:
            if _subject_of(tokens, ks, j, _AUX) is not None:
                return True
    # (c)
    if "magkano" in ks and (markers_p or markers_s) and (nouns or any(k in _PAST | _BASE for k in ks)):
        return True
    # (e)
    if "₱" in text and (markers_p or markers_s):
        return True
    # (f)
    for j, k in enumerate(ks):
        if k not in _F_VERBS:
            continue
        p, count = j + 1, 0
        while p < len(ks) and count <= 4:
            if ks[p] in ENCLITICS or ks[p] in TRANSPARENT_CONTRACTIONS:
                p += 1
                continue
            if ks[p] in ("si", "ni") and p + 1 < len(ks):
                p += 2          # a "si/ni <name>" subject is allowed in between
                continue
            if ks[p] in ("me", "us") and count <= 2:
                return True
            if ks[p] == "sa" and p + 1 < len(ks) and ks[p + 1] in ("akin", "amin", "atin"):
                return True
            count += 1
            p += 1
    # (d)
    wallets = _positions(ks, WALLETS)
    if any(k in ("how", "magkano", "natira", "left", "laman") for k in ks):
        trig = hm or any(k in ("natira", "left", "laman") for k in ks)
        if trig and (markers_p or markers_s) and (nouns or wallets):
            return True
    return False


def money_without_marker(text: str) -> bool:
    """12.2 step 5 (R13: C13-3): amount word plus money noun or wallet, no marker needed."""
    ks = keys(tokenize(text))
    amount = _has_how_much(ks) or any(k in ("natira", "laman", "left") for k in ks)
    return amount and bool(_positions(ks, MONEY_NOUNS) or _positions(ks, WALLETS))


# ---- 12.2.3 personal reference -------------------------------------------------------------
_RECALL_PAST = {"wrote", "written", "noted", "saved", "stored", "listed", "kept", "mentioned", "recorded",
                "sinulat", "isinulat", "nilagay", "inilagay", "nilista", "inilista", "sinave", "nasave",
                "tinala", "binanggit"}
_RECALL_BASE = {"write", "note", "save", "store", "list", "keep", "mention", "record", "put"}
_RECALL_AUX = {"did", "have", "had"}
_SECRETS = ["password", "passcode", "pin", "wifi password", "account number", "plate number"]
_TAG_POSS = {"ko", "kong", "namin", "naming", "natin", "nating"}
_NOTE_PHRASES = ["my notes", "in my notes", "notes ko", "sa notes ko", "check my notes"]


@dataclass
class PersonalMatch:
    a: bool = False
    b: bool = False
    c: bool = False

    @property
    def any(self) -> bool:
        return self.a or self.b or self.c


def personal_reference(text: str) -> PersonalMatch:
    tokens = tokenize(text)
    ks = keys(tokens)
    pm = PersonalMatch()
    for j, k in enumerate(ks):
        if k in _RECALL_PAST and _subject_of(tokens, ks, j, _RECALL_AUX) is not None:
            pm.a = True
        if k in _RECALL_BASE:
            m = _skip(ks, j, -1, _RECALL_AUX)
            if _is_subj(tokens, m):
                before = _skip(ks, m, -1)
                between = ks[m + 1:j]
                ok_aux = (0 <= before and ks[before] in _RECALL_AUX) or any(x in _RECALL_AUX for x in between)
                if ok_aux:
                    pm.a = True
    # (b): secret noun; prefer the two-word forms so "wifi" alone never counts
    for s, e in _positions(ks, _SECRETS):
        if any(_is_poss(tokens, i) for i in range(max(0, s - 3), s) if _gap_ok(i, s, 2)):
            pm.b = True
        j = e + 1
        steps = 0
        while j < len(ks) and steps <= 2:
            if ks[j] in ENCLITICS:
                j += 1
                continue
            if ks[j] in _TAG_POSS:
                pm.b = True
                break
            j += 1
            steps += 1
    # (c)
    if _positions(ks, _NOTE_PHRASES):
        pm.c = True
    return pm


def _starts_with_howwhy(text: str) -> bool:
    ks = keys(tokenize(text))
    return bool(ks) and ks[0] in ("how", "why", "paano", "bakit")


# ---- 12.3 follow-up stickiness --------------------------------------------------------------
_STICKY_OPENERS = ["what about", "how about", "eh yung", "e sa", "paano yung", "pano naman", "e kung"]


def is_sticky(text: str) -> bool:
    ks = keys(tokenize(text))
    if len(ks) > 8:
        return False
    return any(find_phrase(ks[: len(p.split())], p) == [0] for p in _STICKY_OPENERS)


@dataclass
class FollowUp:
    route: Optional[str] = None
    question: str = ""
    chunk_ids: list = field(default_factory=list)
    fallback_eligible: bool = False
    turns: list = field(default_factory=list)   # [(user, assistant), ...] max 2
    last_reply: float = 0.0

    def active(self, now: float) -> bool:
        return self.route is not None and now - self.last_reply <= 90.0

    def wipe(self):
        self.__init__()


# ---- classification (no retrieval) ---------------------------------------------------------
@dataclass
class Classification:
    kind: str
    command: Optional[str] = None
    personal: Optional[PersonalMatch] = None
    implicit_from_b: bool = False   # 12.2.3 fallback rule
    sticky: bool = False


def classify(text: str, followup: Optional[FollowUp] = None, now: Optional[float] = None) -> Classification:
    now = time.monotonic() if now is None else now
    cmd = local_command(text)
    if cmd:
        return Classification(LOCAL_COMMAND, command=cmd)
    if _lock_hint(text):
        return Classification(LOCK_HINT)
    if finance_guard(text):
        return Classification(FINANCE_DEFERRED)
    pm = personal_reference(text)
    if pm.any:
        if pm.b and not (pm.a or pm.c) and _starts_with_howwhy(text):
            return Classification(CANDIDATE, personal=pm, implicit_from_b=True)
        return Classification(PERSONAL, personal=pm)
    if followup is not None and followup.active(now) and is_sticky(text):
        return Classification(CANDIDATE, sticky=True)
    return Classification(CANDIDATE)


# ---- full routing with retrieval callbacks -------------------------------------------------
@dataclass
class RouteResult:
    route: str
    fallback_eligible: bool = False
    hits: list = field(default_factory=list)
    search_query: str = ""
    command: Optional[str] = None


SearchFn = Callable[[str, str], list]   # (query, floor_name) -> hits


def route(text: str, search_text: str, vault_ready: bool, search: SearchFn,
          partial_transient: Callable[[], bool], followup: Optional[FollowUp] = None,
          now: Optional[float] = None) -> RouteResult:
    """Steps 1-6 of 12.2. `partial_transient()` must include the 13.3.7 rescan."""
    now = time.monotonic() if now is None else now
    c = classify(text, followup, now)
    if c.kind in (LOCAL_COMMAND, LOCK_HINT, FINANCE_DEFERRED):
        return RouteResult(c.kind, command=c.command)
    if c.kind == PERSONAL:
        if not vault_ready:
            return RouteResult(VAULT_LOCKED)
        hits = search(search_text, "cos_floor")
        if hits:
            return RouteResult(BRAIN_RAG, fallback_eligible=False, hits=hits, search_query=search_text)
        return RouteResult(STILL_READING if partial_transient() else NOT_IN_NOTES)
    # step 4: stickiness
    if c.sticky and followup is not None:
        prev = followup.route
        if prev == FINANCE_DEFERRED:
            return RouteResult(FINANCE_DEFERRED)
        if prev == BRAIN_RAG and vault_ready:
            q = followup.question + " " + search_text
            hits = search(q, "cos_floor")
            merged = list(dict.fromkeys(list(followup.chunk_ids) + list(hits)))
            if money_without_marker(text) and merged:
                return RouteResult(FINANCE_DEFERRED)
            if merged:
                return RouteResult(BRAIN_RAG, fallback_eligible=followup.fallback_eligible,
                                   hits=merged, search_query=q)
            return RouteResult(STILL_READING if partial_transient() else NOT_IN_NOTES)
        if prev == GENERAL_CHAT:
            return RouteResult(GENERAL_CHAT)
    # step 5: implicit retrieval
    if vault_ready:
        hits = search(search_text, "cos_floor_strict")
        if hits:
            if money_without_marker(text):
                return RouteResult(FINANCE_DEFERRED)
            return RouteResult(BRAIN_RAG, fallback_eligible=True, hits=hits, search_query=search_text)
    return RouteResult(GENERAL_CHAT)
