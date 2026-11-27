"""Controller integration: fake privd daemon + fake llama + real brain on a temp 'vault'."""
import os
import tempfile
import threading
import time
import unittest
import uuid

from gladeon.engine.client import Engine
from gladeon.privd.client import PrivdClient
from gladeon.privd.daemon import Privd, Result
from gladeon.proxy.controller import Config, Controller
from gladeon.proxy.reqcache import RequestCache
from tests.fake_llama import FakeLlama

GOOD = b"correct horse battery staple"


def wait(pred, t=10.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        root = self.d.name
        self.vault = os.path.join(root, "vault")
        os.makedirs(os.path.join(self.vault, "notes"))
        with open(os.path.join(self.vault, "notes", "Wifi.md"), "w") as f:
            f.write("Wifi password: abc123.")
        old = time.time() - 120
        os.utime(os.path.join(self.vault, "notes", "Wifi.md"), (old, old))
        self.calls = []
        self.open_delay = 0.0
        self.close_code = 0

        def v(name, fn=None):
            def inner(call):
                self.calls.append(name)
                return fn(call) if fn else Result(0)
            return inner

        def vopen(call):
            time.sleep(self.open_delay)
            return Result(0) if bytes(call.secrets[0]) == GOOD else Result(10, tool_exit_code=2)

        def unit_start(call):
            if call.args["unit"] == "llm-embed":
                self.ctl.set_engine("embed", "READY")
            return Result(0)

        def unit_stop(call):
            if call.args["unit"] == "llm-embed":
                self.ctl.set_engine("embed", "STOPPED")
            return Result(0)
        self.privd = Privd(os.path.join(root, "privd.sock"), 1500, {
            "vault-status": v("vault-status", lambda c: Result(0, {"fsck_last_s": 1, "last_close_clean": True})),
            "vault-open": v("vault-open", vopen), "vault-close": v("vault-close", lambda c: Result(self.close_code)),
            "lock-intent-set": v("lock-intent-set"), "unit-start": v("unit-start", unit_start),
            "unit-stop": v("unit-stop", unit_stop), "poweroff": v("poweroff")}, uid_of=lambda s: 1500)
        self.privd.start()
        self.llama = FakeLlama()
        self.llama.embed_mode = "bag"
        eng = {n: Engine(n, "k", port=self.llama.port) for n in ("quick", "deep", "embed")}
        cfg = Config(vault_root=self.vault, state_dir=root, run_dir=os.path.join(root, "run"),
                     default_floors=(0.6, 0.97), poll_s=0.2)
        self.cache = RequestCache()
        self.ctl = Controller(cfg, PrivdClient(os.path.join(root, "privd.sock")), eng, self.cache)
        self.ctl.set_engine("quick", "READY")
        import gladeon.proxy.controller as C
        C.INDEX_QUIET_S = 0

    def tearDown(self):
        if self.ctl.s.vault == "READY":
            self.ctl.lock()
            wait(lambda: self.ctl.s.vault == "LOCKED")
        self.privd.close()
        self.llama.close()
        self.d.cleanup()

    def unlock_ready(self):
        self.assertEqual(self.ctl.unlock(GOOD.decode())[0], 202)
        self.assertTrue(wait(lambda: self.ctl.s.vault == "READY"))
        self.assertTrue(wait(lambda: self.ctl.status()["index"]["notes"] == 1 and
                             self.ctl.status()["index"]["pending"] == 0))

    def test_bad_passphrase_lockout(self):
        for _ in range(5):
            self.assertEqual(self.ctl.unlock("wrong passphrase here!!")[0], 202)
            self.assertTrue(wait(lambda: self.ctl.s.vault == "LOCKED"))
        self.assertEqual(self.ctl.s.vault_last_error, "BAD_PASSPHRASE")
        code, body = self.ctl.unlock(GOOD.decode())
        self.assertEqual((code, body["error"]), (423, "LOCKED_OUT"))

    def test_unlock_index_query_lock(self):
        self.unlock_ready()
        st = self.ctl.status()
        self.assertEqual((st["state"], st["vault"], st["index"]["chunks"]), ("FULL_READY", "READY", 1))
        self.assertIn("INDEX_UNCALIBRATED", st["warnings"])
        self.llama.replies.append((["Your wifi password is abc123."], 0))
        out = self.ctl.run_query("What's my wifi password?", "auto", None)
        self.assertEqual((out["route"], out["display_text"]), ("BRAIN_RAG", "Your wifi password is abc123."))
        rid = str(uuid.uuid4())
        self.cache.begin(rid)
        self.cache.finish(rid, 200, out, route="BRAIN_RAG")
        self.assertEqual(self.ctl.lock()[0], 202)
        self.assertTrue(wait(lambda: self.ctl.s.vault == "LOCKED"))
        self.assertEqual(self.cache.lookup(rid).payload["route"], "VAULT_LOCKED")   # P14
        self.assertIsNone(self.ctl.brain)
        self.assertEqual(self.calls[-2:], ["unit-stop", "vault-close"])
        self.assertLess(self.calls.index("lock-intent-set"), self.calls.index("vault-close"))
        out = self.ctl.run_query("What's my wifi password?", "auto", None)
        self.assertEqual(out["route"], "VAULT_LOCKED")

    def test_lock_during_unlock(self):
        self.open_delay = 0.8
        self.ctl.unlock(GOOD.decode())
        time.sleep(0.2)
        self.assertEqual(self.ctl.lock()[1]["state"], "LOCK_REQUESTED")
        self.assertTrue(wait(lambda: self.ctl.s.vault == "LOCKED"))
        self.assertIn("vault-close", self.calls)

    def test_lock_failed(self):
        self.unlock_ready()
        self.close_code = 16
        self.ctl.lock()
        self.assertTrue(wait(lambda: self.ctl.s.vault == "LOCK_FAILED"))
        self.assertEqual(self.ctl.status()["state"], "VAULT_LOCK_FAILED")
        self.close_code = 0
        self.ctl.lock()
        self.assertTrue(wait(lambda: self.ctl.s.vault == "LOCKED"))

    def test_auto_lock(self):
        self.unlock_ready()
        self.ctl.cfg.auto_lock_s = 0.3
        self.ctl.touch()
        self.ctl.auto_lock_tick()
        self.assertEqual(self.ctl.s.vault, "READY")
        time.sleep(0.4)
        self.ctl.auto_lock_tick()
        self.assertTrue(wait(lambda: self.ctl.s.vault == "LOCKED"))

    def test_still_reading_while_new_note_unstable(self):
        self.unlock_ready()
        self.ctl.brain.set_meta(f"cos_floor:{self.ctl.brain.emb_key}", "0.995")   # toy vectors: no vector hit
        with open(os.path.join(self.vault, "notes", "Bike.md"), "w") as f:
            f.write("Bike lock pin 4471.")
        out = self.ctl.run_query("What's my bike lock pin?", "auto", None)
        self.assertEqual(out["route"], "STILL_READING")

    def test_shutdown(self):
        self.assertEqual(self.ctl.system("shutdown")[0], 202)
        self.assertIn("poweroff", self.calls)


if __name__ == "__main__":
    unittest.main()
