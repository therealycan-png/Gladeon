"""Wire format (5.2.5): one JSON line + up to two length-prefixed secrets; reply line + optional blob."""
from __future__ import annotations

import json
import struct
from typing import Optional

MAX_LINE = 64 * 1024
MAX_SECRET = 4096
MAX_BLOB = 100 * 1024 * 1024
_LEN = struct.Struct(">I")

CODES = {
    0: None, 10: "BAD_PASSPHRASE", 11: "DEVICE_MISSING", 12: "DEVICE_BUSY_OR_OPEN", 13: "UNLOCK_OUT_OF_MEMORY",
    14: "FSCK_FAILED", 15: "MOUNT_FAILED", 16: "UMOUNT_BUSY", 17: "VAULT_ID_MISMATCH", 18: "UNIT_START_REFUSED",
    19: "PRIVD_INTERNAL", 20: "COPY_IN_PROGRESS", 21: "NOT_PERMITTED", 22: "CLOCK_BUDGET_EXCEEDED",
    23: "UPDATE_WINDOW_OPEN", 24: "ROTATE_PRECONDITION_FAILED", 26: "JOB_IN_PROGRESS", 27: "KEYSLOT_CHECK_FAILED",
    28: "LOCK_REQUESTED", 29: "PRIVD_BUSY", 30: "ROTATION_IN_PROGRESS", 31: "VAULT_DIRTY",
}
BY_NAME = {v: k for k, v in CODES.items() if v}
# fixed detail text per code; never tool output
DETAIL = {k: (v or "OK").replace("_", " ").capitalize() + "." for k, v in CODES.items()}


class ProtocolError(Exception):
    pass


class Stream:
    """Buffered reads from a blocking socket with a timeout set by the caller."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def _fill(self) -> None:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ProtocolError("eof")
        self.buf += chunk

    def line(self) -> bytes:
        while b"\n" not in self.buf:
            if len(self.buf) > MAX_LINE:
                raise ProtocolError("line too long")
            self._fill()
        line, self.buf = self.buf.split(b"\n", 1)
        if len(line) > MAX_LINE:
            raise ProtocolError("line too long")
        return line

    def exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            self._fill()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def prefixed(self, limit: int) -> bytes:
        (n,) = _LEN.unpack(self.exact(4))
        if n > limit:
            raise ProtocolError("frame too long")
        return self.exact(n)


def encode_request(verb: str, args: Optional[dict] = None, secrets: tuple = ()) -> bytes:
    if len(secrets) > 2:
        raise ValueError("at most two secrets")
    head = json.dumps({"verb": verb, "args": args or {}, "nsecrets": len(secrets)},
                      separators=(",", ":")).encode() + b"\n"
    return head + b"".join(_LEN.pack(len(s)) + s for s in secrets)


def decode_request(st: Stream) -> tuple[str, dict, list[bytearray]]:
    try:
        obj = json.loads(st.line())
    except ValueError:
        raise ProtocolError("bad json")
    if not isinstance(obj, dict) or not isinstance(obj.get("verb"), str) or not isinstance(obj.get("args", {}), dict):
        raise ProtocolError("bad request")
    ns = obj.get("nsecrets", 0)
    if ns not in (0, 1, 2):
        raise ProtocolError("bad nsecrets")
    secrets = [bytearray(st.prefixed(MAX_SECRET)) for _ in range(ns)]
    return obj["verb"], obj.get("args", {}), secrets


def encode_reply(code: int, data: Optional[dict] = None, tool_exit_code: Optional[int] = None,
                 blob: Optional[bytes] = None) -> bytes:
    line = json.dumps({"code": code, "tool_exit_code": tool_exit_code, "detail": DETAIL.get(code, "Error."),
                       "data": data or {}, "blob": blob is not None}, separators=(",", ":")).encode() + b"\n"
    return line + (_LEN.pack(len(blob)) + blob if blob is not None else b"")


def decode_reply(st: Stream) -> tuple[dict, Optional[bytes]]:
    try:
        obj = json.loads(st.line())
    except ValueError:
        raise ProtocolError("bad json")
    blob = st.prefixed(MAX_BLOB) if obj.get("blob") else None
    return obj, blob
