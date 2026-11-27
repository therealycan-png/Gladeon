"""Proxy server over real TLS on loopback with a fake controller."""
import http.client
import json
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import uuid

from gladeon.proxy.app import App
from gladeon.proxy.server import Server, tls_context

TOKEN = "x" * 43


class FakeCtl:
    def __init__(self):
        self.queries = 0
        self.slow = 0.0
        self.touches = 0

    def status(self):
        return {"schema": 2, "state": "FULL_READY"}

    def preflight(self):
        return []

    def query_blocked(self, mode):
        return (503, "DEGRADED") if mode == "deep" and self.slow < 0 else None

    def run_query(self, transcript, mode, epoch, early=None):
        self.queries += 1
        time.sleep(self.slow)
        return {"schema": 2, "route": "GENERAL_CHAT", "display_text": transcript.upper(), "tts_text": transcript}

    def unlock(self, pw):
        return 202, {"schema": 2, "state": "VAULT_UNLOCKING"}

    def lock(self):
        return 202, {"schema": 2}

    def system(self, action):
        return 202, {"schema": 2, "action": action}

    def engine_restart(self, engine):
        return 202, {"schema": 2}

    def touch(self):
        self.touches += 1

    def processing_reply(self):
        return {"status": "PROCESSING", "display_text": "Working.", "tts_text": "Working.", "retry_window_s": 210}


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.TemporaryDirectory()
        cls.cert, cls.key = os.path.join(cls.d.name, "c.pem"), os.path.join(cls.d.name, "k.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-days", "1", "-subj", "/CN=GladeonLocal", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", cls.key, "-out", cls.cert], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.d.cleanup()

    def setUp(self):
        self.ctl = FakeCtl()
        self.app = App(self.ctl, sync_wait_s=0.5)
        self.srv = Server("127.0.0.1", 0, tls_context(self.cert, self.key), TOKEN.encode(),
                          frozenset({"127.0.0.1"}), self.app)
        self.srv.start()
        self.cctx = ssl.create_default_context(cafile=self.cert)

    def tearDown(self):
        self.srv.close()

    def conn(self):
        return http.client.HTTPSConnection("127.0.0.1", self.srv.port, context=self.cctx, timeout=5)

    def call(self, c, method, path, body=None, token=TOKEN, headers=None):
        h = {"Authorization": f"Bearer {token}"} if token else {}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Length"] = str(len(data))
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}"), r.getheader("Connection")

    def test_status_and_keepalive(self):
        c = self.conn()
        s, b, conn_h = self.call(c, "GET", "/status")
        self.assertEqual((s, b["state"], conn_h), (200, "FULL_READY", "keep-alive"))
        s, b, _ = self.call(c, "GET", "/status")       # same connection
        self.assertEqual(s, 200)
        c.close()

    def test_unauthorized_closes(self):
        c = self.conn()
        s, b, conn_h = self.call(c, "GET", "/status", token="wrong")
        self.assertEqual((s, b["error"], conn_h), (401, "UNAUTHORIZED", "close"))
        c.close()

    def test_origin_forbidden_and_404(self):
        c = self.conn()
        self.assertEqual(self.call(c, "GET", "/status", headers={"Origin": "http://x"})[0], 403)
        c.close()
        c = self.conn()
        self.assertEqual(self.call(c, "GET", "/nope")[0], 404)
        c.close()

    def test_query_dedup_and_result(self):
        rid = str(uuid.uuid4())
        c = self.conn()
        s, b, _ = self.call(c, "POST", "/query", {"transcript": "hi", "client_epoch": 1, "mode": "auto"},
                            headers={"X-Request-Id": rid})
        self.assertEqual((s, b["display_text"], b["request_id"]), (200, "HI", rid))
        s, b, _ = self.call(c, "POST", "/query", {"transcript": "other", "mode": "auto"}, headers={"X-Request-Id": rid})
        self.assertEqual((s, b["display_text"]), (200, "HI"))
        self.assertEqual(self.ctl.queries, 1)
        c.close()

    def test_p17_202_while_running(self):
        self.ctl.slow = 1.5
        rid = str(uuid.uuid4())
        c = self.conn()
        s, b, _ = self.call(c, "POST", "/query", {"transcript": "slow"}, headers={"X-Request-Id": rid})
        self.assertEqual((s, b["status"]), (202, "PROCESSING"))
        s, _, _ = self.call(c, "GET", f"/result/{rid}")
        self.assertEqual(s, 202)
        time.sleep(1.3)
        s, b, _ = self.call(c, "GET", f"/result/{rid}")
        self.assertEqual((s, b["display_text"]), (200, "SLOW"))
        c.close()

    def test_blocked_query_not_registered(self):
        from gladeon.proxy.httpcore import Request
        ctl = FakeCtl()
        ctl.query_blocked = lambda mode: (503, "LOADING")
        app = App(ctl, sync_wait_s=0.5)
        rid = str(uuid.uuid4())
        req = Request("POST", "/query", {"x-request-id": rid}, b'{"transcript": "hi"}')
        self.assertEqual(app(req)[0], 503)
        self.assertIsNone(app.cache.lookup(rid))
        ctl.query_blocked = lambda mode: None                # recovered: the retry runs normally
        self.assertEqual(app(req)[0], 200)

    def test_app_shares_the_given_cache(self):
        from gladeon.proxy.reqcache import RequestCache
        c = RequestCache()
        self.assertIs(App(FakeCtl(), c).cache, c)          # regression: empty cache is falsy

    def test_bad_query(self):
        c = self.conn()
        self.assertEqual(self.call(c, "POST", "/query", {"transcript": "x"}, headers={"X-Request-Id": "nope"})[0], 400)
        c.close()
        c = self.conn()
        self.assertEqual(self.call(c, "POST", "/system/shutdown", {"confirm": "yes"})[0], 400)
        c.close()
        c = self.conn()
        s, b, _ = self.call(c, "POST", "/system/shutdown", {"confirm": "SHUTDOWN"})
        self.assertEqual((s, b["action"]), (202, "shutdown"))
        c.close()

    def test_peer_not_allowed(self):
        self.srv.phone_ips = frozenset({"10.9.9.9"})
        c = self.conn()
        with self.assertRaises((ConnectionError, ssl.SSLError, OSError, http.client.HTTPException)):
            self.call(c, "GET", "/status")
        c.close()

    def test_slow_headers_timeout(self):
        s = socket.create_connection(("127.0.0.1", self.srv.port))
        t = self.cctx.wrap_socket(s, server_hostname="127.0.0.1")
        t.sendall(b"GET /status HTTP/1.1\r\n")
        t.settimeout(6)
        start = time.monotonic()
        data = t.recv(4096)
        self.assertLess(time.monotonic() - start, 4.5)
        self.assertTrue(data == b"" or b"504" in data)
        t.close()


if __name__ == "__main__":
    unittest.main()
