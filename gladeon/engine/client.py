"""HTTP client for a local llama-server (8.4, 8.6). Standard library only."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

PORTS = {"quick": 9931, "deep": 9932, "embed": 9933}
READ_LOOP_S = 5.0          # 7.2: no blocking read longer than 5 s

TEMPERATURE = {"QUICK": 0.3, "DEEP": 0.4, "BRAIN": 0.2}
MAX_TOKENS = {"QUICK": 160, "DEEP": 450}


class EngineError(Exception):
    """Transport-level failure (connection refused, reset, bad HTTP status)."""


class DeadlineExceeded(EngineError):
    def __init__(self, where: str, partial: str = ""):
        super().__init__(where)
        self.partial = partial


class Cancelled(EngineError):
    pass


@dataclass
class ChatParams:
    mode: str                         # QUICK | DEEP
    brain: bool = False
    max_tokens: Optional[int] = None
    stop: Sequence[str] = ()
    supports_enable_thinking: bool = False
    seed: Optional[int] = None        # model gate only
    temperature_bump: float = 0.0

    def body(self, messages: list[dict]) -> dict:
        b = {
            "messages": messages,
            "stream": True,
            "cache_prompt": True,
            "temperature": (TEMPERATURE["BRAIN"] if self.brain else TEMPERATURE[self.mode]) + self.temperature_bump,
            "max_tokens": self.max_tokens or MAX_TOKENS[self.mode],
            "repeat_penalty": 1.0 if self.brain else 1.1,
        }
        if self.stop:
            b["stop"] = list(self.stop)
        if self.supports_enable_thinking:
            b["chat_template_kwargs"] = {"enable_thinking": False}
        if self.seed is not None:
            b["seed"] = self.seed
        return b


@dataclass
class ChatResult:
    text: str
    finish_reason: Optional[str]
    tokens: int
    elapsed_s: float


class Engine:
    def __init__(self, name: str, api_key: str, host: str = "127.0.0.1", port: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.name = name
        self.host = host
        self.port = port or PORTS[name]
        self.api_key = api_key
        self.clock = clock
        self._local = threading.local()      # 11.7.3: one HTTPConnection per worker per engine

    # ---- plumbing --------------------------------------------------------------------------
    def _conn(self) -> http.client.HTTPConnection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = http.client.HTTPConnection(self.host, self.port, timeout=READ_LOOP_S)
            self._local.conn = c
        return c

    def _drop(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            try:
                c.close()
            finally:
                self._local.conn = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _post_json(self, path: str, payload: dict, deadline: float) -> dict:
        if self.clock() >= deadline:
            raise DeadlineExceeded(path)
        c = self._conn()
        try:
            c.timeout = max(0.05, min(READ_LOOP_S, deadline - self.clock()))
            c.request("POST", path, json.dumps(payload), self._headers())
            r = c.getresponse()
            data = r.read()
        except (OSError, http.client.HTTPException) as e:
            self._drop()
            if self.clock() >= deadline:
                raise DeadlineExceeded(path) from None
            raise EngineError(type(e).__name__) from None
        if r.status != 200:
            raise EngineError(f"HTTP {r.status}")
        try:
            return json.loads(data)
        except ValueError:
            raise EngineError("bad json") from None

    def _get_json(self, path: str, timeout: float = 2.0) -> dict:
        c = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            c.request("GET", path, headers=self._headers())
            r = c.getresponse()
            data = r.read()
            if r.status != 200:
                raise EngineError(f"HTTP {r.status}")
            return json.loads(data)
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise EngineError(type(e).__name__) from None
        finally:
            c.close()

    # ---- API -------------------------------------------------------------------------------
    def health(self) -> bool:
        try:
            return self._get_json("/health").get("status") == "ok"
        except EngineError:
            return False

    def tokenize(self, text: str, deadline: float) -> int:
        return len(self._post_json("/tokenize", {"content": text}, deadline).get("tokens", []))

    def embed(self, text: str, deadline: float) -> Optional[list[float]]:
        """Returns the vector, or None for a per-input error from a READY engine (13.4.8 'failed').
        Transport errors and timeouts raise, so the chunk stays pending."""
        try:
            d = self._post_json("/v1/embeddings", {"input": text}, deadline)
        except EngineError as e:
            if str(e).startswith("HTTP 4"):
                return None
            raise
        try:
            v = d["data"][0]["embedding"]
            return [float(x) for x in v]
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def chat(self, messages: list[dict], params: ChatParams, deadline: float,
             cancel: Optional[threading.Event] = None,
             on_text: Optional[Callable[[str], bool]] = None) -> ChatResult:
        """Streams and aggregates. Closing the connection cancels the server task (8.6.1).
        `on_text(accumulated)` may return False to stop early (12.2 abstention prefix rule).
        Reads use our own buffer over the raw socket with select() in <= 5 s slices, so a long
        silence (DEEP prompt processing) never poisons the stream the way a timed-out file object does."""
        start = self.clock()
        if start >= deadline:
            raise DeadlineExceeded("chat")
        parts: list[str] = []
        finish = None
        ntok = 0
        body = json.dumps(params.body(messages)).encode()
        try:
            sock = socket.create_connection((self.host, self.port), timeout=min(READ_LOOP_S, deadline - start))
        except OSError as e:
            raise EngineError(type(e).__name__) from None
        rd = _Stream(sock, deadline, cancel, self.clock, lambda: "".join(parts))
        try:
            head = (f"POST /v1/chat/completions HTTP/1.1\r\nHost: {self.host}:{self.port}\r\n"
                    f"Authorization: Bearer {self.api_key}\r\nContent-Type: application/json\r\n"
                    f"Accept: text/event-stream\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n")
            try:
                sock.sendall(head.encode() + body)
            except OSError as e:
                raise EngineError(type(e).__name__) from None
            status, headers = rd.read_head()
            if status != 200:
                raise EngineError(f"HTTP {status}")
            cl = headers.get("content-length")
            for line in rd.lines(chunked="chunked" in headers.get("transfer-encoding", "").lower(),
                                 length=int(cl) if cl and cl.isdigit() else None):
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                try:
                    ev = json.loads(data)
                    ch = ev["choices"][0]
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
                delta = (ch.get("delta") or {}).get("content")
                if delta:
                    parts.append(delta)
                    ntok += 1
                    if on_text is not None and on_text("".join(parts)) is False:
                        finish = "client_stop"
                        break
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
        finally:
            try:
                sock.close()
            except OSError:
                pass
        return ChatResult("".join(parts), finish, ntok, self.clock() - start)


class _Stream:
    """Line reader over a raw socket: select() in <= 5 s slices, deadline and cancel checked each slice."""

    MAX_LINE = 1 << 20

    def __init__(self, sock: socket.socket, deadline: float, cancel, clock, partial):
        self.sock, self.deadline, self.cancel, self.clock, self.partial = sock, deadline, cancel, clock, partial
        self.buf = b""
        self.eof = False

    def _fill(self) -> bool:
        import select
        while True:
            if self.cancel is not None and self.cancel.is_set():
                raise Cancelled("chat")
            left = self.deadline - self.clock()
            if left <= 0:
                raise DeadlineExceeded("chat", self.partial())
            try:
                r, _, _ = select.select([self.sock], [], [], max(0.01, min(READ_LOOP_S, left)))
            except (OSError, ValueError) as e:
                raise EngineError(type(e).__name__) from None
            if not r:
                continue                     # quiet slice: loop re-checks deadline and cancel
            try:
                chunk = self.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError as e:
                raise EngineError(type(e).__name__) from None
            if not chunk:
                self.eof = True
                return False
            self.buf += chunk
            return True

    def _line(self) -> Optional[bytes]:
        while b"\n" not in self.buf:
            if len(self.buf) > self.MAX_LINE:
                raise EngineError("line too long")
            if not self._fill():
                if self.buf:
                    out, self.buf = self.buf, b""
                    return out
                return None
        line, self.buf = self.buf.split(b"\n", 1)
        return line + b"\n"

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            if not self._fill():
                raise EngineError("short read")
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def read_head(self) -> tuple[int, dict]:
        first = self._line()
        if not first:
            raise EngineError("no response")
        try:
            status = int(first.split()[1])
        except (IndexError, ValueError):
            raise EngineError("bad status line") from None
        headers = {}
        while True:
            line = self._line()
            if line is None:
                raise EngineError("eof in headers")
            line = line.strip()
            if not line:
                return status, headers
            k, _, v = line.decode("latin-1").partition(":")
            headers[k.strip().lower()] = v.strip()

    def lines(self, chunked: bool, length: Optional[int] = None):
        if not chunked and length is not None:       # a plain body: exactly Content-Length bytes
            left = length
            while left > 0:
                while b"\n" not in self.buf[:left]:
                    if len(self.buf) >= left:
                        break
                    if len(self.buf) > self.MAX_LINE:
                        raise EngineError("line too long")
                    if not self._fill():             # early EOF: hand over what arrived
                        if self.buf:
                            out, self.buf = self.buf[:left], b""
                            yield out
                        return
                seg = self.buf[:left]
                cut = seg.find(b"\n")
                n = cut + 1 if cut >= 0 else len(seg)
                out, self.buf = self.buf[:n], self.buf[n:]
                left -= n
                yield out
            return
        if not chunked:
            while True:
                ln = self._line()
                if ln is None:
                    return
                yield ln
        # decode chunked transfer encoding into a body buffer, then split lines from it
        body = b""
        while True:
            size_line = self._line()
            if size_line is None:
                if body:
                    yield body                  # early EOF: hand over what arrived
                return
            try:
                size = int(size_line.split(b";")[0].strip() or b"0", 16)
            except ValueError:
                raise EngineError("bad chunk size") from None
            if size == 0:
                if body:
                    yield body
                return
            if size > self.MAX_LINE:
                raise EngineError("chunk too large")
            body += self._exact(size)
            self._exact(2)                   # CRLF after each chunk
            while b"\n" in body:
                ln, body = body.split(b"\n", 1)
                yield ln + b"\n"
            if len(body) > self.MAX_LINE:
                raise EngineError("line too long")
