"""Notes watcher, spec 13.3. Polls; never follows symlinks; one writer (the Brain)."""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import stat as _stat
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .chunker import chunk_note
from .index import Brain

POLL_S = 30
STABLE_S = 30
MISSING_GAP_S = 10
MAX_BYTES = 2 * 1024 * 1024
TMP_FRESH_S = 120
RESCAN_BOUND_S = 2.0
MANILA = _dt.timezone(_dt.timedelta(hours=8))
CALIBRATION = "_gladeon/calibration.md"


def manila_date(epoch_s: float) -> str:
    return _dt.datetime.fromtimestamp(epoch_s, MANILA).date().isoformat()


def skippable_name(name: str) -> bool:
    return name.startswith(".") or ".sync-conflict-" in name or not name.lower().endswith(".md")


def syncthing_target(name: str) -> Optional[str]:
    """`.syncthing.<target>.tmp` -> target, else None."""
    if name.startswith(".syncthing.") and name.endswith(".tmp"):
        return name[len(".syncthing."):-len(".tmp")]
    return None


@dataclass
class Seen:
    size: int
    mtime_ns: int
    changed_at: float
    skip: bool = False          # skipped by size / zero-byte / decode
    done: bool = False          # chunked at this (size, mtime_ns)


@dataclass
class ScanStats:
    indexed: int = 0
    touched: int = 0
    purged: int = 0
    skipped: int = 0


