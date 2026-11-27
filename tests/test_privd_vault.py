"""privd vault verbs against a fake System: command order, unwinds, idempotency (5.2.8, P23d-like)."""
import os
import tempfile
import unittest

from gladeon.privd.daemon import Call
from gladeon.privd.protocol import CODES
from gladeon.privd.vault import MAPPER, Vault


class FakeSys:
    def __init__(self):
        self.calls = []
        self.mapping = False
        self.is_mounted = False
        self.vid = "VID1"
        self.rc = {}            # argv[0] or "argv0 argv1" -> rc
        self.umount_fail = 0
        self.t = 0.0

    def run(self, argv, stdin=None, timeout=60):
        self.calls.append(" ".join(argv[:2]))
        key2, key1 = " ".join(argv[:2]), argv[0]
        rc = self.rc.get(key2, self.rc.get(key1, 0))
        if key2 == "cryptsetup open" and rc == 0:
            self.mapping = stdin == b"good passphrase here!!"
            if not self.mapping:
                return 2
        if key2 == "cryptsetup close" and rc == 0:
            self.mapping = False
        if key2 == "systemctl start" and rc == 0:
            self.is_mounted = True
        if key1 == "umount":
            if self.umount_fail > 0:
                self.umount_fail -= 1
                return 32
            self.is_mounted = False
        if key2 == "fsck.ext4 -p":
            self.t += 3
        return rc

    def exists(self, p):
        return self.mapping if p == MAPPER else False

    def mounted(self, where=None):
        return self.is_mounted

    def read_id(self):
        return self.vid if self.is_mounted else None

    def unit_active(self, u):
        return False

    def sleep(self, s):
        pass

    def monotonic(self):
        return self.t


def call(secret=b"good passphrase here!!", alive=True):
    return Call("vault-open", {}, [bytearray(secret)] if secret is not None else [], 1500, lambda: alive)


class VaultVerbTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.sd, self.rd = os.path.join(self.d.name, "s"), os.path.join(self.d.name, "r")
        os.makedirs(self.sd)
        os.makedirs(self.rd)
        self.sys = FakeSys()
        self.v = Vault(self.sd, self.rd, lambda: "VID1", self.sys)

    def tearDown(self):
        self.d.cleanup()

    def name(self, r):
        return CODES[r.code]

    def test_open_happy_path(self):
        r = self.v.open(call())
        self.assertEqual(r.code, 0)
        self.assertEqual(self.sys.calls, ["cryptsetup open", "fsck.ext4 -p", "systemctl start"])
        st = self.v.status().data
        self.assertTrue(st["mounted"] and st["id_ok"] and st["mapping_open"])
        self.assertEqual((st["last_close_clean"], st["fsck_last_s"]), (False, 3.0))

    def test_idempotent_open(self):
        self.v.open(call())
        self.sys.calls.clear()
        self.assertEqual(self.v.open(call(secret=None)).data, {"already_open": True})
        self.assertEqual(self.sys.calls, [])

    def test_bad_passphrase(self):
        r = self.v.open(call(b"wrong"))
        self.assertEqual((self.name(r), r.tool_exit_code), ("BAD_PASSPHRASE", 2))
        self.assertFalse(self.sys.mapping)

    def test_fsck_failure_unwinds(self):
        self.sys.rc["fsck.ext4 -p"] = 4
        r = self.v.open(call())
        self.assertEqual(self.name(r), "FSCK_FAILED")
        self.assertFalse(self.sys.mapping)
        self.assertEqual(self.sys.calls[-1], "cryptsetup close")

    def test_wrong_id_unwinds(self):
        self.sys.vid = "OTHER"
        r = self.v.open(call())
        self.assertEqual(self.name(r), "VAULT_ID_MISMATCH")
        self.assertFalse(self.sys.mapping or self.sys.is_mounted)

    def test_lock_requested_during_open(self):
        orig = self.sys.run

        def run(argv, stdin=None, timeout=60):
            rc = orig(argv, stdin, timeout)
            if argv[:2] == ["systemctl", "start"]:
                self.v.set_lock_intent(True)        # lock-intent-set arrives while open runs
            return rc
        self.sys.run = run
        r = self.v.open(call())
        self.assertEqual(self.name(r), "LOCK_REQUESTED")
        self.assertFalse(self.sys.mapping or self.sys.is_mounted)

    def test_client_gone_at_reply(self):
        r = self.v.open(call(alive=True))
        self.assertEqual(r.code, 0)
        self.v.close()
        self.sys.calls.clear()
        seq = iter([True, False])
        r = self.v.open(Call("vault-open", {}, [bytearray(b"good passphrase here!!")], 1500, lambda: next(seq)))
        self.assertEqual(self.name(r), "PRIVD_INTERNAL")
        self.assertFalse(self.sys.mapping)

    def test_half_open_is_unwound_first(self):
        self.sys.mapping = True                     # mapping open but not mounted
        r = self.v.open(call())
        self.assertEqual(r.code, 0)
        self.assertEqual(self.sys.calls[0], "cryptsetup close")

    def test_close(self):
        self.v.open(call())
        self.sys.calls.clear()
        r = self.v.close()
        self.assertEqual(r.code, 0)
        self.assertEqual(self.sys.calls, ["systemctl stop", "umount /srv/gladeon/vault", "cryptsetup close"])
        st = self.v.status().data
        self.assertTrue(st["lock_intent"] and st["last_close_clean"])
        self.assertEqual(self.v.close().code, 0)    # closing a closed vault is OK

    def test_close_busy_then_retry(self):
        self.v.open(call())
        self.sys.umount_fail = 3
        r = self.v.close(holders=lambda: ["bash"])
        self.assertEqual((self.name(r), r.data["holders"]), ("UMOUNT_BUSY", ["bash"]))
        self.assertTrue(self.sys.mapping)
        self.assertEqual(self.v.close().code, 0)

    def test_refusals(self):
        open(os.path.join(self.sd, "update_window"), "w").close()
        self.assertEqual(self.name(self.v.open(call())), "UPDATE_WINDOW_OPEN")
        os.unlink(os.path.join(self.sd, "update_window"))
        with open(os.path.join(self.sd, "rotation.json"), "w") as f:
            f.write('{"state": "erasing"}')
        self.assertEqual(self.name(self.v.open(call())), "ROTATION_IN_PROGRESS")


if __name__ == "__main__":
    unittest.main()
