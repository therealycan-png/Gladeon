"""Sub-states and public state (6.1-6.3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

ENGINE_STATES = ("STOPPED", "LOADING", "READY", "FAILED", "RECYCLING")
VAULT_STATES = ("LOCKED", "UNLOCKING", "RECOVERING", "READY", "LOCKING", "LOCK_FAILED")


@dataclass
class SubStates:
    boot: str = "PREFLIGHT"                    # PREFLIGHT | RUNNING | DEGRADED
    degraded_reason: Optional[str] = None
    engines: dict = field(default_factory=lambda: {"quick": "STOPPED", "deep": "STOPPED", "embed": "STOPPED"})
    vault: str = "LOCKED"
    vault_last_error: Optional[str] = None
    maintenance: bool = False


def public_state(s: SubStates) -> str:
    if s.boot == "DEGRADED":
        return "DEGRADED"
    if s.boot == "PREFLIGHT":
        return "BOOTING"
    if s.vault == "LOCK_FAILED":
        return "VAULT_LOCK_FAILED"
    if s.engines.get("quick") != "READY" and s.engines.get("deep") != "READY":
        return "LOADING_ENGINE"
    if s.maintenance:
        return "MAINTENANCE_BUSY"
    if s.vault in ("UNLOCKING", "RECOVERING"):
        return "VAULT_UNLOCKING"
    if s.vault in ("LOCKED", "LOCKING"):
        return "VAULT_LOCKED_PUBLIC_ONLY"
    return "FULL_READY"


def unlock_allowed(s: SubStates) -> tuple[bool, Optional[int]]:
    """6.3.1: accepted only when LOCKED; UNLOCKING/RECOVERING -> 409."""
    if s.vault == "LOCKED":
        return True, None
    if s.vault in ("UNLOCKING", "RECOVERING", "READY", "LOCKING"):
        return False, 409
    return False, 409


def lock_allowed(s: SubStates) -> bool:
    """6.3.3: READY, LOCK_FAILED and UNLOCKING/RECOVERING (queued)."""
    return s.vault in ("READY", "LOCK_FAILED", "UNLOCKING", "RECOVERING")


def query_blocked(s: SubStates) -> Optional[tuple[int, str]]:
    if s.boot == "DEGRADED":
        return 503, "DEGRADED"
    if s.boot == "PREFLIGHT":
        return 503, "LOADING"
    if s.engines.get("quick") != "READY" and s.engines.get("deep") not in ("READY", "LOADING"):
        return 503, "LOADING"
    return None
