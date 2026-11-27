"""Calibration file parsing and floor math (13.6.7); index-partial flags (13.6.8)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

MIN_POS = 10
MIN_NEG = 10
EMBED_GRACE_S = 600


@dataclass
class CalibrationSet:
    positives: list[tuple[str, str]] = field(default_factory=list)   # (question, target)
    negatives: list[str] = field(default_factory=list)


def parse(text: str) -> CalibrationSet:
    cs = CalibrationSet()
    for line in text.splitlines():
        s = line.strip()
        for lead in ("- ", "* ", "+ "):
            if s.startswith(lead):
                s = s[len(lead):].strip()
        if not s or s.startswith("#"):
            continue
        if "=>" in s:
            q, t = s.split("=>", 1)
            if q.strip() and t.strip():
                cs.positives.append((q.strip(), t.strip()))
            continue
        cs.negatives.append(s)
    return cs


def percentile(values: Sequence[float], p: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    xs = sorted(values)
    if not xs:
        raise ValueError("empty")
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


@dataclass
class Floors:
    cos_floor: float
    cos_floor_strict: float


def floors(pos_scores: Sequence[float], neg_best: Sequence[float]) -> Optional[Floors]:
    """pos_scores: each usable positive's score against its target; neg_best: each negative's best score."""
    if len(pos_scores) < MIN_POS or len(neg_best) < MIN_NEG:
        return None
    f = (percentile(pos_scores, 10) + percentile(neg_best, 90)) / 2
    return Floors(f, max(f, percentile(neg_best, 95)))


def resolve_target(target: str, notes: Sequence[tuple[str, str]]) -> Optional[str]:
    """notes: (relpath, title). A relpath or a unique title; otherwise None (CALIBRATION_TARGET_UNRESOLVED)."""
    t = target.strip()
    rels = [r for r, _ in notes if r == t or r == t + ".md"]
    if len(rels) == 1:
        return rels[0]
    titles = [r for r, title in notes if title is not None and title.casefold() == t.casefold()]
    return titles[0] if len(titles) == 1 else None


@dataclass
class PartialState:
    """Inputs for 13.6.8; times are monotonic seconds."""
    embed_ready: bool
    embed_down_since: Optional[float]
    pending: int
    unstable_files: int
    first_scan_done: bool
    embed_failed: int
    over_ceiling: bool
    disk_low: bool


@dataclass
class Partial:
    indexing_allowed: bool
    transient: bool
    permanent: bool
    warnings: list[str]


def partial(s: PartialState, now: float) -> Partial:
    down_for = (now - s.embed_down_since) if (not s.embed_ready and s.embed_down_since is not None) else 0.0
    long_down = not s.embed_ready and down_for >= EMBED_GRACE_S
    short_down = not s.embed_ready and down_for < EMBED_GRACE_S
    allowed = (s.embed_ready or short_down) and not s.disk_low
    work = s.pending > 0 or s.unstable_files > 0 or not s.first_scan_done
    transient = (work and allowed) or short_down
    permanent = s.embed_failed > 0 or s.over_ceiling or long_down or s.disk_low
    if s.disk_low or long_down:          # only these two override the transient clauses (R7: C7-1)
        transient = False
    warnings = []
    if long_down:
        warnings.append("EMBED_FAILED")
    if permanent and not transient:
        warnings.append("INDEX_PARTIAL")
    return Partial(allowed, transient, permanent, warnings)


RESTART_SCHEDULE_S = (300, 900, 3600)


def embed_restart_due(failed_since: float, now: float, done: int) -> bool:
    """13.6.8: restart llm-embed after 5, 15, 60 minutes, then hourly. `done` = restarts already made."""
    elapsed = now - failed_since
    if done < len(RESTART_SCHEDULE_S):
        return elapsed >= RESTART_SCHEDULE_S[done]
    return elapsed >= RESTART_SCHEDULE_S[-1] + 3600 * (done - len(RESTART_SCHEDULE_S) + 1)
