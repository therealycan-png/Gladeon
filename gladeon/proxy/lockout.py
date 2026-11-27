"""Unlock lockout (9.7), persisted atomically (5.x state-file rule)."""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Callable, Optional

FREE_ATTEMPTS = 5
BASE_S = 60
MAX_S = 3600


def atomic_write(path: str, data: bytes, mode: int = 0o600) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        os.fchmod(fd, mode)
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
        dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Lockout:
    """failures, step (doubling index) and remaining block seconds survive restarts.
    Remaining time is counted on the monotonic clock and re-armed in full after a restart."""

    def __init__(self, path: str, clock: Callable[[], float] = time.monotonic):
        self.path = path
        self.clock = clock
        self.warning: Optional[str] = None
        self.failures, self.step, remaining = 0, 0, 0.0
        try:
            with open(path, "rb") as f:
                d = json.loads(f.read())
            self.failures, self.step, remaining = int(d["failures"]), int(d["step"]), float(d["remaining_s"])
            if self.failures < 0 or self.step < 0 or remaining < 0:
                raise ValueError
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            self.failures, self.step, remaining = FREE_ATTEMPTS, 0, BASE_S   # 60 s + warning
            self.warning = "LOCKOUT_STATE_RESET"
        self.until = self.clock() + remaining if remaining > 0 else 0.0
        if self.warning:
            self._save()

    def remaining(self) -> float:
        return max(0.0, self.until - self.clock())

    def blocked(self) -> bool:
        return self.remaining() > 0

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= FREE_ATTEMPTS:
            block = min(MAX_S, BASE_S * (2 ** self.step))
            self.step += 1
            self.until = self.clock() + block
        self._save()

    def success(self) -> None:
        self.failures, self.step, self.until = 0, 0, 0.0
        self.warning = None
        self._save()

    def _save(self) -> None:
        atomic_write(self.path, json.dumps({"failures": self.failures, "step": self.step,
                                            "remaining_s": round(self.remaining(), 3)}).encode())
