import base64
import struct
import subprocess
import tempfile
import os
import shutil
import unittest

from gladeon.admin import keys as K


def fake_key(seed: bytes = b"a") -> str:
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + (seed * 32)[:32]
    return "ssh-ed25519 " + base64.b64encode(blob).decode() + " user@phone"


class KeysTest(unittest.TestCase):
    def test_parse(self):
        k = K.parse_pubkey("phone", fake_key())
        self.assertTrue(k.fingerprint.startswith("SHA256:"))
        for bad in ("ssh-rsa AAAA x", 'command="x" ' + fake_key(), "ssh-ed25519 !!!", "ssh-ed25519 AAAA"):
            with self.assertRaises(ValueError):
                K.parse_pubkey("phone", bad)
        with self.assertRaises(ValueError):
            K.parse_pubkey("root", fake_key())

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen missing")
    def test_fingerprint_matches_ssh_keygen(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "k")
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", p], check=True)
            with open(p + ".pub") as f:
                k = K.parse_pubkey("admin", f.read())
            out = subprocess.run(["ssh-keygen", "-lf", p + ".pub"], capture_output=True, text=True).stdout
            self.assertIn(k.fingerprint, out)

    def test_files(self):
        ph, ad = K.parse_pubkey("phone", fake_key(b"a")), K.parse_pubkey("admin", fake_key(b"b"))
        keys = K.add(K.add([], ph), ad)
        self.assertEqual(K.add(keys, ph), keys)                   # idempotent
        ak = K.authorized_keys(keys, "192.168.1.20", "192.168.1.30")
        self.assertIn('from="192.168.1.20",no-port-forwarding', ak)
        self.assertIn('from="192.168.1.20,192.168.1.30"', ak)
        db = K.dropbear_keys(keys)
        self.assertTrue(K.check_dropbear(db))
        self.assertFalse(K.check_dropbear(ak))
        self.assertEqual(K.load(K.dump(keys)), keys)
        left = K.remove(keys, ph.fingerprint)
        self.assertEqual(left, [ad])
        with self.assertRaises(ValueError):
            K.remove(left, ad.fingerprint)                         # never remove the last key
        with self.assertRaises(ValueError):
            K.authorized_keys(keys, "not-an-ip", "192.168.1.30")

    def test_device_id(self):
        self.assertTrue(K.valid_device_id("-".join(["ABCDEFG"] * 8)))
        self.assertFalse(K.valid_device_id("abc"))


if __name__ == "__main__":
    unittest.main()
