"""Proxy-side privd client. Each call is one short-lived connection."""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Optional

from .protocol import CODES, ProtocolError, Stream, decode_reply, encode_request

DEFAULT_SOCKET = "/run/gladeon/privd.sock"


@dataclass
class Reply:
    code: int
    name: Optional[str]
    data: dict
    tool_exit_code: Optional[int]
    blob: Optional[bytes] = None

    @property
    def ok(self) -> bool:
        return self.code == 0


class PrivdClient:
    def __init__(self, path: str = DEFAULT_SOCKET, timeout_s: float = 5.0):
        self.path = path
        self.timeout_s = timeout_s

    def call(self, verb: str, args: Optional[dict] = None, secrets: tuple = (), deadline_s: float = 30.0) -> Reply:
        """`deadline_s` is the declared operation deadline; reads loop in 5 s slices (7.2)."""
        import time
        end = time.monotonic() + deadline_s
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(self.timeout_s)
            try:
                s.connect(self.path)
                s.sendall(encode_request(verb, args, secrets))
            except OSError:
                return Reply(19, CODES[19], {}, None)
            st = Stream(s)
            while True:
                try:
                    obj, blob = decode_reply(st)
                    break
                except (socket.timeout, TimeoutError):
                    if time.monotonic() >= end:
                        return Reply(19, CODES[19], {"timeout": True}, None)
                except (ProtocolError, OSError):
                    return Reply(19, CODES[19], {}, None)
            code = int(obj.get("code", 19))
            return Reply(code, CODES.get(code), obj.get("data") or {}, obj.get("tool_exit_code"), blob)
        finally:
            s.close()
