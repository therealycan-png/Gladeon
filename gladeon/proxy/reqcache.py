"""Request-ID dedup cache (11.3)."""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

TTL_S = 600
MAX_ENTRIES = 64
RUNNING = "running"
DONE = "done"


def valid_uuid4(s: str) -> bool:
    try:
        u = uuid.UUID(s)
    except (ValueError, TypeError, AttributeError):
        return False
    return u.version == 4 and str(u) == s.lower()


@dataclass
class Entry:
    state: str
    route: Optional[str] = None
    status: int = 202
    payload: Optional[dict] = None
    done_at: float = 0.0
    no_cache: bool = False


class RequestCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._e: "OrderedDict[str, Entry]" = OrderedDict()
        self._mu = threading.RLock()            # workers and query threads share the cache

    def _expire(self) -> None:
        now = self.clock()
        for k in [k for k, e in self._e.items() if e.state == DONE and now - e.done_at > TTL_S]:
            del self._e[k]

    def lookup(self, rid: str) -> Optional[Entry]:
        with self._mu:
            self._expire()
            return self._e.get(rid)

    def forget(self, rid: str) -> None:
        with self._mu:
            self._e.pop(rid, None)

    def begin(self, rid: str, no_cache: bool = False) -> bool:
        """Register a new request. False if the id is already known (caller replies from lookup)."""
        with self._mu:
            return self._begin(rid, no_cache)

    def _begin(self, rid: str, no_cache: bool) -> bool:
        self._expire()
        if rid in self._e:
            return False
        while len(self._e) >= MAX_ENTRIES:
            victim = next((k for k, e in self._e.items() if e.state == DONE), None)
            if victim is None:
                break
            del self._e[victim]
        self._e[rid] = Entry(RUNNING, no_cache=no_cache)
        return True

    def finish(self, rid: str, status: int, payload: dict, route: Optional[str] = None) -> None:
        with self._mu:
            self._finish(rid, status, payload, route)

    def _finish(self, rid: str, status: int, payload: dict, route: Optional[str]) -> None:
        e = self._e.get(rid)
        if e is None:
            return
        if e.no_cache:                     # unlock/lock bodies are never cached
            del self._e[rid]
            return
        e.state, e.status, e.payload, e.route, e.done_at = DONE, status, payload, route, self.clock()

    def replace_brain(self, payload_for: Callable[[Entry], dict]) -> None:
        """At lock (9.5.3): BRAIN results are replaced by the locked reply."""
        with self._mu:
            self._replace_brain(payload_for)

    def _replace_brain(self, payload_for) -> None:
        for e in self._e.values():
            if e.route == "BRAIN_RAG" and e.state == DONE:
                e.payload = payload_for(e)

    def clear_brain(self) -> None:
        with self._mu:
            self._clear_brain()

    def _clear_brain(self) -> None:
        for k in [k for k, e in self._e.items() if e.route == "BRAIN_RAG"]:
            del self._e[k]

    def __len__(self) -> int:
        return len(self._e)
