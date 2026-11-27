"""Endpoint dispatch (11.4) over a Controller that owns state, pipeline and privd."""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional, Protocol

from .httpcore import SCHEMA, HttpError, Request
from .reqcache import RequestCache, valid_uuid4

_RESULT_RE = re.compile(r"^/result/([0-9a-f-]{36})$")
ENGINES = ("quick", "deep", "embed")


class Controller(Protocol):
    def status(self) -> dict: ...
    def preflight(self) -> list: ...
    def query_blocked(self, mode: str) -> Optional[tuple[int, str]]: ...
    def run_query(self, transcript: str, mode: str, client_epoch: Optional[int]) -> dict: ...
    def unlock(self, passphrase: str) -> tuple[int, dict]: ...
    def lock(self) -> tuple[int, dict]: ...
    def system(self, action: str) -> tuple[int, dict]: ...
    def engine_restart(self, engine: str) -> tuple[int, dict]: ...
    def touch(self) -> None: ...
    def processing_reply(self) -> dict: ...


def err(code: str) -> dict:
    from .httpcore import DETAIL
    return {"schema": SCHEMA, "error": code, "detail": DETAIL.get(code, "Error.")}


class App:
    def __init__(self, ctl: Controller, cache: Optional[RequestCache] = None, sync_wait_s: float = 205.0):
        self.ctl = ctl
        self.cache = cache or RequestCache()
        self.sync_wait_s = sync_wait_s    # how long /query waits before answering 202
        self._done: dict[str, threading.Event] = {}

    def __call__(self, req: Request) -> tuple[int, dict]:
        m, p = req.method, req.path
        if m == "GET" and p == "/status":
            return 200, self.ctl.status()
        if m == "GET" and p == "/preflight":
            return 200, {"schema": SCHEMA, "checks": self.ctl.preflight()}
        if m == "GET":
            mm = _RESULT_RE.match(p)
            if mm:
                self.ctl.touch()
                return self._result(mm.group(1))
        if m != "POST":
            raise HttpError(404, "NOT_FOUND")
        body = req.json()
        if p == "/query":
            self.ctl.touch()
            return self._query(req, body)
        if p == "/vault/unlock":
            self.ctl.touch()
            pw = body.get("passphrase")
            if not isinstance(pw, str) or not pw:
                raise HttpError(400, "BAD_REQUEST")
            return self.ctl.unlock(pw)
        if p == "/vault/lock":
            self.ctl.touch()
            return self.ctl.lock()
        if p in ("/system/shutdown", "/system/reboot"):
            want = "SHUTDOWN" if p.endswith("shutdown") else "REBOOT"
            if body.get("confirm") != want:
                raise HttpError(400, "BAD_REQUEST")
            return self.ctl.system(want.lower())
        if p == "/engine/restart":
            if body.get("engine") not in ENGINES:
                raise HttpError(400, "BAD_REQUEST")
            return self.ctl.engine_restart(body["engine"])
        raise HttpError(404, "NOT_FOUND")

    # ---- /query and /result ----------------------------------------------------------------
    def _query(self, req: Request, body: dict) -> tuple[int, dict]:
        rid = (req.header("x-request-id") or "").lower()
        if not valid_uuid4(rid):
            raise HttpError(400, "BAD_REQUEST")
        known = self.cache.lookup(rid)
        if known is not None:                       # dedup (11.3): 202 while running, cached after
            return (known.status, known.payload) if known.state == "done" else (202, self.ctl.processing_reply())
        transcript = body.get("transcript")
        mode = body.get("mode", "auto")
        epoch = body.get("client_epoch")
        if not isinstance(transcript, str) or mode not in ("auto", "quick", "deep") or \
                (epoch is not None and not isinstance(epoch, int)):
            raise HttpError(400, "BAD_REQUEST")
        blocked = self.ctl.query_blocked(mode)
        if blocked:
            return blocked[0], err(blocked[1])
        self.cache.begin(rid)
        ev = threading.Event()
        self._done[rid] = ev

        def run():
            try:
                out = self.ctl.run_query(transcript, mode, epoch)
                out["request_id"] = rid
                self.cache.finish(rid, 200, out, route=out.get("route"))
            except Exception as e:
                self.cache.finish(rid, 503, err("BUSY"))
            finally:
                ev.set()
                self._done.pop(rid, None)
        threading.Thread(target=run, name="query", daemon=True).start()
        if ev.wait(self.sync_wait_s):
            e = self.cache.lookup(rid)
            return e.status, e.payload
        return 202, self.ctl.processing_reply()

    def _result(self, rid: str) -> tuple[int, dict]:
        e = self.cache.lookup(rid)
        if e is None:
            raise HttpError(404, "NOT_FOUND")
        if e.state != "done":
            return 202, self.ctl.processing_reply()
        return e.status, e.payload
