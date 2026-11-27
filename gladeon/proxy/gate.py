"""Connection gate (11.2): peer allowlist, bearer token, keep-alive policy."""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from .httpcore import HttpError, Request

KEEPALIVE_IDLE_S = 5.0
KEEPALIVE_MAX = 20
ACCEPT_QUEUE = 8
WORKERS = 4


def peer_allowed(peer_ip: str, phone_ips: frozenset[str]) -> bool:
    return peer_ip in phone_ips


def check_bearer(req: Request, token: bytes) -> None:
    v = req.header("authorization") or ""
    given = v[7:].encode() if v.startswith("Bearer ") else b""
    # compare_digest on equal-length digests so length is not leaked either
    if not given or not hmac.compare_digest(_h(given), _h(token)):
        raise HttpError(401, "UNAUTHORIZED")


def _h(b: bytes) -> bytes:
    import hashlib
    return hashlib.sha256(b).digest()


@dataclass
class ConnPolicy:
    served: int = 0
    authed_2xx: bool = False

    def after(self, status: int, authenticated: bool) -> bool:
        """Return True if the connection may stay open (11.2.8)."""
        self.served += 1
        if status >= 400:
            return False
        if authenticated and status in (200, 202):
            self.authed_2xx = True
        return self.authed_2xx and self.served < KEEPALIVE_MAX
