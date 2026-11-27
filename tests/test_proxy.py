"""Proxy core tests: 11.2 limits and order, 11.3 cache (P17), 9.7 lockout, 6.2 public state."""
import json
import os
import tempfile
import unittest
import uuid

from gladeon.proxy.gate import ConnPolicy, check_bearer, peer_allowed
from gladeon.proxy.httpcore import (HttpError, Reader, error, read_body, read_request, response)
from gladeon.proxy.lockout import Lockout
from gladeon.proxy.reqcache import RequestCache, valid_uuid4
from gladeon.proxy.state import SubStates, lock_allowed, public_state, unlock_allowed


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def feeder(data: bytes, step=7):
    pos = [0]

    def recv(n):
        chunk = data[pos[0]:pos[0] + min(n, step)]
        pos[0] += len(chunk)
        return chunk
    return recv


def req(raw: bytes, clock=None):
    clock = clock or Clock()
    r = Reader(feeder(raw), clock() + 3, clock)
    q = read_request(r)
    return r, q


TOKEN = b"t" * 43


class HttpTest(unittest.TestCase):
    def test_good_post(self):
        body = b'{"transcript":"hi","client_epoch":1,"mode":"auto"}'
        raw = (b"POST /query HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + TOKEN +
               b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        r, q = req(raw)
        check_bearer(q, TOKEN)
        read_body(r, q)
        self.assertEqual(q.json()["transcript"], "hi")

    def assertStatus(self, raw, status, after_auth=False):
        with self.assertRaises(HttpError) as cm:
            r, q = req(raw)
            if after_auth:
                read_body(r, q)
        self.assertEqual(cm.exception.status, status)

    def test_limits(self):
        self.assertStatus(b"GET /status HTTP/1.1\r\nX: " + b"a" * 9000 + b"\r\n\r\n", 413)
        self.assertStatus(b"POST /q HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n", 400)
        self.assertStatus(b"GET /status HTTP/1.1\r\nOrigin: http://evil\r\n\r\n", 403)
        self.assertStatus(b"GET /status HTTP/1.1\r\nSec-Fetch-Site: cross-site\r\n\r\n", 403)
        self.assertStatus(b"POST /q HTTP/1.1\r\nContent-Length: 70000\r\n\r\n", 413, after_auth=True)
        self.assertStatus(b"POST /q HTTP/1.1\r\n\r\n", 400, after_auth=True)
        self.assertStatus(b"POST /q HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx", 400)
        self.assertStatus(b"PUT /q HTTP/1.1\r\n\r\n", 400)
        self.assertStatus(b"GET /q HTTP/1.1\r\n folded\r\n\r\n", 400)

    def test_deadline(self):
        clock = Clock()

        def slow(n):
            clock.t += 1.1
            return b"G"
        r = Reader(slow, clock() + 3, clock)
        with self.assertRaises(HttpError) as cm:
            read_request(r)
        self.assertEqual(cm.exception.status, 504)

    def test_bearer(self):
        _, q = req(b"GET /status HTTP/1.1\r\nAuthorization: Bearer wrong\r\n\r\n")
        with self.assertRaises(HttpError) as cm:
            check_bearer(q, TOKEN)
        self.assertEqual(cm.exception.status, 401)
        _, q = req(b"GET /status HTTP/1.1\r\n\r\n")
        self.assertRaises(HttpError, check_bearer, q, TOKEN)

    def test_body_not_read_before_auth(self):
        # an unauthenticated POST with a huge Content-Length fails at the bearer check, body untouched
        r, q = req(b"POST /q HTTP/1.1\r\nContent-Length: 999999\r\n\r\n")
        self.assertRaises(HttpError, check_bearer, q, TOKEN)
        self.assertEqual(q.body, b"")

    def test_error_body_fixed(self):
        raw = error(401, "UNAUTHORIZED")
        body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        self.assertEqual(body, {"schema": 2, "error": "UNAUTHORIZED", "detail": "Missing or wrong token."})
        self.assertIn(b"Connection: close", raw)
        self.assertIn(b"Cache-Control: no-store", response(200, {}))

    def test_peer_and_keepalive(self):
        self.assertTrue(peer_allowed("192.168.1.5", frozenset({"192.168.1.5"})))
        self.assertFalse(peer_allowed("192.168.1.6", frozenset({"192.168.1.5"})))
        p = ConnPolicy()
        self.assertFalse(p.after(200, authenticated=False))
        p = ConnPolicy()
        self.assertTrue(p.after(200, True))
        self.assertFalse(p.after(401, True))
        p = ConnPolicy()
        results = [p.after(200, True) for _ in range(20)]
        self.assertTrue(all(results[:19]) and not results[19])


class CacheTest(unittest.TestCase):
    def test_uuid4(self):
        self.assertTrue(valid_uuid4(str(uuid.uuid4())))
        self.assertFalse(valid_uuid4(str(uuid.uuid1())))
        self.assertFalse(valid_uuid4("not-a-uuid"))
        self.assertFalse(valid_uuid4(str(uuid.uuid4()).upper() + "x"))

    def test_p17_repeat_during_and_after(self):
        clk = Clock()
        c = RequestCache(clk)
        rid = str(uuid.uuid4())
        self.assertTrue(c.begin(rid))
        self.assertFalse(c.begin(rid))
        self.assertEqual(c.lookup(rid).state, "running")        # → 202
        c.finish(rid, 200, {"display_text": "x"}, route="GENERAL_CHAT")
        self.assertEqual(c.lookup(rid).payload, {"display_text": "x"})   # → cached
        clk.t += 601
        self.assertIsNone(c.lookup(rid))

    def test_unlock_never_cached_and_brain_cleared(self):
        c = RequestCache(Clock())
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        c.begin(a, no_cache=True)
        c.finish(a, 202, {"ok": 1})
        self.assertIsNone(c.lookup(a))
        c.begin(b)
        c.finish(b, 200, {"display_text": "secret"}, route="BRAIN_RAG")
        c.replace_brain(lambda e: {"display_text": "locked"})
        self.assertEqual(c.lookup(b).payload["display_text"], "locked")
        c.clear_brain()
        self.assertIsNone(c.lookup(b))

    def test_max_entries(self):
        c = RequestCache(Clock())
        ids = [str(uuid.uuid4()) for _ in range(70)]
        for i in ids:
            c.begin(i)
            c.finish(i, 200, {})
        self.assertEqual(len(c), 64)
        self.assertIsNone(c.lookup(ids[0]))


class LockoutTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.d.name, "unlock_lockout.json")

    def tearDown(self):
        self.d.cleanup()

    def test_doubling_and_persistence(self):
        clk = Clock()
        lo = Lockout(self.p, clk)
        for _ in range(4):
            lo.failure()
            self.assertFalse(lo.blocked())
        lo.failure()
        self.assertAlmostEqual(lo.remaining(), 60)
        clk.t += 61
        lo.failure()
        self.assertAlmostEqual(lo.remaining(), 120)
        clk.t += 30
        # restart: remaining time re-armed in full (from the last save) on a new monotonic clock
        lo2 = Lockout(self.p, Clock(5.0))
        self.assertAlmostEqual(lo2.remaining(), 120)
        self.assertEqual(lo2.step, 2)
        for _ in range(10):
            lo2.clock.t += 10_000
            lo2.failure()
        self.assertEqual(lo2.remaining(), 3600)
        lo2.success()
        self.assertFalse(Lockout(self.p, Clock()).blocked())
        self.assertEqual(oct(os.stat(self.p).st_mode & 0o777), "0o600")

    def test_unreadable_state(self):
        with open(self.p, "w") as f:
            f.write("{garbage")
        lo = Lockout(self.p, Clock())
        self.assertAlmostEqual(lo.remaining(), 60)
        self.assertEqual(lo.warning, "LOCKOUT_STATE_RESET")


