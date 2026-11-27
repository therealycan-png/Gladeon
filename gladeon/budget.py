"""Context budget (8.7) and transcript/answer truncation helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

BUDGET = {
    "quick": {"system": 250, "buffer": 400, "chunks": 1000, "question": 150, "max_tokens": 160, "margin": 128, "ctx": 3072},
    "deep": {"system": 250, "buffer": 800, "chunks": 2200, "question": 150, "max_tokens": 450, "margin": 256, "ctx": 6144},
}
_SENT_END = re.compile(r"[.!?](?:[\"')\]]*)(?=\s|$)")


def truncate_transcript(text: str, count: Callable[[str], int], limit: int = 150) -> tuple[str, bool]:
    """Cut at the last sentence end within the limit, else at exactly `limit` tokens (8.7)."""
    if count(text) <= limit:
        return text, False
    words = text.split()
    lo, hi = 0, len(words)
    while lo < hi:                    # longest word prefix within the limit
        mid = (lo + hi + 1) // 2
        if count(" ".join(words[:mid])) <= limit:
            lo = mid
        else:
            hi = mid - 1
    cut = " ".join(words[:lo])
    ends = list(_SENT_END.finditer(cut))
    if ends:
        return cut[:ends[-1].end()], True
    return cut, True


def last_sentences(text: str) -> str:
    """Text up to the last complete sentence, or '' if none (12.2 mid-stream deadline)."""
    ends = list(_SENT_END.finditer(text))
    return text[:ends[-1].end()].strip() if ends else ""


@dataclass
class Fitted:
    turns: list
    chunks: list
    dropped_turns: int
    dropped_chunks: int


def fit(mode: str, system_tokens: int, turns: Sequence[tuple[str, str]], chunks: Sequence,
        chunk_tokens: Callable[[object], int], turn_tokens: Callable[[tuple[str, str]], int],
        question_tokens: int, chunk_budget: int | None = None, max_tokens: int | None = None) -> Fitted:
    """Drop the oldest buffer turn first, then the lowest-ranked chunk, until the table fits."""
    b = BUDGET[mode]
    cb = min(chunk_budget or b["chunks"], b["chunks"])
    mt = max_tokens or b["max_tokens"]
    turns, chunks = list(turns), list(chunks)
    dt = dc = 0
    while turns and sum(map(turn_tokens, turns)) > b["buffer"]:
        turns.pop(0)
        dt += 1
    while chunks and sum(map(chunk_tokens, chunks)) > cb:
        chunks.pop()
        dc += 1

    def total():
        return (system_tokens + sum(map(turn_tokens, turns)) + sum(map(chunk_tokens, chunks))
                + question_tokens + mt + b["margin"])
    while total() > b["ctx"] and turns:
        turns.pop(0)
        dt += 1
    while total() > b["ctx"] and chunks:
        chunks.pop()
        dc += 1
    return Fitted(turns, chunks, dt, dc)
