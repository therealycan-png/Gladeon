"""Daily index work: snapshots (14.2), vector GC and failed retry (13.2.6, 13.4.8), integrity (13.7),
disk floors (11.6), and the calibration runner (13.6.7)."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .calibrate import floors, parse, resolve_target
from .index import Brain, quantize

DISK_FLOOR_BYTES = 2 * 1024 ** 3
KEEP_SNAPSHOTS = 3
BACKUP_PAGES = 256


def disk_low(path: str, floor: int = DISK_FLOOR_BYTES) -> bool:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize < floor


def data_version(db: sqlite3.Connection) -> str:
    """A cheap change marker: total row counts plus the newest indexed_at."""
    r = db.execute("SELECT (SELECT count(*) FROM chunks), (SELECT count(*) FROM vectors), "
                   "(SELECT coalesce(max(indexed_at),0) FROM notes)").fetchone()
    return f"{r[0]}:{r[1]}:{r[2]}"


def snapshot(brain: Brain, backups_dir: str, clock_trusted: bool, epoch: int,
             heartbeat: Callable[[], None] = lambda: None, floor: int = DISK_FLOOR_BYTES) -> Optional[str]:
    """Online Backup API in 256-page steps -> .tmp -> checks -> rename; keep 3. Returns the path or None."""
    if not clock_trusted:
        return None
    os.makedirs(backups_dir, exist_ok=True)
    if disk_low(backups_dir, floor):
        return None
    ver = data_version(brain.db)
    if brain.get_meta("last_snapshot_version") == ver:
        return None
    final = os.path.join(backups_dir, f"brain-{epoch}.db")
    tmp = final + ".tmp"
    if os.path.exists(tmp):
        os.unlink(tmp)
    dst = sqlite3.connect(tmp)
    try:
        brain.db.backup(dst, pages=BACKUP_PAGES, progress=lambda status, remaining, total: heartbeat())
        ok = dst.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        nc = dst.execute("SELECT count(*) FROM chunks").fetchone()[0]
        nf = dst.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
    finally:
        dst.close()
    if not ok or nc != nf:
        os.unlink(tmp)
        return None
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, final)
    dfd = os.open(backups_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    snaps = sorted(f for f in os.listdir(backups_dir) if f.startswith("brain-") and f.endswith(".db"))
    for old in snaps[:-KEEP_SNAPSHOTS]:
        os.unlink(os.path.join(backups_dir, old))
    brain.set_meta("last_snapshot_version", ver)
    return final


@dataclass
class DailyReport:
    vectors_deleted: int
    failed_retried: int
    integrity_ok: bool


def daily(brain: Brain, key_change_complete: bool, heartbeat: Callable[[], None] = lambda: None) -> DailyReport:
    brain.db.set_progress_handler(lambda: (heartbeat(), 0)[1], 10_000)
    try:
        ok = brain.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        brain.db.set_progress_handler(None, 0)
    return DailyReport(brain.gc_vectors(key_change_complete), brain.retry_failed(), ok and not brain.check())


# ---- 13.6.7 calibration runner --------------------------------------------------------------
@dataclass
class CalibrationResult:
    ok: bool
    positives: int
    negatives: int
    warnings: list
    cos_floor: Optional[float] = None
    cos_floor_strict: Optional[float] = None


def run_calibration(brain: Brain, text: str, embed: Callable[[str], Optional[Sequence[float]]],
                    pending: int, first_scan_done: bool, unstable_files: int) -> CalibrationResult:
    if pending or not first_scan_done or unstable_files:
        return CalibrationResult(False, 0, 0, ["CALIBRATION_WAITING"])
    cs = parse(text)
    notes = brain.db.execute("SELECT relpath, title FROM notes").fetchall()
    warnings: list = []
    brain._load_vectors()
    vec_by_note: dict[str, list] = {}
    for cid, (rel,) in [(r[0], r[1:]) for r in brain.db.execute(
            "SELECT c.id, n.relpath FROM chunks c JOIN notes n ON n.id=c.note_id")]:
        if cid in brain._vec:
            vec_by_note.setdefault(rel, []).append(brain._vec[cid])
    pos_scores: list[float] = []
    for q, target in cs.positives:
        rel = resolve_target(target, notes)
        if rel is None:
            warnings.append("CALIBRATION_TARGET_UNRESOLVED")
            continue
        if rel not in vec_by_note:
            continue                                 # target has no usable vector
        qv = quantize(embed(q) or [])
        if qv is None:
            continue
        import math
        pos_scores.append(max(math.sumprod(qv, v) / 127 ** 2 for v in vec_by_note[rel]))
    neg_best: list[float] = []
    for q in cs.negatives:
        qv = quantize(embed(q) or [])
        if qv is None or not brain._vec:
            continue
        import math
        neg_best.append(max(math.sumprod(qv, v) / 127 ** 2 for v in brain._vec.values()))
    f = floors(pos_scores, neg_best)
    if f is None:
        return CalibrationResult(False, len(pos_scores), len(neg_best), warnings + ["INDEX_UNCALIBRATED"])
    brain.set_meta(f"cos_floor:{brain.emb_key}", f"{f.cos_floor:.6f}")
    brain.set_meta(f"cos_floor_strict:{brain.emb_key}", f"{f.cos_floor_strict:.6f}")
    return CalibrationResult(True, len(pos_scores), len(neg_best), sorted(set(warnings)), f.cos_floor, f.cos_floor_strict)
