"""privd server: peer check, per-verb ACL, verb lock and floor mutex (5.2.3-5.2.7)."""
from __future__ import annotations

import logging
import os
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from .protocol import BY_NAME, ProtocolError, Stream, decode_request, encode_reply

log = logging.getLogger("gladeon.privd")

PROXY_VERBS = frozenset({
    "vault-status", "vault-open", "vault-close", "lock-intent-set", "set-time", "clock-floor-tick",
    "system-stopping", "poweroff", "reboot", "unit-start", "unit-stop", "unit-restart", "selfcheck"})
ROOT_VERBS = frozenset({
    "vault-setup", "vault-addkey", "vault-erase", "vault-fsck", "rotation-mark-copy", "rotation-compare",
    "rotation-versions-import", "rotation-finish", "rotation-abort", "header-backup", "offline-copy-start",
    "offline-copy-abort", "offline-copy-verify", "offline-copy-export", "versions-list", "versions-export",
    "files-list", "set-time-force", "clock-budget-reset", "clock-floor-set", "update-window-set",
    "update-window-clear", "sync-device-set"})
LOCK_FREE = frozenset({"vault-status", "system-stopping", "selfcheck", "lock-intent-set"})
FLOOR_ONLY = frozenset({"clock-floor-tick", "set-time", "set-time-force", "clock-floor-set", "clock-budget-reset"})
BUSY_FAST = frozenset({"vault-open", "vault-close", "unit-start", "unit-stop", "unit-restart", "poweroff", "reboot"})
UNITS = frozenset({"llm-quick", "llm-deep", "llm-embed", "sync", "wifi-tune"})
SOCKET_TIMEOUT_S = 5.0


@dataclass
class Call:
    verb: str
    args: dict
    secrets: list            # list[bytearray]; wiped after the verb
    uid: int
    client_alive: Callable[[], bool]


@dataclass
class Result:
    code: int = 0
    data: Optional[dict] = None
    tool_exit_code: Optional[int] = None
    blob: Optional[bytes] = None


Verb = Callable[[Call], Result]


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def allowed(verb: str, uid: int, gladeon_uid: int, args: dict) -> bool:
    if verb in ROOT_VERBS:
        return uid == 0
    if verb in PROXY_VERBS:
        if verb.startswith("unit-") and args.get("unit") not in UNITS:
            return False
        if verb in ("poweroff", "reboot") and args.get("force") and uid != 0:
            return False
        return uid in (0, gladeon_uid)
    return False


class Privd:
    def __init__(self, path: str, gladeon_uid: int, verbs: dict[str, Verb],
                 uid_of: Callable[[socket.socket], int] = peer_uid):
        self.path = path
        self.gladeon_uid = gladeon_uid
        self.verbs = verbs
        self.uid_of = uid_of
        self.verb_lock = threading.Lock()
        self.floor_mutex = threading.Lock()
        self.lock_holder: Optional[str] = None
        self.stop = threading.Event()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        os.chmod(path, 0o660)
        self.sock.listen(16)
        self.sock.settimeout(SOCKET_TIMEOUT_S)
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._accept, name="privd-accept", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        if self.thread:
            self.thread.join(timeout=SOCKET_TIMEOUT_S + 1)

    def _accept(self) -> None:
        while not self.stop.is_set():
            try:
                c, _ = self.sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _client(self, c: socket.socket) -> None:
        try:
            try:
                uid = self.uid_of(c)
            except OSError:
                return
            if uid not in (0, self.gladeon_uid):
                return                                     # close without reply, before any lock
            c.settimeout(SOCKET_TIMEOUT_S)
            try:
                verb, args, secrets = decode_request(Stream(c))
            except (ProtocolError, OSError, ValueError):
                return
            try:
                res = self._dispatch(Call(verb, args, secrets, uid, lambda: _alive(c)))
            finally:
                for s in secrets:                          # best effort; Python cannot fully wipe
                    s[:] = b"\0" * len(s)
            try:
                c.sendall(encode_reply(res.code, res.data, res.tool_exit_code, res.blob))
            except OSError:
                pass
        finally:
            c.close()

    def _dispatch(self, call: Call) -> Result:
        if not allowed(call.verb, call.uid, self.gladeon_uid, call.args) or call.verb not in self.verbs:
            return Result(BY_NAME["NOT_PERMITTED"])
        fn = self.verbs[call.verb]
        try:
            if call.verb in LOCK_FREE:
                return fn(call)
            if call.verb in FLOOR_ONLY:
                with self.floor_mutex:
                    return fn(call)
            if call.verb in BUSY_FAST and call.uid != 0:
                if not self.verb_lock.acquire(blocking=False):
                    return Result(BY_NAME["PRIVD_BUSY"], {"lock_holder": self.lock_holder})
            else:
                # root verbs wait, but give up if the client goes away (5.2.8 queued vault-open)
                while not self.verb_lock.acquire(timeout=1.0):
                    if not call.client_alive():
                        return Result(BY_NAME["PRIVD_INTERNAL"])
            try:
                self.lock_holder = call.verb
                return fn(call)
            finally:
                self.lock_holder = None
                self.verb_lock.release()
        except Exception as e:
            log.error("verb %s failed: %s", call.verb, type(e).__name__)
            return Result(BY_NAME["PRIVD_INTERNAL"])


def _alive(c: socket.socket) -> bool:
    """True if the client has not closed its end (a zero-byte peek means EOF)."""
    try:
        c.setblocking(False)
        try:
            data = c.recv(1, socket.MSG_PEEK)
            return data != b""
        except BlockingIOError:
            return True
        finally:
            c.setblocking(True)
            c.settimeout(SOCKET_TIMEOUT_S)
    except OSError:
        return False
