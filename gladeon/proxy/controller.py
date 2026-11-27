"""Controller: owns sub-states, vault lifecycle (9.4/9.5), auto-lock (9.8), indexer (11.7), /status (11.5)."""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..brain.calibrate import PartialState, partial
from ..brain.index import Brain
from ..brain.watcher import Watcher
from ..pipeline import Deps, Pipeline, Profile
from ..privd.client import PrivdClient
from ..voice import replies as R
from .httpcore import SCHEMA
from .lockout import Lockout
from .reqcache import RequestCache
from .state import SubStates, lock_allowed, public_state, query_blocked

log = logging.getLogger("gladeon.proxy")

AUTO_LOCK_S = 7200
INDEX_QUIET_S = 60
BRAIN_DRAIN_S = 15


@dataclass
class Config:
    vault_root: str                         # /srv/gladeon/vault
    state_dir: str                          # /var/lib/gladeon
    run_dir: str                            # /run/gladeon-proxy
    emb_key: str = "embed@0@0@0"
    default_floors: tuple = (0.55, 0.65)
    profiles: dict = field(default_factory=lambda: {"quick": Profile(), "deep": Profile()})
    auto_lock_s: int = AUTO_LOCK_S
    poll_s: float = 30.0


class Controller:
    def __init__(self, cfg: Config, privd: PrivdClient, engines: dict, cache: RequestCache,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self.cfg = cfg
        self.privd = privd
        self.engines = engines              # quick/deep/embed -> Engine
        self.cache = cache
        self.clock, self.wall = clock, wall
        self.s = SubStates(boot="RUNNING")
        self.lockout = Lockout(os.path.join(cfg.state_dir, "unlock_lockout.json"), clock)
        self.warnings: set[str] = set()
        if self.lockout.warning:
            self.warnings.add(self.lockout.warning)
        self.mu = threading.RLock()
        self.brain: Optional[Brain] = None
        self.watcher: Optional[Watcher] = None
        self.stop_bg = threading.Event()
        self.bg: list[threading.Thread] = []
        self.inflight = 0
        self.last_interactive = -1e18
        self.last_activity = clock()
        self.embed_down_since: Optional[float] = None
        self.lock_requested = False
        self.started = clock()
        self.pipeline = Pipeline(Deps(
            engines={"quick": engines.get("quick"), "deep": engines.get("deep")},
            profiles=cfg.profiles,
            engine_ready=lambda n: self.s.engines.get(n) == "READY",
            vault_ready=lambda: self.s.vault == "READY",
            search=self._search,
            partial_transient=self._partial_transient_with_rescan,
            still_reading_n=self._still_reading_n,
            command=self._command,
        ))

    # ---- activity / auto-lock (9.8) --------------------------------------------------------
    def touch(self) -> None:
        self.last_activity = self.clock()
        try:
            os.makedirs(self.cfg.run_dir, exist_ok=True)
            with open(os.path.join(self.cfg.run_dir, "last_activity"), "w") as f:
                f.write(f"{self.last_activity:.3f}")
        except OSError:
            pass

    def auto_lock_tick(self) -> None:
        if (self.s.vault == "READY" and self.inflight == 0
                and self.clock() - self.last_activity >= self.cfg.auto_lock_s):
            self.lock()

    # ---- engines ---------------------------------------------------------------------------
    def set_engine(self, name: str, state: str) -> None:
        with self.mu:
            prev = self.s.engines.get(name)
            self.s.engines[name] = state
            if name == "embed":
                if state == "READY":
                    self.embed_down_since = None
                elif prev == "READY" and self.s.vault == "READY":
                    self.embed_down_since = self.clock()

    # ---- vault: unlock (9.4) ---------------------------------------------------------------
    def unlock(self, passphrase: str) -> tuple[int, dict]:
        with self.mu:
            if self.lockout.blocked():
                return 423, {"schema": SCHEMA, "error": "LOCKED_OUT", "retry_after_s": int(self.lockout.remaining())}
            if self.s.vault in ("UNLOCKING", "RECOVERING"):
                return 409, {"schema": SCHEMA, "error": "CONFLICT"}
            if self.s.vault != "LOCKED":
                return 409, {"schema": SCHEMA, "error": "CONFLICT"}
            self.s.vault = "UNLOCKING"
            self.lock_requested = False
        secret = passphrase.encode()
        threading.Thread(target=self._unlock_job, args=(secret,), name="unlock", daemon=True).start()
        return 202, {"schema": SCHEMA, "state": "VAULT_UNLOCKING"}

    def _unlock_job(self, secret: bytes) -> None:
        st = self.privd.call("vault-status")
        deadline = 120 + float(st.data.get("fsck_last_s") or 0)
        if st.data.get("last_close_clean") is False:
            deadline = max(deadline, 600)
        r = self.privd.call("vault-open", secrets=(secret,), deadline_s=deadline)
        del secret
        if r.name == "BAD_PASSPHRASE":
            self.lockout.failure()
        if not r.ok:
            with self.mu:
                self.s.vault, self.s.vault_last_error = "LOCKED", r.name
            return
        self.lockout.success()
        self.warnings.discard("LOCKOUT_STATE_RESET")
        with self.mu:
            if self.lock_requested:
                self.s.vault = "READY"
            else:
                self.s.vault = "RECOVERING"
        if self.lock_requested:
            self.lock()
            return
        try:
            self._open_brain()
        except Exception as e:
            log.error("recovery failed %s", type(e).__name__)
            with self.mu:
                self.s.vault = "READY"
            self.lock()
            return
        self.privd.call("unit-start", {"unit": "llm-embed"})
        self.privd.call("unit-start", {"unit": "sync"})
        with self.mu:
            self.s.vault, self.s.vault_last_error = "READY", None
            if self.s.engines.get("embed") != "READY":
                self.embed_down_since = self.clock()
        self.touch()
        if self.lock_requested:
            self.lock()

    def _open_brain(self) -> None:
        db_dir = os.path.join(self.cfg.vault_root, "db")
        os.makedirs(db_dir, exist_ok=True)
        for n in os.listdir(db_dir):                       # 13.7: leftover temp files
            if n.endswith(".tmp"):
                os.unlink(os.path.join(db_dir, n))
        path = os.path.join(db_dir, "brain.db")
        b = Brain(path, self.cfg.emb_key, embed_ready=lambda: self.s.engines.get("embed") == "READY")
        errs = b.check()
        if errs or b.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            b.db.close()
            os.replace(path, f"{path}.corrupt-{int(self.wall())}")
            b = Brain(path, self.cfg.emb_key, embed_ready=lambda: self.s.engines.get("embed") == "READY")
        self.brain = b
        notes = os.path.join(self.cfg.vault_root, "notes")
        os.makedirs(notes, exist_ok=True)
        self.watcher = Watcher(notes, b, cap=self._cap, clock_trusted=lambda: True,
                               now=self.clock, wall=self.wall)
        self.calibration_due = True                        # also run once after the first full index
        self.watcher.on_calibration = lambda: setattr(self, "calibration_due", True)
        self.stop_bg.clear()
        self.bg = [threading.Thread(target=self._watch_loop, name="watcher", daemon=True),
                   threading.Thread(target=self._index_loop, name="indexer", daemon=True)]
        for t in self.bg:
            t.start()

    def _cap(self) -> str:
        import datetime as dt
        return dt.datetime.fromtimestamp(self.wall(), dt.timezone(dt.timedelta(hours=8))).date().isoformat()

    # ---- background loops (single writer: both loops take self.mu around brain writes) -----
    def _watch_loop(self) -> None:
        while not self.stop_bg.is_set():
            with self.mu:
                if self.watcher is None:
                    return
                try:
                    self.watcher.scan()
                except Exception as e:
                    log.error("scan failed %s", type(e).__name__)
            self.stop_bg.wait(self.cfg.poll_s)

    def indexing_allowed(self) -> bool:
        if self.brain is None or self.s.engines.get("embed") != "READY":
            return False
        return self.inflight == 0 and self.clock() - self.last_interactive >= INDEX_QUIET_S

    def index_once(self) -> int:
        """Embed one pending chunk (11.7.2). Returns 1 if a chunk was processed."""
        with self.mu:
            if not self.indexing_allowed():
                return 0
            todo = self.brain.pending(1)
        if not todo:
            return 0
        cid, h, header, body = todo[0]
        from ..engine.client import EngineError
        try:
            vec = self.engines["embed"].embed(header + "\n" + body, self.clock() + 30)
        except EngineError:
            return 0                                    # transport error: stays pending
        with self.mu:
            if self.brain is not None:
                self.brain.store_vector(h, vec)
        return 1

    def _index_loop(self) -> None:
        while not self.stop_bg.is_set():
            if not self.index_once():
                self.stop_bg.wait(1.0)

    # ---- maintenance: calibration (13.6.7), daily work (13.2.6, 13.7, 14.2), disk floors (11.6)
    def maintenance_tick(self) -> None:
        from ..brain import maintenance as M
        with self.mu:
            b, w = self.brain, self.watcher
            if b is None or w is None or self.inflight or self.clock() - self.last_interactive < INDEX_QUIET_S:
                return
            low = M.disk_low(self.cfg.vault_root)
            (self.warnings.add if low else self.warnings.discard)("DISK_LOW_VAULT")
            if getattr(self, "calibration_due", False) and self.s.engines.get("embed") == "READY":
                path = os.path.join(self.cfg.vault_root, "notes", "_gladeon", "calibration.md")
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except (OSError, UnicodeDecodeError):
                    text = None
                if text is not None:
                    def emb(q):
                        try:
                            return self.engines["embed"].embed(q, self.clock() + 10)
                        except Exception:
                            return None
                    r = M.run_calibration(b, text, emb, b.pending_count(), w.first_scan_done, w.unstable_files())
                    if "CALIBRATION_WAITING" not in r.warnings:
                        self.calibration_due = False
                    (self.warnings.add if "CALIBRATION_TARGET_UNRESOLVED" in r.warnings
                     else self.warnings.discard)("CALIBRATION_TARGET_UNRESOLVED")
                else:
                    self.calibration_due = False
            if self.clock() - getattr(self, "last_daily", -1e18) >= 86400:
                self.last_daily = self.clock()
                M.daily(b, key_change_complete=b.pending_count() == 0)
                if not low:
                    M.snapshot(b, os.path.join(self.cfg.vault_root, "backups"), True, int(self.wall()))

    # ---- search hooks for the pipeline -----------------------------------------------------
    def _search(self, text: str, floor_name: str):
        with self.mu:
            b = self.brain
            if b is None:
                return []
            floor = b.floor(floor_name, self.cfg.default_floors[0 if floor_name == "cos_floor" else 1])
        qvec = None
        if self.s.engines.get("embed") == "READY":
            from ..engine.client import EngineError
            try:
                qvec = self.engines["embed"].embed(text, self.clock() + 5)
            except EngineError:
                qvec = None
        with self.mu:
            return b.search(text, qvec, floor) if self.brain is b else []

    def _partial(self):
        b, w = self.brain, self.watcher
        if b is None or w is None:
            return None
        return partial(PartialState(
            embed_ready=self.s.engines.get("embed") == "READY", embed_down_since=self.embed_down_since,
            pending=b.pending_count(), unstable_files=w.unstable_files(), first_scan_done=w.first_scan_done,
            embed_failed=b.failed_count(), over_ceiling=b.over_ceiling(), disk_low=False), self.clock())

    def _partial_transient_with_rescan(self) -> bool:
        with self.mu:
            p = self._partial()
            if p is None:
                return False
            if p.transient:
                return True
            return self.watcher.rescan_changed()

    def _still_reading_n(self) -> int:
        with self.mu:
            if self.brain is None or self.watcher is None:
                return 0
            return self.brain.pending_count() + self.watcher.unstable_files()

    def _command(self, cmd: str) -> tuple:
        if cmd == "lock":
            threading.Thread(target=self.lock, daemon=True).start()
            return ("Locking the vault.", "Locking the vault.")
        if cmd == "time":
            t = time.strftime("%I:%M %p", time.gmtime(self.wall() + 8 * 3600)).lstrip("0")
            return (f"It's {t}.", f"It's {t}.")
        st = public_state(self.s).replace("_", " ").lower()
        return (f"Status: {st}.", f"Status: {st}.")

    # ---- vault: lock (9.5) -----------------------------------------------------------------
    def lock(self) -> tuple[int, dict]:
        with self.mu:
            if self.s.vault in ("UNLOCKING", "RECOVERING"):
                self.lock_requested = True
                self.privd.call("lock-intent-set")
                return 202, {"schema": SCHEMA, "state": "LOCK_REQUESTED"}
            if not lock_allowed(self.s):
                return 202, {"schema": SCHEMA, "state": public_state(self.s)}
            self.s.vault = "LOCKING"
        self.privd.call("lock-intent-set")                        # step 1, before the 202 returns
        threading.Thread(target=self._lock_job, name="lock", daemon=True).start()
        return 202, {"schema": SCHEMA, "state": "LOCKING"}

    def _lock_job(self) -> None:
        self.stop_bg.set()                                         # step 2
        for t in self.bg:
            t.join(timeout=10)
        end = self.clock() + BRAIN_DRAIN_S                         # step 3
        while self.inflight and self.clock() < end:
            time.sleep(0.05)
        self.cache.replace_brain(lambda e: {"schema": SCHEMA, "route": "VAULT_LOCKED",
                                            "display_text": R.VAULT_LOCKED[0], "tts_text": R.VAULT_LOCKED[1]})
        self.pipeline.followup.wipe()
        with self.mu:                                              # step 5
            if self.brain is not None:
                try:
                    self.brain.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    self.brain.db.close()
            self.brain, self.watcher, self.bg = None, None, []
            self.warnings.discard("CALIBRATION_TARGET_UNRESOLVED")
        self.privd.call("unit-stop", {"unit": "llm-embed"})        # step 6
        r = self.privd.call("vault-close", deadline_s=60)           # step 7
        with self.mu:
            if r.ok:
                self.s.vault, self.embed_down_since = "LOCKED", None
            else:
                self.s.vault, self.s.vault_last_error = "LOCK_FAILED", r.name

    # ---- reconciliation at proxy start (6.3.5) --------------------------------------------
    def reconcile(self, busy_retry_s: float = 0.5, max_wait_s: float = 600) -> str:
        st = self.privd.call("vault-status")
        if not st.ok:
            self.s.vault, self.s.vault_last_error = "LOCKED", st.name
            return "privd-error"
        d = st.data
        if d.get("lock_holder") in ("vault-open", "vault-setup", "vault-erase"):
            self.privd.call("lock-intent-set")
            self._close_retrying(busy_retry_s, max_wait_s)
            return "closed-after-open"
        if d.get("lock_intent") and d.get("mapping_open"):
            self.s.vault = "LOCKING"
            self._lock_job()
            return "resumed-lock"
        if d.get("mounted") and d.get("id_ok") and not d.get("lock_intent"):
            saved = self._saved_activity()
            if saved is None:
                self.s.vault = "READY"
                self._lock_job()
                return "locked-no-activity"
            self.s.vault = "RECOVERING"
            try:
                self._open_brain()
            except Exception as e:
                log.error("adopt failed %s", type(e).__name__)
                self.s.vault = "LOCKING"
                self._lock_job()
                return "locked-adopt-failed"
            self.privd.call("unit-start", {"unit": "llm-embed"})
            self.last_activity = saved
            self.s.vault = "READY"
            return "adopted"
        if d.get("mapping_open"):
            self._close_retrying(busy_retry_s, max_wait_s)
            return "closed-half-open"
        self.s.vault = "LOCKED"
        return "locked"

    def _close_retrying(self, retry_s: float, max_wait_s: float) -> None:
        end = self.clock() + max_wait_s
        while True:
            r = self.privd.call("vault-close", deadline_s=60)
            if r.name != "PRIVD_BUSY" or self.clock() >= end:
                break
            time.sleep(retry_s)
        self.s.vault = "LOCKED" if r.ok else "LOCK_FAILED"
        if not r.ok:
            self.s.vault_last_error = r.name

    def _saved_activity(self):
        try:
            with open(os.path.join(self.cfg.run_dir, "last_activity")) as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return None

    # ---- /query ----------------------------------------------------------------------------
    def query_blocked(self, mode: str):
        return query_blocked(self.s)

    def run_query(self, transcript: str, mode: str, client_epoch) -> dict:
        with self.mu:
            self.inflight += 1
            self.last_interactive = self.clock()
        try:
            rep = self.pipeline.handle(transcript, mode)
        finally:
            with self.mu:
                self.inflight -= 1
                self.last_interactive = self.clock()
        if rep.route == "BRAIN_RAG" and self.s.vault != "READY":    # 9.5.3
            return {"schema": SCHEMA, "state": public_state(self.s), "route": "VAULT_LOCKED", "mode": rep.mode,
                    "display_text": R.VAULT_LOCKED[0], "tts_text": R.VAULT_LOCKED[1], "warnings": []}
        return {"schema": SCHEMA, "state": public_state(self.s), "route": rep.route, "mode": rep.mode,
                "display_text": rep.display_text, "tts_text": rep.tts_text, "warnings": rep.warnings}

    def processing_reply(self) -> dict:
        return {"schema": SCHEMA, "status": "PROCESSING", "display_text": "Working on it.",
                "tts_text": "Working on it.", "retry_window_s": 210}

    # ---- system ----------------------------------------------------------------------------
    def system(self, action: str) -> tuple[int, dict]:
        st = self.privd.call("vault-status")
        if st.data.get("copy_active") or (st.data.get("rotation") or {}).get("state") == "erasing":
            return 503, {"schema": SCHEMA, "error": "JOB_IN_PROGRESS"}
        r = self.privd.call("poweroff" if action == "shutdown" else "reboot")
        if not r.ok:
            return 503, {"schema": SCHEMA, "error": r.name or "BUSY"}
        return 202, {"schema": SCHEMA}

    def engine_restart(self, engine: str) -> tuple[int, dict]:
        r = self.privd.call("unit-restart", {"unit": f"llm-{engine}"})
        return (202, {"schema": SCHEMA}) if r.ok else (503, {"schema": SCHEMA, "error": "BUSY"})

    def preflight(self) -> list:
        return []

    # ---- /status (11.5) --------------------------------------------------------------------
    def status(self) -> dict:
        with self.mu:
            b, w = self.brain, self.watcher
            p = self._partial()
            idx = {"notes": 0, "files_total": 0, "chunks": 0, "pending": 0, "embed_failed": 0, "skipped": 0,
                   "unstable_files": 0, "first_scan_done": False, "partial_transient": False,
                   "partial_permanent": False, "calibrated": False, "chunk_ceiling": 0, "last_indexed_epoch": 0}
            if b is not None and w is not None:
                idx.update(notes=len(b.relpaths()),
                           chunks=b.db.execute("SELECT count(*) FROM chunks").fetchone()[0],
                           pending=b.pending_count(), embed_failed=b.failed_count(),
                           unstable_files=w.unstable_files(), first_scan_done=w.first_scan_done,
                           partial_transient=p.transient, partial_permanent=p.permanent,
                           calibrated=b.get_meta(f"cos_floor:{b.emb_key}") is not None,
                           chunk_ceiling=b.chunk_ceiling)
            warnings = sorted(self.warnings | set(p.warnings if p else []))
            if not idx["calibrated"] and b is not None:
                warnings.append("INDEX_UNCALIBRATED")
            left = None
            if self.s.vault == "READY":
                left = max(0, int(self.cfg.auto_lock_s - (self.clock() - self.last_activity)))
            return {"schema": SCHEMA, "state": public_state(self.s), "boot": self.s.boot,
                    "degraded_reason": self.s.degraded_reason, "engines": dict(self.s.engines),
                    "vault": self.s.vault, "vault_last_error": self.s.vault_last_error,
                    "maintenance": self.s.maintenance, "auto_lock_in_s": left, "index": idx,
                    "warnings": warnings, "uptime_s": int(self.clock() - self.started)}
