"""Proxy server loop (11.2): accept thread, peer allowlist, bounded queue, TLS in workers."""
from __future__ import annotations

import logging
import queue
import socket
import ssl
import threading
import time
from typing import Callable, Optional

from .gate import ACCEPT_QUEUE, KEEPALIVE_IDLE_S, WORKERS, ConnPolicy, check_bearer, peer_allowed
from .httpcore import REQUEST_DEADLINE_S, HttpError, Reader, error, read_body, read_request, response

log = logging.getLogger("gladeon.proxy")
LOOP_S = 5.0


def tls_context(cert: str, key: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    ctx.options |= ssl.OP_NO_COMPRESSION
    return ctx


Handler = Callable[["object"], tuple]   # Request -> (status, payload)


class Server:
    def __init__(self, host: str, port: int, ctx: ssl.SSLContext, token: bytes, phone_ips: frozenset,
                 handler: Handler, clock: Callable[[], float] = time.monotonic):
        self.ctx = ctx
        self.token = token
        self.phone_ips = phone_ips
        self.handler = handler
        self.clock = clock
        self.q: "queue.Queue[tuple[socket.socket, float]]" = queue.Queue(maxsize=ACCEPT_QUEUE)
        self.stop = threading.Event()
        self.sock = socket.create_server((host, port), reuse_port=False)
        self.sock.settimeout(LOOP_S)
        self.port = self.sock.getsockname()[1]
        self.threads: list[threading.Thread] = []

    # ---- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        t = threading.Thread(target=self._accept, name="accept", daemon=True)
        t.start()
        self.threads.append(t)
        for i in range(WORKERS):
            w = threading.Thread(target=self._worker, name=f"worker-{i}", daemon=True)
            w.start()
            self.threads.append(w)

    def close(self) -> None:
        self.stop.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)     # wakes a blocked accept() on Linux
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        for _ in range(WORKERS):
            try:
                self.q.put_nowait(None)
            except queue.Full:
                pass
        for t in self.threads:
            t.join(timeout=LOOP_S + 1)

    # ---- accept ----------------------------------------------------------------------------
    def _accept(self) -> None:
        while not self.stop.is_set():
            try:
                conn, addr = self.sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                if self.stop.is_set():
                    return
                continue
            if not peer_allowed(addr[0], self.phone_ips):
                _close(conn)
                continue
            try:
                self.q.put_nowait((conn, self.clock()))
            except queue.Full:
                _close(conn)                       # full queue: close before TLS

    # ---- workers ---------------------------------------------------------------------------
    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                item = self.q.get(timeout=LOOP_S)
            except queue.Empty:
                continue
            if item is None:
                return
            conn, accepted = item
            try:
                self._serve(conn, accepted)
            except Exception as e:                 # never log messages; they may carry vault data
                log.warning("worker error %s", type(e).__name__)
            finally:
                _close(conn)

    def _serve(self, raw: socket.socket, accepted: float) -> None:
        deadline = accepted + REQUEST_DEADLINE_S
        raw.settimeout(max(0.05, min(LOOP_S, deadline - self.clock())))
        try:
            conn = self.ctx.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            return
        try:
            self._session(conn, deadline)
        finally:
            _close(conn)

    def _session(self, conn: ssl.SSLSocket, deadline: float) -> None:
        policy = ConnPolicy()
        first = True
        while not self.stop.is_set():
            if not first:
                # keep-alive idle wait: up to 5 s for the next request's first byte
                conn.settimeout(KEEPALIVE_IDLE_S)
                try:
                    peek = conn.recv(1)
                except (socket.timeout, TimeoutError, ssl.SSLError, OSError):
                    return
                if not peek:
                    return
                start_buf = peek
                deadline = self.clock() + REQUEST_DEADLINE_S
            else:
                start_buf = b""
            first = False

            def recv(n, _c=conn, _d=lambda: deadline):
                _c.settimeout(max(0.05, min(LOOP_S, _d() - self.clock())))
                return _c.recv(n)
            reader = Reader(recv, deadline, self.clock)
            reader.buf = start_buf
            authed = False
            try:
                req = read_request(reader)
                check_bearer(req, self.token)
                authed = True
                read_body(reader, req)
                status, payload = self.handler(req)
            except HttpError as e:
                _send(conn, error(e.status, e.code))
                return
            except (ssl.SSLError, OSError):
                return
            keep = policy.after(status, authed)
            if not _send(conn, response(status, payload, keep_alive=keep)) or not keep:
                return


def _send(conn, data: bytes) -> bool:
    try:
        conn.sendall(data)
        return True
    except (OSError, ssl.SSLError):
        return False


def _close(s) -> None:
    try:
        s.close()
    except OSError:
        pass
