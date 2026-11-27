"""Python output hygiene and liveness monitor (7.2). Shared by the proxy and privd."""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import warnings
from typing import Callable, Optional

log = logging.getLogger("gladeon")


class _CodeSink:
    """Replaces sys.stderr: anything written is dropped and counted; only codes reach the journal."""

    def __init__(self):
        self.dropped = 0

    def write(self, s: str) -> int:
        self.dropped += 1
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def install(component: str) -> None:
    handler = logging.StreamHandler(sys.__stdout__)
    handler.setFormatter(logging.Formatter(f"{component} %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    logging.lastResort = handler
    sys.stderr = _CodeSink()

    def excepthook(et, ev, tb):
        log.error("UNCAUGHT %s", et.__name__)

    def thread_hook(args):
        log.error("THREAD_UNCAUGHT %s in %s", args.exc_type.__name__, getattr(args.thread, "name", "?"))

    def unraisable(u):
        log.error("UNRAISABLE %s", type(u.exc_value).__name__ if u.exc_value else "None")

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    sys.unraisablehook = unraisable

    def showwarning(message, category, filename, lineno, file=None, line=None):
        log.warning("PY_WARNING %s", category.__name__)
    warnings.showwarning = showwarning


class Liveness:
    """7.2 watchdog rule: a thread is stale only if it neither beat in 10 s nor is inside a declared
    operation whose deadline has not passed."""

    STALE_S = 10.0

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.mu = threading.Lock()
        self.beats: dict[str, float] = {}
        self.ops: dict[str, float] = {}

    def beat(self, name: str) -> None:
        with self.mu:
            self.beats[name] = self.clock()

    def op(self, name: str, seconds: float):
        mon = self

        class _Op:
            def __enter__(self_):
                with mon.mu:
                    mon.ops[name] = mon.clock() + seconds
                return self_

            def __exit__(self_, *a):
                with mon.mu:
                    mon.ops.pop(name, None)
                    mon.beats[name] = mon.clock()
                return False
        return _Op()

    def stale(self) -> list[str]:
        now = self.clock()
        with self.mu:
            return [n for n, t in self.beats.items()
                    if now - t > self.STALE_S and not (n in self.ops and self.ops[n] > now)]


def sd_notify(msg: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.connect(addr)
        s.sendall(msg.encode())
        return True
    except OSError:
        return False
    finally:
        s.close()