class StateTest(unittest.TestCase):
    def test_order(self):
        s = SubStates()
        self.assertEqual(public_state(s), "BOOTING")
        s.boot = "RUNNING"
        self.assertEqual(public_state(s), "LOADING_ENGINE")
        s.engines["quick"] = "READY"
        self.assertEqual(public_state(s), "VAULT_LOCKED_PUBLIC_ONLY")
        s.vault = "UNLOCKING"
        self.assertEqual(public_state(s), "VAULT_UNLOCKING")
        s.maintenance = True
        self.assertEqual(public_state(s), "MAINTENANCE_BUSY")
        s.maintenance = False
        s.vault = "READY"
        self.assertEqual(public_state(s), "FULL_READY")
        s.vault = "LOCK_FAILED"
        self.assertEqual(public_state(s), "VAULT_LOCK_FAILED")
        s.boot = "DEGRADED"
        self.assertEqual(public_state(s), "DEGRADED")

    def test_unlock_lock_rules(self):
        s = SubStates(boot="RUNNING")
        self.assertEqual(unlock_allowed(s), (True, None))
        s.vault = "RECOVERING"
        self.assertEqual(unlock_allowed(s), (False, 409))
        self.assertTrue(lock_allowed(s))
        s.vault = "LOCKED"
        self.assertFalse(lock_allowed(s))


if __name__ == "__main__":
    unittest.main()
