import datetime as dt
import os
import subprocess
import tempfile
import unittest

from gladeon import preflight as P


class PreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.TemporaryDirectory()
        cls.cert = os.path.join(cls.d.name, "c.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-days", "30", "-subj", "/CN=GladeonLocal",
                        "-addext", "subjectAltName=IP:192.168.1.10,IP:192.168.1.11",
                        "-keyout", os.path.join(cls.d.name, "k.pem"), "-out", cls.cert], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.d.cleanup()

    def test_sqlite(self):
        self.assertIn(P.sqlite_check(lib_path=None).status, ("PASS",))
        self.assertEqual(P.sqlite_check(lib_path="/nope/libsqlite3.so.0").status, "WARN")
        self.assertEqual(P.sqlite_check(min_version=(99, 0, 0)).status, "FAIL")

    def test_tls(self):
        self.assertEqual(P.tls_check(self.cert, ["192.168.1.10/24", "192.168.1.11"]).status, "WARN")  # 30 days
        self.assertEqual(P.tls_check(self.cert, ["192.168.1.12"]).status, "FAIL")
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=40)
        self.assertEqual(P.tls_check(self.cert, ["192.168.1.10"], now=later).detail, "expired")

    def test_postfilter(self):
        self.assertEqual(P.postfilter_check(["<|im_end|>"], ["An ordinary sentence."]).status, "PASS")
        self.assertEqual(P.postfilter_check(["a"], []).status, "FAIL")      # 'a' would eat fixed strings

    def test_ifaces_and_disk(self):
        with tempfile.TemporaryDirectory() as r:
            os.makedirs(os.path.join(r, "gleth0"))
            with open(os.path.join(r, "gleth0", "operstate"), "w") as f:
                f.write("up\n")
            self.assertEqual(P.iface_check(root=r).status, "PASS")
            self.assertEqual(P.iface_check(names=("glwlan0",), root=r).status, "FAIL")
        self.assertEqual(P.disk_check("/", floor=0).status, "PASS")


if __name__ == "__main__":
    unittest.main()
