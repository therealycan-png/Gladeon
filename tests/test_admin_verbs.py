"""gladeon-admin key-add / key-remove / phone-replace, staged under a temp root (P37 file side)."""
import io
import json
import os
import tempfile
import unittest

from gladeon.admin.main import KEYS_DB, NET_CONF, SSHD_KEYS, DROPBEAR_KEYS, Admin
from gladeon.admin import keys as K
from tests.test_keys import fake_key

DEV = "-".join(["ABCDEFG"] * 8)


class AdminVerbsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.a = Admin(self.d.name)
        self.a.write(NET_CONF, json.dumps({"phone_ip": "192.168.1.20", "admin_ip": "192.168.1.30",
                                           "pc_ip_eth": "192.168.1.10/24", "pc_ip_wlan": "192.168.1.11/24"}))
        self.a.write("/etc/gladeon/proxy.json", json.dumps({"phone_ips": ["192.168.1.20"], "port": 8443}))

    def tearDown(self):
        self.d.cleanup()

    def read(self, p):
        return self.a.read(p)

    def test_key_add_remove(self):
        self.assertEqual(self.a.key_add("phone", fake_key(b"a")), 0)
        self.assertEqual(self.a.key_add("admin", fake_key(b"b")), 0)
        self.assertTrue(K.check_dropbear(self.read(DROPBEAR_KEYS)))
        self.assertEqual(self.read(SSHD_KEYS).count("ssh-ed25519"), 2)
        self.assertIn(["update-initramfs", "-u", "-k", "all"], self.a.ran)
        fp = K.parse_pubkey("phone", fake_key(b"a")).fingerprint
        self.assertEqual(self.a.key_remove(fp), 0)
        self.assertEqual(self.read(SSHD_KEYS).count("ssh-ed25519"), 1)
        self.assertEqual(oct(os.stat(self.a.p(KEYS_DB)).st_mode & 0o777), "0o600")

    def test_p37_phone_replace(self):
        self.a.key_add("phone", fake_key(b"a"))
        self.assertEqual(self.a.phone_replace("192.168.1.25", DEV), 0)
        nft = self.read("/etc/nftables.conf")
        self.assertIn("ip saddr 192.168.1.25 tcp dport 8443 accept", nft)
        self.assertNotIn("192.168.1.20", nft)
        self.assertIn("IPAddressAllow=192.168.1.25 localhost",
                      self.read("/etc/systemd/system/gladeon-proxy.service.d/ip.conf"))
        self.assertIn("IPAddressAllow=192.168.1.25 localhost",
                      self.read("/etc/systemd/system/gladeon-sync.service.d/ip.conf"))
        self.assertIn('from="192.168.1.25"', self.read(SSHD_KEYS))
        self.assertEqual(json.loads(self.read("/etc/gladeon/proxy.json"))["phone_ips"], ["192.168.1.25"])
        cmds = [c[0] + " " + c[1] for c in self.a.ran]
        self.assertIn("systemctl restart", cmds)
        self.assertIn("privd sync-device-set", cmds)
        self.assertLess(cmds.index("privd sync-device-set"), cmds.index("systemctl restart"))
        before = {p: self.read(p) for p in ("/etc/nftables.conf", SSHD_KEYS)}
        self.assertEqual(self.a.phone_replace("192.168.1.25", DEV), 0)     # idempotent
        self.assertEqual(before, {p: self.read(p) for p in before})

    def test_p30_token_rotate_file_side(self):
        self.a.write("/etc/gladeon/proxy.json", json.dumps({"token_file": "/etc/gladeon/api_token"}))
        self.a.write("/etc/gladeon/api_token", "old" * 14 + "x\n")
        self.assertEqual(self.a.token_rotate("/etc/gladeon/proxy.json"), 0)
        new = self.read("/etc/gladeon/api_token").strip()
        self.assertEqual(len(new), 43)
        self.assertNotIn("old", new)
        self.assertEqual(self.a.ran[-1], ["systemctl", "restart", "gladeon-proxy.service"])

    def test_bad_inputs(self):
        self.assertEqual(self.a.phone_replace("192.168.1.25", "nope"), 2)
        with self.assertRaises(ValueError):
            self.a.phone_replace("999.1.1.1", DEV)
        with self.assertRaises(ValueError):
            self.a.key_add("phone", "ssh-rsa AAAA x")


if __name__ == "__main__":
    unittest.main()
