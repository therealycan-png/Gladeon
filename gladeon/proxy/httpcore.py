"""Minimal HTTP/1.1 request reader with the 11.2 limits. No http.server: we control every byte."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

MAX_HEADER_BYTES = 8 * 1024
MAX_BODY_BYTES = 64 * 1024
REQUEST_DEADLINE_S = 3.0
SCHEMA = 2

REASONS = {200: "OK", 202: "Accepted", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
           404: "Not Found", 409: "Conflict", 413: "Payload Too Large", 423: "Locked",
           429: "Too Many Requests", 503: "Service Unavailable", 504: "Gateway Timeout"}

# fixed detail text per error code; never derived from input
DETAIL = {
    "BAD_REQUEST": "Malformed request.", "UNAUTHORIZED": "Missing or wrong token.", "FORBIDDEN": "Not allowed.",
    "NOT_FOUND": "No such resource.", "TOO_LARGE": "Request too large.", "TIMEOUT": "Request took too long.",
    "CONFLICT": "Already in progress.", "LOCKED_OUT": "Too many attempts. Wait and try again.",
    "LOADING": "Engine loading.", "MAINTENANCE": "Maintenance in progress.", "DEGRADED": "System degraded.",
    "BUSY": "Busy.", "UPDATE_WINDOW_OPEN": "Update window open.", "COPY_IN_PROGRESS": "Offline copy running.",
    "ROTATION_IN_PROGRESS": "Vault rotation running.", "JOB_IN_PROGRESS": "A vault job is running.",
    "BAD_PASSPHRASE": "Wrong passphrase.", "VAULT_LOCKED": "Vault is locked.",
}


class HttpError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes = b""

    def header(self, name: str) -> Optional[str]:
        return self.headers.get(name.lower())

    def json(self) -> dict:
        try:
            v = json.loads(self.body.decode("utf-8")) if self.body else {}
        except (UnicodeDecodeError, ValueError):
            raise HttpError(400, "BAD_REQUEST")
        if not isinstance(v, dict):
            raise HttpError(400, "BAD_REQUEST")
        return v


class Reader:
    """Pulls bytes from `recv(n)` until the deadline. `recv` returns b'' on EOF and raises TimeoutError
    on a 5 s socket timeout (7.2 loop rule); either way the deadline is re-checked."""

    def __init__(self, recv: Callable[[int], bytes], deadline: float, clock: Callable[[], float] = time.monotonic):
        self.recv = recv
        self.deadline = deadline
        self.clock = clock
        self.buf = b""

    def _fill(self) -> bool:
        if self.clock() > self.deadline:
            raise HttpError(504, "TIMEOUT")
        try:
            chunk = self.recv(4096)
        except TimeoutError:
            if self.clock() > self.deadline:
                raise HttpError(504, "TIMEOUT")
            return True
        if not chunk:
            return False
        self.buf += chunk
        return True

    def read_head(self) -> bytes:
        while b"\r\n\r\n" not in self.buf:
            if len(self.buf) > MAX_HEADER_BYTES:
                raise HttpError(413, "TOO_LARGE")
            if not self._fill():
                raise HttpError(400, "BAD_REQUEST")
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if len(head) + 4 > MAX_HEADER_BYTES:
            raise HttpError(413, "TOO_LARGE")
        return head

    def read_body(self, n: int) -> bytes:
        while len(self.buf) < n:
            if not self._fill():
                raise HttpError(400, "BAD_REQUEST")
        body, self.buf = self.buf[:n], self.buf[n:]
        return body


def parse_head(head: bytes) -> tuple[str, str, dict[str, str]]:
    try:
        text = head.decode("ascii")
    except UnicodeDecodeError:
        raise HttpError(400, "BAD_REQUEST")
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or parts[2] not in ("HTTP/1.1", "HTTP/1.0") or parts[0] not in ("GET", "POST"):
        raise HttpError(400, "BAD_REQUEST")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line or line[0] in " \t":
            raise HttpError(400, "BAD_REQUEST")       # no obsolete line folding
        k, v = line.split(":", 1)
        k = k.strip().lower()
        if not k or k in headers:
            raise HttpError(400, "BAD_REQUEST")       # duplicate headers rejected (smuggling guard)
        headers[k] = v.strip()
    return parts[0], parts[1], headers


def read_request(reader: Reader) -> Request:
    method, path, headers = parse_head(reader.read_head())
    if "transfer-encoding" in headers:
        raise HttpError(400, "BAD_REQUEST")
    if "origin" in headers or "sec-fetch-site" in headers:
        raise HttpError(403, "FORBIDDEN")
    return Request(method, path, headers)


def read_body(reader: Reader, req: Request) -> None:
    """Called only after the bearer check (11.2.7 order)."""
    cl = req.header("content-length")
    if req.method == "GET":
        if cl not in (None, "0"):
            raise HttpError(400, "BAD_REQUEST")
        return
    if cl is None or not cl.isdigit():
        raise HttpError(400, "BAD_REQUEST")
    n = int(cl)
    if n > MAX_BODY_BYTES:
        raise HttpError(413, "TOO_LARGE")
    req.body = reader.read_body(n)


def response(status: int, payload: dict, keep_alive: bool = False) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    head = (f"HTTP/1.1 {status} {REASONS[status]}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
            f"Connection: {'keep-alive' if keep_alive else 'close'}\r\n\r\n")
    return head.encode() + body


def error(status: int, code: str) -> bytes:
    return response(status, {"schema": SCHEMA, "error": code, "detail": DETAIL.get(code, "Error.")})