class Watcher:
    def __init__(self, root: str, brain: Brain, cap: Callable[[], str],
                 clock_trusted: Callable[[], bool] = lambda: True,
                 count_tokens=None, embed_seq: int = 512,
                 now: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self.root = root
        self.brain = brain
        self.cap = cap
        self.clock_trusted = clock_trusted
        self.count_tokens = count_tokens
        self.embed_seq = embed_seq
        self.now = now
        self.wall = wall
        self.seen: dict[str, Seen] = {}
        self.fresh_tmp: dict[str, float] = {}
        self.missing_since: dict[str, float] = {}
        self.first_scan_done = False
        self.calibration_stat: Optional[tuple[int, int]] = None
        self.calibration_changed_at: Optional[float] = None
        self.on_calibration: Callable[[], None] = lambda: None

    # ---- stat walk -------------------------------------------------------------------------
    def _walk(self, deadline: Optional[float] = None):
        """Yield (relpath, st) for non-dot .md-candidate files and fresh syncthing temp files."""
        stack = [""]
        while stack:
            if deadline is not None and self.now() > deadline:
                raise TimeoutError
            rel = stack.pop()
            try:
                it = os.scandir(os.path.join(self.root, rel) if rel else self.root)
            except OSError:
                continue
            with it:
                for e in it:
                    r = f"{rel}/{e.name}" if rel else e.name
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if e.name.startswith(".") or (not rel and e.name == "_gladeon"):
                                continue
                            stack.append(r)
                            continue
                        if not e.is_file(follow_symlinks=False):
                            continue
                        yield r, e.name, e.stat(follow_symlinks=False)
                    except OSError:
                        continue

    def _cap(self) -> str:
        return self.cap()

    # ---- one poll --------------------------------------------------------------------------
    def scan(self) -> ScanStats:
        st_ = ScanStats()
        now = self.now()
        present: set[str] = set()
        self.fresh_tmp = {}
        for rel, name, st in self._walk():
            tgt = syncthing_target(name)
            if tgt is not None:
                if tgt.lower().endswith(".md") and self.wall() - st.st_mtime < TMP_FRESH_S:
                    self.fresh_tmp[rel] = st.st_mtime
                continue
            if skippable_name(name):
                continue
            present.add(rel)
            self.missing_since.pop(rel, None)
            key = (st.st_size, st.st_mtime_ns)
            prev = self.seen.get(rel)
            if prev is None or (prev.size, prev.mtime_ns) != key:
                self.seen[rel] = prev = Seen(st.st_size, st.st_mtime_ns, now)
            if prev.done or prev.skip:
                continue
            if st.st_size == 0 or st.st_size > MAX_BYTES:
                self._skip(rel, prev, st_)
                continue
            # stable = unchanged for 30 s, by our own observation or by the file's mtime age
            if now - prev.changed_at < STABLE_S and self.wall() - st.st_mtime < STABLE_S:
                continue
            self._ingest(rel, prev, st, st_)
        self._calibration(now)
        for rel in list(self.seen):
            if rel in present:
                continue
            first = self.missing_since.setdefault(rel, now)
            if now - first >= MISSING_GAP_S:
                del self.seen[rel]
                del self.missing_since[rel]
                if self.brain.purge_note(rel):
                    st_.purged += 1
        for rel in self.brain.relpaths() - set(self.seen):
            if rel not in self.missing_since:   # indexed before this process started; purge after the gap
                self.missing_since[rel] = now
        self.first_scan_done = True
        return st_

    def _skip(self, rel: str, s: Seen, st_: ScanStats) -> None:
        s.skip = True
        st_.skipped += 1
        if self.brain.purge_note(rel):
            st_.purged += 1

    def _ingest(self, rel: str, s: Seen, st: os.stat_result, st_: ScanStats) -> None:
        path = os.path.join(self.root, rel)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return
        try:
            fst = os.fstat(fd)
            if not _stat.S_ISREG(fst.st_mode) or (fst.st_size, fst.st_mtime_ns) != (s.size, s.mtime_ns):
                return  # changed under us; next poll restarts the stability timer
            with os.fdopen(fd, "rb", closefd=False) as f:
                raw = f.read(MAX_BYTES + 1)
        finally:
            os.close(fd)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            self._skip(rel, s, st_)
            return
        h = hashlib.sha256(raw).hexdigest()
        mdate = manila_date(st.st_mtime)
        row = self.brain.note_row(rel)
        kw = {"embed_seq": self.embed_seq}
        if self.count_tokens:
            kw["count_tokens"] = self.count_tokens
        if row and not self.clock_trusted() and row[1] == h:
            self.brain.touch_note(rel, s.size, s.mtime_ns)  # 13.4.2: no re-evaluation while untrusted
            s.done = True
            st_.touched += 1
            return
        meta, chunks = chunk_note(rel, text, mdate, self._cap(), **kw)
        if row and row[1] == h:
            cur = self.brain.db.execute("SELECT rank_date FROM notes WHERE id=?", (row[0],)).fetchone()[0]
            if cur == meta.rank_date:
                self.brain.touch_note(rel, s.size, s.mtime_ns)
                s.done = True
                st_.touched += 1
                return
        self.brain.upsert_note(rel, meta, chunks, h, s.size, s.mtime_ns, int(self.wall()))
        s.done = True
        st_.indexed += 1

    def reevaluate_all(self) -> None:
        """Once the clock becomes trusted, every note is re-evaluated once (13.4.2)."""
        for s in self.seen.values():
            if not s.skip:
                s.done = False
                s.changed_at = -1e18

    def _calibration(self, now: float) -> None:
        try:
            st = os.stat(os.path.join(self.root, CALIBRATION), follow_symlinks=False)
            key = (st.st_size, st.st_mtime_ns)
        except OSError:
            key = None
        if key != self.calibration_stat:
            self.calibration_stat = key
            self.calibration_changed_at = now
        elif self.calibration_changed_at is not None and now - self.calibration_changed_at >= STABLE_S:
            self.calibration_changed_at = None
            if key is not None:
                self.on_calibration()

    # ---- state for 13.6.8 ------------------------------------------------------------------
    def unstable_files(self) -> int:
        return sum(1 for s in self.seen.values() if not s.done and not s.skip) + len(self.fresh_tmp)

    def rescan_changed(self) -> bool:
        """13.3.7: stat-only rescan before a 'not in notes' reply. True = answer 'still reading'."""
        deadline = self.now() + RESCAN_BOUND_S
        try:
            for rel, name, st in self._walk(deadline):
                tgt = syncthing_target(name)
                if tgt is not None:
                    if tgt.lower().endswith(".md") and self.wall() - st.st_mtime < TMP_FRESH_S:
                        return True
                    continue
                if skippable_name(name):
                    continue
                s = self.seen.get(rel)
                if s is None or (s.size, s.mtime_ns) != (st.st_size, st.st_mtime_ns):
                    if st.st_size == 0 or st.st_size > MAX_BYTES:
                        continue
                    return True
        except TimeoutError:
            return True
        return False
