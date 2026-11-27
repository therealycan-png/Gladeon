"""privd: protocol, peer check, ACL, locking (5.2.3-5.2.7); clock budget (P29-P29d)."""
import os
import socket
import tempfile
import threading
import time
import unittest

from gladeon.privd.client import PrivdClient
from gladeon.privd.clock import BUDGET_S, ClockState, charge, proxy_correction
from gladeon.privd.daemon import Privd, Result, allowed
from gladeon.privd.protocol import BY_NAME

GL = 1500


class PrivdTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.d.name, "privd.sock")
        self.uid = [GL]
        self.gate = threading.Event()
        self.seen = []

        def slow(call):
            self.gate.wait(5)
            return Result(0, {"done": call.verb})

        def status(call):
            return Result(0, {"lock_holder": self.p.lock_holder})

        def echo_secret(call):
            self.seen.append(bytes(call.secrets[0]))
            return Result(0, {"n": len(call.secrets)}, blob=b"BLOB" if call.args.get("blob") else None)
        self.p = Privd(self.path, GL, {"vault-open": slow, "vault-close": slow, "vault-status": status,
                                       "vault-setup": echo_secret, "lock-intent-set": status,
                                       "vault-addkey": slow},
                       uid_of=lambda s: self.uid[0])
        self.p.start()
        self.c = PrivdClient(self.path, timeout_s=2)

    def tearDown(self):
        self.gate.set()
        self.p.close()
        self.d.cleanup()

    def test_acl_table(self):
        self.assertTrue(allowed("vault-open", GL, GL, {}))
        self.assertFalse(allowed("vault-setup", GL, GL, {}))
        self.assertTrue(allowed("vault-setup", 0, GL, {}))
        self.assertTrue(allowed("unit-restart", GL, GL, {"unit": "llm-embed"}))
        self.assertFalse(allowed("unit-restart", GL, GL, {"unit": "ssh"}))
        self.assertFalse(allowed("poweroff", GL, GL, {"force": True}))
        self.assertFalse(allowed("rm-rf", 0, GL, {}))

    def test_stranger_gets_no_reply(self):
        self.uid[0] = 4242
        r = self.c.call("vault-status")
        self.assertEqual(r.name, "PRIVD_INTERNAL")

    def test_root_only_refused_for_proxy(self):
        r = self.c.call("vault-setup", secrets=(b"x" * 20,))
        self.assertEqual(r.name, "NOT_PERMITTED")

    def test_secret_and_blob_roundtrip(self):
        self.uid[0] = 0
        r = self.c.call("vault-setup", {"blob": True}, secrets=(b"pass phrase twenty+!",))
        self.assertEqual((r.code, r.data["n"], r.blob), (0, 1, b"BLOB"))
        self.assertEqual(self.seen, [b"pass phrase twenty+!"])

    def test_busy_and_lock_free(self):
        t = threading.Thread(target=lambda: self.c.call("vault-open"))
        t.start()
        time.sleep(0.3)
        r = self.c.call("vault-close")
        self.assertEqual((r.name, r.data["lock_holder"]), ("PRIVD_BUSY", "vault-open"))
        r = self.c.call("vault-status")                 # bypasses the verb lock
        self.assertEqual((r.code, r.data["lock_holder"]), (0, "vault-open"))
        self.assertEqual(self.c.call("lock-intent-set").code, 0)
        self.gate.set()
        t.join()
        self.gate.clear()

    def test_root_waits_then_gives_up_when_client_leaves(self):
        t = threading.Thread(target=lambda: self.c.call("vault-open"))
        t.start()
        time.sleep(0.3)
        self.uid[0] = 0
        s = socket.socket(socket.AF_UNIX)
        s.connect(self.path)
        from gladeon.privd.protocol import encode_request
        s.sendall(encode_request("vault-addkey"))
        s.close()                                        # client goes away while queued
        time.sleep(1.5)
        self.gate.set()
        t.join()
        self.assertIsNone(self.p.lock_holder)


class ClockTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.wall = [1_800_000_000.0]
        self.mono = [100.0]

    def tearDown(self):
        self.d.cleanup()

    def mk(self):
        def set_clock(t):
            self.wall[0] = float(t)
            return True
        return ClockState(self.d.name, lambda: self.wall[0], lambda: self.mono[0], set_clock)

    def test_charge(self):
        self.assertEqual(charge(100, 50, 80).code, "BELOW_FLOOR")
        d = charge(100, 300, 200)
        self.assertEqual((d.free, d.charged), (100, 100))
        self.assertEqual(charge(500, 400, 200).charged, 100)

    def test_p29_trusted_big_jump(self):
        tgt, warn = proxy_correction(int(self.wall[0]) + 48 * 3600, int(self.wall[0]), True, 0)
        c = self.mk()
        c.tick()
        d = c.set_time(tgt)
        self.assertEqual(d.code, "CLOCK_BUDGET_EXCEEDED")
        self.assertEqual(self.wall[0], 1_800_000_000.0)

    def test_p29b_budget_survives_reboots(self):
        start = self.wall[0]
        for _ in range(5):
            c = self.mk()                                # "reboot": fresh object, same state dir
            c.set_time(int(self.wall[0]) + 23 * 3600)
        self.assertLessEqual(self.wall[0] - start, BUDGET_S)

    def test_p29c_raise_to_floor_free(self):
        c = self.mk()
        c.tick()
        floor = c.floor
        self.wall[0] -= 3 * 86400                        # RTC 3 days behind
        c2 = self.mk()
        tgt, _ = proxy_correction(floor + 600, int(self.wall[0]), False, c2.floor)
        d = c2.set_time(tgt)
        self.assertTrue(d.ok)
        self.assertEqual((d.free, d.charged), (3 * 86400, 600))
        self.assertEqual(c2.used, 600)

    def test_p29d_forged_advisory_floor(self):
        c = self.mk()
        c.tick()
        forged_floor = int(self.wall[0]) + 365 * 86400   # proxy's advisory copy is not trusted by privd
        tgt, _ = proxy_correction(forged_floor, int(self.wall[0]), False, forged_floor)
        d = c.set_time(tgt)
        self.assertEqual(d.code, "CLOCK_BUDGET_EXCEEDED")
        self.assertEqual(c.floor, 1_800_000_000)

    def test_tick_rejects_jump_within_boot(self):
        c = self.mk()
        c.tick()
        self.wall[0] += 3600
        self.mono[0] += 600                               # wall moved 1 h in 10 min of monotonic time
        self.assertEqual(c.tick(), 1_800_000_000)
        self.mono[0] += 3000
        self.assertEqual(c.tick(), 1_800_003_600)


if __name__ == "__main__":
    unittest.main()
