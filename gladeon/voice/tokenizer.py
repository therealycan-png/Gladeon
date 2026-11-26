"""Router tokenizer, spec 12.0."""
from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[\w'-]+")
_SUFFIXES = ("'ve", "'re", "'ll", "'s", "'d", "'m")
_IRREGULAR_NT = {"ca": "can", "wo": "will", "sha": "shall"}
# Contraction pieces mapped to the auxiliary they stand for (12.0.2).
_AUX_OF = {"'ve": "have", "'d": "had", "'ll": "will"}
# Pieces that may stand between a marker and a verb without breaking adjacency (12.0.2).
TRANSPARENT_CONTRACTIONS = frozenset({"'m", "'re", "'s", "n't"})

SUBJ = frozenset({"i", "ko", "ako", "kong", "akong", "namin", "naming", "natin", "nating", "ko'y", "we"})
POSS = frozenset({"my", "mine", "ko", "kong", "akin", "aking", "namin", "naming", "natin", "nating", "our"})
ENCLITICS = frozenset({"na", "ba", "pa", "po", "ho", "din", "rin", "lang", "nga", "naman"})


@dataclass(frozen=True)
class Token:
    raw: str      # casefolded surface form
    key: str      # comparison key: hyphens removed, contraction pieces mapped to auxiliaries

    @property
    def is_pronoun_i(self) -> bool:
        # 12.0.4: "i-<letters>" is never the pronoun i.
        return self.raw == "i"


def _split(word: str):
    if word.endswith("n't") and len(word) > 3:
        stem = word[:-3]
        return [_IRREGULAR_NT.get(stem, stem), "n't"]
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) > len(suf):
            return [word[: -len(suf)], suf]
    return [word]


def _key(piece: str) -> str:
    if piece in _AUX_OF:
        return _AUX_OF[piece]
    return piece.replace("-", "")


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    for word in _TOKEN_RE.findall(text.casefold()):
        word = word.strip("-")
        if not word or word == "'":
            continue
        for piece in _split(word):
            if piece:
                out.append(Token(piece, _key(piece)))
    return out


def keys(tokens: list[Token]) -> list[str]:
    return [t.key for t in tokens]


def find_phrase(ks: list[str], phrase: str) -> list[int]:
    """Start indexes where the (space-separated, hyphen-free) phrase occurs as consecutive tokens."""
    parts = [p.replace("-", "") for p in phrase.split()]
    n = len(parts)
    return [i for i in range(len(ks) - n + 1) if ks[i : i + n] == parts]
