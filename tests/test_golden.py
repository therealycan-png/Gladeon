"""More §17 pipeline-suite cases that need no PC: P6c, P6e, P6f, P6g, P6h, P6i, P10d, P15, P18, P32, P44, P45c."""
import os
import socket
import ssl
import threading
import time
import unittest

from gladeon.privd.daemon import Result
from gladeon.proxy.app import App
from gladeon.proxy.server import Server, tls_context
from tests import test_controller as tc
from tests import test_pipeline as tp
from tests import test_server as ts

GOOD, wait = tc.GOOD, tc.wait


class BrainGolden(tc.ControllerTest):
    """Reuses the controller harness (fake privd, fake llama with letter-bag vectors, real brain)."""

    def notes(self, rel):
        return os.path.join(self.vault, "notes", rel)

    def write_old(self, rel, text=None, data=None, age=600):
        p = self.notes(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data if data is not None else text.encode())
        t = time.time() - age
        os.utime(p, (t, t))

    def strict(self):
        self.ctl.brain.set_meta(f"cos_floor:{self.ctl.brain.emb_key}", "0.995")
        self.ctl.brain.set_meta(f"cos_floor_strict:{self.ctl.brain.emb_key}", "0.999")

    def unlock_ready(self):
        self.assertEqual(self.ctl.unlock(GOOD.decode())[0], 202)
        self.assertTrue(wait(lambda: self.ctl.s.vault == "READY"))
        self.assertTrue(wait(lambda: (lambda i: i["first_scan_done"] and i["pending"] == 0 and
                                      i["unstable_files"] == 0)(self.ctl.status()["index"])))

    def ask(self, q):
        return self.ctl.run_query(q, "auto", None)

    # skip the inherited tests in this subclass
    test_bad_passphrase_lockout = test_unlock_index_query_lock = test_lock_during_unlock = None
    test_lock_failed = test_auto_lock = test_still_reading_while_new_note_unstable = test_shutdown = None

    def test_p6c_note_between_polls(self):
        self.unlock_ready()
        self.ctl.cfg.poll_s = 3600
        self.strict()
        time.sleep(0.3)
        with open(self.notes("Vet.md"), "w") as f:
            f.write("Vet pin 9911.")
        self.assertEqual(self.ask("What's my vet pin?")["route"], "STILL_READING")

    def test_p6f_failed_unrelated_chunk(self):
        self.llama.embed_fail.add("x")
        self.write_old("Junk.md", "zzz qqq.")
        self.unlock_ready()
        self.strict()
        self.ctl.brain.store_vector(self.ctl.brain.db.execute(
            "SELECT text_hash FROM chunks c JOIN notes n ON n.id=c.note_id WHERE relpath='Junk.md'").fetchone()[0], None)
        self.assertEqual(self.ctl.status()["index"]["embed_failed"], 1)
        self.assertEqual(self.ask("What's my passport number pin?")["route"], "NOT_IN_NOTES")

    def test_p6g_skipped_files(self):
        self.write_old("_gladeon/calibration.md", "q => a")
        self.write_old("Untitled.md", "")
        self.write_old("Big.md", data=b"a " * (1536 * 1024))
        self.write_old("Wifi.sync-conflict-20261101.md", "Passport pin 1.")
        self.unlock_ready()
        self.strict()
        self.assertEqual(self.ctl.status()["index"]["unstable_files"], 0)
        self.assertEqual(self.ask("What's my passport pin?")["route"], "NOT_IN_NOTES")

    def test_p6h_old_syncthing_temp(self):
        self.write_old(".syncthing.photo.jpg.tmp", "x")
        self.write_old(".syncthing.x.md.tmp", "x")
        self.unlock_ready()
        self.strict()
        self.assertEqual(self.ctl.status()["index"]["unstable_files"], 0)
        self.assertEqual(self.ask("What's my passport pin?")["route"], "NOT_IN_NOTES")

    def test_p6e_embed_down_short(self):
        self.unlock_ready()
        self.strict()
        self.ctl.set_engine("embed", "STOPPED")
        self.ctl.embed_down_since = time.monotonic() - 60
        self.llama.replies.append((["It is abc123."], 0))
        self.assertEqual(self.ask("What's my wifi password?")["route"], "BRAIN_RAG")   # FTS-only hit
        self.assertEqual(self.ask("What's my passport pin?")["route"], "STILL_READING")

    def test_p6i_embed_down_long(self):
        self.unlock_ready()
        self.strict()
        with open(self.notes("More.md"), "w") as f:
            f.write("Some new text here.")
        self.ctl.set_engine("embed", "STOPPED")
        self.ctl.embed_down_since = time.monotonic() - 660
        self.ctl.watcher.scan()
        self.assertEqual(self.ask("What's my passport pin?")["route"], "NOT_IN_NOTES")
        self.assertIn("EMBED_FAILED", self.ctl.status()["warnings"])

    def test_p10d_not_sticky_after_fixed(self):
        self.unlock_ready()
        self.strict()
        self.assertEqual(self.ask("What's my passport pin?")["route"], "NOT_IN_NOTES")
        self.llama.replies.append((["Vets treat animals."], 0))
        self.assertNotEqual(self.ask("What about the vet?")["route"], "BRAIN_RAG")

    def test_p32_nested_note(self):
        self.write_old("A/B/note.md", "Locker pin 5512.")
        self.unlock_ready()
        self.assertTrue(wait(lambda: self.ctl.status()["index"]["notes"] == 2))

    def test_p18_lockout_survives_restart(self):
        for _ in range(5):
            self.ctl.unlock("wrong passphrase here!!")
            self.assertTrue(wait(lambda: self.ctl.s.vault == "LOCKED"))
        self.assertEqual(self.ctl.unlock("wrong passphrase here!!")[0], 423)
        from gladeon.proxy.controller import Controller
        again = Controller(self.ctl.cfg, self.ctl.privd, self.ctl.engines, self.cache)   # "reboot"
        self.assertEqual(again.unlock(GOOD.decode())[0], 423)

    def test_p44_shutdown_during_copy(self):
        self.privd.verbs["vault-status"] = lambda c: Result(0, {"copy_active": True})
        code, body = self.ctl.system("shutdown")
        self.assertEqual((code, body["error"]), (503, "JOB_IN_PROGRESS"))
        self.assertNotIn("poweroff", self.calls)


