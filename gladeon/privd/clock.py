"""Clock floor and correction budget (5.2.17, 10.6). Pure logic plus persisted state."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Optional

from ..atomic import atomic_write

BUDGET_S = 86_400
TICK_SLACK_S = 60
DRIFT_S = 60


@dataclass
class Decision:
    ok: bool
    code: Optional[str] = None      # CLOCK_BUDGET_EXCEEDED | BELOW_FLOOR
    charged: int = 0
    free: int = 0


def charge(now: int, target: int, floor: int) -> Decision:
    """Cost of moving the system clock from `now` to `target` under `floor`."""
    if target < floor:
        return Decision(False, "BELOW_FLOOR")
    if now < floor:
        return Decision(True, charged=target - floor, free=floor - now)
    return Decision(True, charged=abs(target - now))


class ClockState:
    """Persisted: floor (epoch s) and budget used. In memory: the wall/monotonic pair of the last tick."""

    def __init__(self, state_dir: str, wall: Callable[[], float], mono: Callable[[], float],
                 set_clock: Callable[[int], bool]):
        self.dir = state_dir
        self.wall, self.mono, self.set_clock = wall, mono, set_clock
        self.floor = self._read_int("clock_floor", 0)
        self.used = int(self._read_json("clock_budget.json").get("used_s", 0))
        self._tick: Optional[tuple[float, float]] = None

    # ---- persistence -----------------------------------------------------------------------
    def _p(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def _read_int(self, name: str, default: int) -> int:
        try:
            with open(self._p(name)) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return default

    def _read_json(self, name: str) -> dict:
        try:
            with open(self._p(name)) as f:
                v = json.load(f)
            return v if isinstance(v, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        atomic_write(self._p("clock_floor"), str(self.floor).encode())
        atomic_write(self._p("clock_budget.json"), json.dumps({"used_s": self.used}).encode())

    def _log(self, old: int, new: int) -> None:
        with open(self._p("time_corrections.log"), "a") as f:
            f.write(f"{old} {new} {self.mono():.3f}\n")
            f.flush()
            os.fsync(f.fileno())

    # ---- verbs -----------------------------------------------------------------------------
    def tick(self) -> int:
        now, m = int(self.wall()), self.mono()
        if now >= self.floor:
            if self._tick is None or now - self._tick[0] <= (m - self._tick[1]) + TICK_SLACK_S:
                if now > self.floor:
                    self.floor = now
                    self._save()
                self._tick = (now, m)
        return self.floor

    def set_time(self, target: int, force: bool = False) -> Decision:
        now = int(self.wall())
        d = charge(now, target, self.floor)
        if not d.ok and not force:
            return d
        if not force and self.used + d.charged > BUDGET_S:
            return Decision(False, "CLOCK_BUDGET_EXCEEDED", d.charged, d.free)
        if not self.set_clock(target):
            return Decision(False, "PRIVD_INTERNAL")
        if not force:
            self.used += d.charged
        self.floor = max(self.floor, target) if not force else target
        self._tick = None
        self._save()
        self._log(now, target)
        return d

    def budget_reset(self) -> None:
        self.used = 0
        self._save()

    def floor_set(self, value: int) -> None:
        self.floor = value
        self._tick = None
        self._save()

    def stop(self) -> None:
        self.tick()


def proxy_correction(phone_epoch: int, now: int, trusted: bool, floor: int) -> tuple[Optional[int], Optional[str]]:
    """10.6 policy on the proxy side: which target (if any) to ask privd for."""
    if not trusted:
        if phone_epoch >= floor:
            return phone_epoch, None
        return None, "CLOCK_BELOW_FLOOR"
    if abs(phone_epoch - now) > DRIFT_S:
        return phone_epoch, None
    return None, None
