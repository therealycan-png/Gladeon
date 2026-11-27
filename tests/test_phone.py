"""Run the Termux scripts (with plain bash) against the real proxy server over pinned TLS."""
import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

from gladeon.proxy.app import App
from gladeon.proxy.server import Server, tls_context
from tests.test_server import FakeCtl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHONE = os.path.join(ROOT, "phone")
TOKEN = "y" * 43


@unittest.skipUnless(shutil.which("curl") and shutil.which("jq") and shutil.which("openssl"), "needs curl, jq, openssl")
class PhoneScriptsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.TemporaryDirectory()
        g = cls.g = os.path.join(cls.d.name, ".gladeon")
        os.makedirs(g)
        cert, key = os.path.join(g, "cert.pem"), os.path.join(cls.d.name, "key.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-days", "1", "-subj", "/CN=GladeonLocal", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", key, "-out", cert], check=True, capture_output=True)
        der = subprocess.run(["openssl", "x509", "-in", cert, "-pubkey", "-noout"], check=True, capture_output=True).stdout
        spki = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"], input=der, check=True,
                              capture_output=True).stdout
        pin = base64.b64encode(hashlib.sha256(spki).digest()).decode()
        cls.ctl = FakeCtl()
        cls.srv = Server("127.0.0.1", 0, tls_context(cert, key), TOKEN.encode(), frozenset({"127.0.0.1"}),
                         App(cls.ctl, sync_wait_s=0.5))
        cls.srv.start()
        with open(os.path.join(g, "config"), "w") as f:
            f.write(f"PC_IP_ETH=127.0.0.1\nPC_IP_WLAN=127.0.0.1\nSPKI_PIN={pin}\nPORT={cls.srv.port}\n")
        with open(os.path.join(g, "auth.header"), "w") as f:
            f.write(f"Authorization: Bearer {TOKEN}\n")

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        cls.d.cleanup()

    def run_script(self, name, *args, stdin=""):
        env = dict(os.environ, GLADEON_DIR=self.g, PATH="/usr/bin:/bin")
        return subprocess.run(["bash", os.path.join(PHONE, name), *args], input=stdin, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_status(self):
        r = self.run_script("gladeon-status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("State: FULL_READY", r.stdout)

    def test_ask(self):
        r = self.run_script("gladeon-ask", "hello", "there")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "HELLO THERE")

    def test_ask_slow_polls_result(self):
        self.ctl.slow = 3.0
        try:
            r = self.run_script("gladeon-ask", "slow")
        finally:
            self.ctl.slow = 0.0
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "SLOW")

    def test_unlock_reads_stdin(self):
        r = self.run_script("gladeon-unlock", stdin="correct horse battery staple\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Unlocking.", r.stdout)

    def test_shutdown_needs_confirm(self):
        self.assertEqual(self.run_script("gladeon-shutdown", stdin="no\n").returncode, 1)
        r = self.run_script("gladeon-shutdown", stdin="SHUTDOWN\n")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wrong_pin_refused(self):
        bad = os.path.join(self.d.name, "bad")
        shutil.copytree(self.g, bad)
        with open(os.path.join(bad, "config")) as f:
            conf = f.read()
        import re
        with open(os.path.join(bad, "config"), "w") as f:
            f.write(re.sub(r"SPKI_PIN=.*", "SPKI_PIN=" + base64.b64encode(b"0" * 32).decode(), conf))
        env = dict(os.environ, GLADEON_DIR=bad, PATH="/usr/bin:/bin")
        r = subprocess.run(["bash", os.path.join(PHONE, "gladeon-status")], env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