class P45cTest(tp.PipelineTest):
    test_fixed_routes = test_personal_locked_and_missing = test_personal_brain = test_leak_blocked = None
    test_p8c_implicit_fallback = test_general_without_note = test_deep_unavailable_prefix = None
    test_engine_empty_and_tts = test_mid_stream_deadline = None

    def test_p45c_followup_fallback_question(self):
        self.note("Travel.md", "Trip plan: the capital of Japan is Tokyo.")
        self.say("Tokyo.")
        r = self.p.handle("What's the capital of Japan?")
        self.assertEqual(r.route, "BRAIN_RAG")
        self.say("I don't see that in your notes.")
        self.say("Seoul.")
        r = self.p.handle("How about Korea?")
        self.assertEqual(r.route, "GENERAL_FALLBACK")
        self.assertEqual(self.last_prompt()[-1]["content"], "What's the capital of Japan? How about Korea?")


class P15Test(unittest.TestCase):
    def test_p15_sixteen_connections(self):
        ts.ServerTest.setUpClass()
        try:
            srv = Server("127.0.0.1", 0, tls_context(ts.ServerTest.cert, ts.ServerTest.key), b"t" * 43,
                         frozenset({"127.0.0.1"}), App(ts.FakeCtl()))
            srv.start()
            before = threading.active_count()
            socks = [socket.create_connection(("127.0.0.1", srv.port)) for _ in range(16)]
            time.sleep(0.5)
            closed = 0
            for s in socks:
                s.settimeout(0.2)
                try:
                    if s.recv(1) == b"":
                        closed += 1
                except (socket.timeout, TimeoutError):
                    pass
            self.assertEqual(closed, 4)                  # 4 workers + 8 queued held; 4 closed, no ServerHello
            self.assertEqual(threading.active_count(), before)
            for s in socks:
                s.close()
            srv.close()
        finally:
            ts.ServerTest.tearDownClass()


if __name__ == "__main__":
    unittest.main()
