"""brain.db: schema (13.2), single writer, vector cache (13.5) and hybrid search (13.6)."""
from __future__ import annotations

import math
import sqlite3
from array import array
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

from .chunker import Chunk, NoteMeta, frontmatter_json
from .fts import Term, build_terms, fts_query, required_matches

RRF_K = 60
VECTOR_TOP = 20
SQLITE_MIN = (3, 53, 0)

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, relpath TEXT UNIQUE NOT NULL, title TEXT,
  declared_date TEXT, rank_date TEXT NOT NULL,
  date_source TEXT NOT NULL CHECK(date_source IN ('frontmatter','filename','mtime')),
  content_hash TEXT NOT NULL, size INTEGER, mtime_ns INTEGER, frontmatter_json TEXT, indexed_at INTEGER);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, header TEXT NOT NULL, body TEXT NOT NULL, text_hash TEXT NOT NULL,
  token_count INTEGER NOT NULL, UNIQUE(note_id, ord));
CREATE INDEX IF NOT EXISTS chunks_hash ON chunks(text_hash);
CREATE TABLE IF NOT EXISTS vectors(text_hash TEXT NOT NULL, emb_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ok','failed')), emb BLOB, PRIMARY KEY(text_hash, emb_key));
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(header, body, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

PRAGMAS = ("journal_mode=WAL", "synchronous=NORMAL", "busy_timeout=5000", "foreign_keys=ON",
           "temp_store=MEMORY", "mmap_size=0")


def sqlite_ok() -> bool:
    return sqlite3.sqlite_version_info >= SQLITE_MIN


def connect(path: str, readonly: bool = False) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro" if readonly else f"file:{path}"
    db = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
    for p in PRAGMAS:
        if readonly and p.startswith(("journal_mode", "synchronous")):
            continue
        db.execute("PRAGMA " + p)
    if not readonly:
        db.executescript(SCHEMA)
    return db


# ---- vector encoding (13.5.3) ---------------------------------------------------------------
def quantize(vec: Sequence[float]) -> Optional[array]:
    """L2-normalise then int8 = round(x*127); None for NaN or norm <= 1e-6 (a 'failed' input)."""
    if any(math.isnan(x) or math.isinf(x) for x in vec):
        return None
    norm = math.sqrt(math.sumprod(vec, vec))
    if norm <= 1e-6:
        return None
    return array("b", (max(-127, min(127, round(x / norm * 127))) for x in vec))


@dataclass
class Hit:
    chunk_id: int
    note_id: int
    relpath: str
    rank_date: str
    title: str
    header: str
    body: str
    cos: Optional[float]
    bm25_rank: Optional[int]
    score: float


class Brain:
    """Owns the writer connection. All writes go through this object on one thread (13.1)."""

    def __init__(self, path: str, emb_key: str, chunk_ceiling: int = 25_000,
                 embed_ready: Callable[[], bool] = lambda: True):
        self.db = connect(path)
        self.emb_key = emb_key
        self.chunk_ceiling = chunk_ceiling
        self.embed_ready = embed_ready
        self._vec: dict[int, array] = {}
        self._dirty = True
        self.set_meta("active_emb_key", emb_key)

    # ---- meta ------------------------------------------------------------------------------
    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))

    def floor(self, name: str, default: float) -> float:
        v = self.get_meta(f"{name}:{self.emb_key}")
        return float(v) if v is not None else default

    # ---- notes -----------------------------------------------------------------------------
    def note_row(self, relpath: str):
        return self.db.execute("SELECT id, content_hash, size, mtime_ns FROM notes WHERE relpath=?",
                               (relpath,)).fetchone()

    def upsert_note(self, relpath: str, meta: NoteMeta, chunks: Iterable[Chunk], content_hash: str,
                    size: int, mtime_ns: int, now: int) -> int:
        """Replace a note's chunks in one transaction; vectors survive by text_hash (13.3.5)."""
        chunks = list(chunks)
        with _txn(self.db):
            row = self.note_row(relpath)
            vals = (meta.title, meta.declared_date, meta.rank_date, meta.date_source, content_hash, size,
                    mtime_ns, frontmatter_json(meta.frontmatter), now)
            if row:
                note_id = row[0]
                self._delete_chunks(note_id)
                self.db.execute("""UPDATE notes SET title=?, declared_date=?, rank_date=?, date_source=?,
                    content_hash=?, size=?, mtime_ns=?, frontmatter_json=?, indexed_at=? WHERE id=?""",
                                vals + (note_id,))
            else:
                note_id = self.db.execute("""INSERT INTO notes(title, declared_date, rank_date, date_source,
                    content_hash, size, mtime_ns, frontmatter_json, indexed_at, relpath)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""", vals + (relpath,)).lastrowid
            for c in chunks:
                cid = self.db.execute("""INSERT INTO chunks(note_id, ord, header, body, text_hash, token_count)
                    VALUES(?,?,?,?,?,?)""", (note_id, c.ord, c.header, c.body, c.text_hash, c.token_count)).lastrowid
                self.db.execute("INSERT INTO chunks_fts(rowid, header, body) VALUES(?,?,?)", (cid, c.header, c.body))
        self._dirty = True
        return note_id

    def touch_note(self, relpath: str, size: int, mtime_ns: int) -> None:
        """Same hash and path: metadata only (13.3.3). Callers re-chunk if rank_date changes (13.4.2)."""
        self.db.execute("UPDATE notes SET size=?, mtime_ns=? WHERE relpath=?", (size, mtime_ns, relpath))

    def purge_note(self, relpath: str) -> bool:
        with _txn(self.db):
            row = self.note_row(relpath)
            if not row:
                return False
            self._delete_chunks(row[0])
            self.db.execute("DELETE FROM notes WHERE id=?", (row[0],))
        self._dirty = True
        return True

    def _delete_chunks(self, note_id: int) -> None:
        # 13.2.5: FTS rows first, same transaction
        self.db.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE note_id=?)", (note_id,))
        self.db.execute("DELETE FROM chunks WHERE note_id=?", (note_id,))

    def relpaths(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT relpath FROM notes")}

    # ---- vectors ---------------------------------------------------------------------------
    def pending(self, limit: int = 100) -> list[tuple[int, str, str, str]]:
        """(chunk id, text_hash, header, body) with no vectors row under the active key, newest first."""
        return self.db.execute("""SELECT c.id, c.text_hash, c.header, c.body FROM chunks c JOIN notes n ON n.id=c.note_id
            WHERE NOT EXISTS (SELECT 1 FROM vectors v WHERE v.text_hash=c.text_hash AND v.emb_key=?)
            GROUP BY c.text_hash ORDER BY n.rank_date DESC, c.id DESC LIMIT ?""", (self.emb_key, limit)).fetchall()

    def pending_count(self) -> int:
        return self.db.execute("""SELECT count(DISTINCT c.text_hash) FROM chunks c WHERE NOT EXISTS
            (SELECT 1 FROM vectors v WHERE v.text_hash=c.text_hash AND v.emb_key=?)""", (self.emb_key,)).fetchone()[0]

    def failed_count(self) -> int:
        return self.db.execute("""SELECT count(DISTINCT c.text_hash) FROM chunks c JOIN vectors v
            ON v.text_hash=c.text_hash AND v.emb_key=? WHERE v.status='failed'""", (self.emb_key,)).fetchone()[0]

    def store_vector(self, text_hash: str, raw: Optional[Sequence[float]]) -> str:
        """Store a per-input result from a READY engine; returns 'ok' or 'failed' (13.4.8)."""
        q = quantize(raw) if raw is not None else None
        status = "ok" if q is not None else "failed"
        self.db.execute("INSERT OR REPLACE INTO vectors(text_hash, emb_key, status, emb) VALUES(?,?,?,?)",
                        (text_hash, self.emb_key, status, q.tobytes() if q is not None else None))
        self._dirty = True
        return status

    def retry_failed(self) -> int:
        """Daily: drop failed rows so they become pending again."""
        return self.db.execute("DELETE FROM vectors WHERE emb_key=? AND status='failed'", (self.emb_key,)).rowcount

    def gc_vectors(self, key_change_complete: bool) -> int:
        n = self.db.execute("DELETE FROM vectors WHERE text_hash NOT IN (SELECT text_hash FROM chunks)").rowcount
        if key_change_complete:
            n += self.db.execute("DELETE FROM vectors WHERE emb_key<>?", (self.emb_key,)).rowcount
        return n

    def _load_vectors(self) -> None:
        if not self._dirty:
            return
        rows = self.db.execute("""SELECT c.id, v.emb FROM chunks c JOIN notes n ON n.id=c.note_id
            JOIN vectors v ON v.text_hash=c.text_hash AND v.emb_key=? AND v.status='ok'
            ORDER BY n.rank_date DESC, c.id DESC LIMIT ?""", (self.emb_key, self.chunk_ceiling)).fetchall()
        self._vec = {}
        for cid, blob in rows:
            a = array("b")
            a.frombytes(blob)
            self._vec[cid] = a
        self._dirty = False

    def over_ceiling(self) -> bool:
        return self.db.execute("SELECT count(*) FROM chunks").fetchone()[0] > self.chunk_ceiling

    # ---- checks (13.2.7) -------------------------------------------------------------------
    def check(self) -> list[str]:
        errs = []
        nc = self.db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        nf = self.db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
        if nc != nf:
            errs.append(f"COUNT_MISMATCH:{nc}:{nf}")
        if self.db.execute("SELECT count(*) FROM chunks_fts WHERE rowid NOT IN (SELECT id FROM chunks)").fetchone()[0]:
            errs.append("FTS_ORPHAN")
        if self.db.execute("SELECT count(*) FROM chunks WHERE note_id NOT IN (SELECT id FROM notes)").fetchone()[0]:
            errs.append("CHUNK_ORPHAN")
        try:
            self.db.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('integrity-check', 1)")
        except sqlite3.DatabaseError:
            errs.append("FTS_INTEGRITY")
        return errs

    # ---- search (13.6) ---------------------------------------------------------------------
    def search(self, search_text: str, qvec: Optional[Sequence[float]], cos_floor: float) -> list[Hit]:
        """Hybrid search. `qvec` is the raw query embedding, or None when EMBED is not READY."""
        terms = build_terms(search_text)
        vec_ok = qvec is not None and self.embed_ready()
        cos: dict[int, float] = {}
        vec_rank: dict[int, int] = {}
        if vec_ok:
            self._load_vectors()
            q = quantize(qvec)
            if q is not None:
                scored = sorted(((math.sumprod(q, v) / (127 * 127), cid) for cid, v in self._vec.items()),
                                key=lambda t: (-t[0], -t[1]))[:VECTOR_TOP]
                for i, (s, cid) in enumerate(scored):
                    cos[cid] = s
                    vec_rank[cid] = i
        bm_rank: dict[int, int] = {}
        if terms:
            rows = self.db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 200",
                                   (fts_query(terms),)).fetchall()
            bm_rank = {r[0]: i for i, r in enumerate(rows)}
        usable = set(self._vec) if vec_ok else set()
        need = required_matches(len(terms))
        fts_only: set[int] = set()
        if need is not None:
            cands = [cid for cid in bm_rank if cid not in usable]
            if cands:
                counts = dict.fromkeys(cands, 0)
                marks = ",".join("?" * len(cands))
                for t in terms:
                    for (cid,) in self.db.execute(
                            f"SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid IN ({marks})",
                            (t.fts(), *cands)):
                        counts[cid] += 1
                fts_only = {cid for cid, n in counts.items() if n >= need}
        hits = {cid for cid, s in cos.items() if s >= cos_floor} | fts_only
        if not hits:
            return []
        marks = ",".join("?" * len(hits))
        info = {r[0]: r[1:] for r in self.db.execute(
            f"""SELECT c.id, c.note_id, n.relpath, n.rank_date, n.title, c.header, c.body FROM chunks c
                JOIN notes n ON n.id=c.note_id WHERE c.id IN ({marks})""", tuple(hits))}
        out = []
        for cid in hits:
            if cid not in info:
                continue
            score = 0.0
            if cid in vec_rank:
                score += 1 / (RRF_K + vec_rank[cid] + 1)
            if cid in bm_rank:
                score += 1 / (RRF_K + bm_rank[cid] + 1)
            nid, rel, rd, title, header, body = info[cid]
            out.append(Hit(cid, nid, rel, rd, title, header, body, cos.get(cid), bm_rank.get(cid), score))
        out.sort(key=lambda h: (-h.score, _neg_date(h.rank_date), -h.chunk_id))
        return out


def _neg_date(d: str) -> tuple:
    return tuple(-int(x) for x in d.split("-"))


class _txn:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")

    def __exit__(self, et, ev, tb):
        self.db.execute("COMMIT" if et is None else "ROLLBACK")
        return False


# ---- 13.5.6 scan gate -----------------------------------------------------------------------
def scan_gate(dim: int, budget_s: float = 0.150, lo: int = 5000, hi: int = 25000, step: int = 1000,
              clock=None) -> int:
    """Largest N (multiple of `step`, lo..hi) whose full int8 sumprod scan takes <= budget_s.
    Returns 0 if even `lo` is too slow (the caller reports the index as over-ceiling)."""
    import random
    import time as _t
    clock = clock or _t.perf_counter
    rnd = random.Random(7)
    q = array("b", (rnd.randint(-127, 127) for _ in range(dim)))
    vecs = [array("b", (rnd.randint(-127, 127) for _ in range(dim))) for _ in range(hi)]
    best = 0
    for n in range(lo, hi + 1, step):
        t0 = clock()
        sorted(((math.sumprod(q, v), i) for i, v in enumerate(vecs[:n])), reverse=True)[:VECTOR_TOP]
        if clock() - t0 > budget_s:
            break
        best = n
    return best
