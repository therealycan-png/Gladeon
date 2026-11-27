"""FTS5 term builder and FTS-only hit rule, spec 13.6.1 / 13.6.3(b)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..voice.tokenizer import POSS, SUBJ

_STOP = set("""the a an is are was were be of to in on at for and or with about my i me you it this that
ang ng sa mga ko ako na at ay si ni yung ba po
what who where when why how which did do does have has had can could
ano sino saan kailan bakit paano nasaan
write wrote written note noted save saved put store stored list listed keep kept mention mentioned
record recorded sinulat isinulat nilagay inilagay nilista inilista sinave nasave tinala binanggit
our your his her their mo niya nila lang pa nga naman din rin ba kaya
notes note check will shall tungkol para kay""".split())
STOPWORDS = frozenset(_STOP | SUBJ | POSS)
_CONTRACTION_PIECES = frozenset({"s", "ve", "d", "m", "re", "ll", "t"})
COMPOUNDS = {"wifi": "wi fi", "email": "e mail"}
_TOKEN_RE = re.compile(r"[\w'-]+")


@dataclass(frozen=True)
class Term:
    """One content term: a group of alternative FTS5 phrases that counts as one term."""
    alts: tuple[str, ...]

    def fts(self) -> str:
        q = [f'"{a}"' for a in self.alts]
        return q[0] if len(q) == 1 else "(" + " OR ".join(q) + ")"


def _apostrophe_parts(token: str) -> list[str]:
    """unicode61 splits on '; contraction suffixes are dropped only from those apostrophe pieces."""
    parts = token.split("'")
    return [p for i, p in enumerate(parts) if p.strip("-") and not (i > 0 and p in _CONTRACTION_PIECES)]


def build_terms(search_text: str) -> list[Term]:
    terms: list[Term] = []
    seen: set[tuple[str, ...]] = set()
    for tok in _TOKEN_RE.findall(search_text.casefold()):
        if tok.endswith("n't"):
            tok = tok[:-3]
        for part in _apostrophe_parts(tok):
            part = part.strip("-")
            joined = part.replace("-", "")
            if not joined or joined in STOPWORDS:
                continue
            pieces = [x for x in part.split("-") if x]
            alts = (" ".join(pieces), joined) if len(pieces) > 1 else (joined,)
            if joined in COMPOUNDS:
                alts = alts + (COMPOUNDS[joined],)
            alts = tuple(dict.fromkeys(alts))
            if alts not in seen:
                seen.add(alts)
                terms.append(Term(alts))
    return terms


def fts_query(terms: list[Term]) -> str:
    return " OR ".join(t.fts() for t in terms)


def required_matches(n: int) -> int | None:
    """Terms an FTS-only hit must match; None when n < 2 (never an FTS-only hit)."""
    if n < 2:
        return None
    return max(2, math.ceil(0.7 * n))
