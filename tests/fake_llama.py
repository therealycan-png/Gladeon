"""A tiny fake llama-server for tests (chat stream, tokenize, embeddings, health)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeLlama:
    def __init__(self, key="k"):
        self.key = key
        self.replies = []          # list of (pieces, delay_per_piece)
        self.requests = []
        self.cancelled = 0
        self.embed_fail = set()
        self.embed_mode = "len"
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _auth(self):
                n = int(self.headers.get("Content-Length", 0) or 0)
                self._body = self.rfile.read(n) if n else b""
                if self.headers.get("Authorization") != f"Bearer {outer.key}":
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                body = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if not self._auth():
                    return
                req = json.loads(self._body)
                outer.requests.append((self.path, req))
                if self.path == "/tokenize":
                    return self._json({"tokens": list(range(len(req["content"].split())))})
                if self.path == "/v1/embeddings":
                    if req["input"] in outer.embed_fail:
                        return self._json({"error": "bad input"}, 400)
                    if outer.embed_mode == "bag":
                        v = [0.0] * 26
                        for ch in req["input"].casefold():
                            if "a" <= ch <= "z":
                                v[ord(ch) - 97] += 1
                        return self._json({"data": [{"embedding": v}]})
                    return self._json({"data": [{"embedding": [1.0, 0.0, float(len(req["input"]))]}]})
                pieces, delay = outer.replies.pop(0) if outer.replies else (["ok"], 0)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for i, p in enumerate(pieces):
                        time.sleep(delay)
                        ev = {"choices": [{"delta": {"content": p},
                                           "finish_reason": "stop" if i == len(pieces) - 1 else None}]}
                        self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    outer.cancelled += 1
                self.close_connection = True

            def _json(self, obj, status=200):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
