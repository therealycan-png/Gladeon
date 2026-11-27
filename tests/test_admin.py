"""gladeon-admin helpers: P28/P46 escaping and IDs, armored bundle (3.3.7), passphrase rules (9.2)."""
import os
import tempfile
import unittest

from gladeon.admin.common import (FileIds, armor, canonical_passphrase, dearmor, escape, make_bundle,
                                  new_passphrase)


class AdminTest(unittest.TestCase):
    def test_p28_escape(self):
        self.assertEqual(escape("a\x1bb\x9bc"), "a\\x1bb\\x9bc")
        self.assertEqual(escape("back\\slash"), "back\\x5cslash")
        self.assertEqual(escape("rtl‮evil"), "rtl\\u{202e}evil")
        self.assertEqual(escape(os.fsdecode(b"bad\xffname")), "bad\\xffname")
        self.assertEqual(escape("Rent$(touch /tmp/x)`;.md"), "Rent$(touch /tmp/x)`;.md")   # printed, never run
        self.assertEqual(escape("Mañana.md"), "Mañana.md")

    def test_p46_ids(self):
        with tempfile.TemporaryDirectory() as d:
            ids = FileIds(d, "versions-recent")
            i = ids.add("Rent$(touch /tmp/x)`;.md")
            self.assertRegex(i, r"^[0-9a-f]{12}$")
            ids.save()
            self.assertEqual(oct(os.stat(ids.path).st_mode & 0o777), "0o600")
            self.assertEqual(FileIds.resolve(d, "versions-recent", i), "Rent$(touch /tmp/x)`;.md")
            self.assertIsNone(FileIds.resolve(d, "versions-recent", "../../etc/x"))
            self.assertIsNone(FileIds.resolve(d, "versions-recent", i, now=lambda: 9e12))   # expired
            again = FileIds(d, "versions-recent")
            self.assertNotEqual(again.add("Rent$(touch /tmp/x)`;.md"), i)                  # per-listing key

    def test_armor_roundtrip(self):
        data = make_bundle(b"-----BEGIN CERTIFICATE-----\nMII\n-----END CERTIFICATE-----\n", "t" * 43, "pin=")
        a = armor(data)
        self.assertTrue(a.startswith("-----BEGIN GLADEON BUNDLE-----"))
        self.assertEqual(dearmor(a.replace("\n", "\r\n")), data)
        tampered = a.replace(a.splitlines()[1][:4], "AAAA", 1)
        with self.assertRaises(ValueError):
            dearmor(tampered)

    def test_passphrases(self):
        self.assertEqual(canonical_passphrase("Café au lait twenty chars"),
                         "Café au lait twenty chars".encode())
        with self.assertRaises(ValueError):
            canonical_passphrase("short")
        with self.assertRaises(ValueError):
            new_passphrase("correct horse battery staple", "correct horse battery stapl")
        self.assertEqual(new_passphrase("correct horse battery staple", "correct horse battery staple"),
                         b"correct horse battery staple")


if __name__ == "__main__":
    unittest.main()
