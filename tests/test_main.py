"""Smoke test: run the real proxy process with a fake privd and a fake llama, then SIGTERM it."""
import http.client
import json
import os
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import unittest

from gladeon.privd.daemon import Privd, Result
from tests.fake_llama import FakeLlama

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MainSmokeTest(unittest.TestCase):
    def test_start_status_stop(self):
        with tempfile.TemporaryDirectory() as d:
            cert, key = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                            "-nodes", "-days", "1", "-subj", "/CN=GladeonLocal", "-addext",
                            "subjectAltName=IP:127.0.0.1", "-keyout", key, "-out", cert], check=True, capture_output=True)
            for n, v in (("token", "t" * 43), ("llmkey", "k")):
                with open(os.path.join(d, n), "w") as f:
                    f.write(v)
            calls = []
            verbs = {v: (lambda name: (lambda c: calls.append(name) or Result(0, {"stopping": False})))(v)
                     for v in ("vault-status", "unit-start", "clock-floor-tick", "system-stopping", "lock-intent-set")}
            sock = os.path.join(d, "privd.sock")
            p = Privd(sock, os.getuid(), verbs, uid_of=lambda s: os.getuid())
            p.start()
            llama = FakeLlama()
            port = 18443 + os.getpid() % 1000
            conf = {"token_file": os.path.join(d, "token"), "llm_key_file": os.path.join(d, "llmkey"),
                    "phone_ips": ["127.0.0.1"], "cert": cert, "key": key, "listen": "127.0.0.1", "port": port,
                    "vault_root": os.path.join(d, "vault"), "state_dir": d, "run_dir": os.path.join(d, "run"),
                    "privd_socket": sock, "engine_ports": {"quick": llama.port, "deep": 1, "embed": 1}}
            cpath = os.path.join(d, "proxy.json")
            with open(cpath, "w") as f:
                json.dump(conf, f)
            proc = subprocess.Popen([sys.executable, "-m", "gladeon.proxy.main", "--config", cpath], cwd=ROOT,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                ctx = ssl.create_default_context(cafile=cert)
                body = None
                for _ in range(100):
                    try:
                        c = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=2)
                        c.request("GET", "/status", headers={"Authorization": "Bearer " + "t" * 43})
                        body = json.loads(c.getresponse().read())
                        c.close()
                        if body["engines"]["quick"] == "READY":
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                self.assertIsNotNone(body)
                self.assertEqual(body["engines"]["quick"], "READY")
                self.assertEqual(body["state"], "VAULT_LOCKED_PUBLIC_ONLY")
                self.assertIn("unit-start", calls)
                proc.send_signal(signal.SIGTERM)
                out, err = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(err, b"")                      # stderr sink: nothing escapes
                self.assertIn("system-stopping", calls)
            finally:
                if proc.poll() is None:
                    proc.kill()
                p.close()
                llama.close()


if __name__ == "__main__":
    unittest.main()
