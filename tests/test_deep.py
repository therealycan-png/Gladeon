"""DEEP loading (8.3): early 202, swap QUICK->DEEP, failure fallback with prefix, deep_failed_this_boot, idle return."""
import threading
import time
import unittest
import uuid

from gladeon.privd.daemon import Result
from gladeon.proxy.app import App
from gladeon.proxy.httpcore import Request
from gladeon.voice import replies as R
from tests import test_controller as tc


class DeepTest(tc.ControllerTest):
    test_bad_passphrase_lockout = test_unlock_index_query_lock = test_lock_during_unlock = None
    test_lock_failed = test_auto_lock = test_still_reading_while_new_note_unstable = test_shutdown = None
    test_maintenance_calibrates_and_snapshots = test_clock_trust_and_correction = None

    def setUp(self):
        super().setUp()
        self.ctl.cfg.deep_plan = "swap"
        self.ctl.cfg.load_seconds = {"quick": 0.5, "deep": 0.5}
        self.deep_comes_up = True
        units = []
        self.units = units

        def start(c):
            units.append(("start", c.args["unit"]))
            u = c.args["unit"]
            if u == "llm-deep" and self.deep_comes_up:
                threading.Timer(0.2, lambda: self.ctl.set_engine("deep", "READY")).start()
            if u == "llm-quick":
                threading.Timer(0.1, lambda: self.ctl.set_engine("quick", "READY")).start()
            if u == "llm-embed":
                self.ctl.set_engine("embed", "READY")
            return Result(0)

        def stop(c):
            units.append(("stop", c.args["unit"]))
            return Result(0)
        self.privd.verbs["unit-start"] = start
        self.privd.verbs["unit-stop"] = stop

    def test_swap_and_early_202(self):
        app = App(self.ctl, self.cache, sync_wait_s=5)
        self.llama.replies.append((["Rice is a grain."], 0))
        rid = str(uuid.uuid4())
        req = Request("POST", "/query", {"x-request-id": rid},
                      b'{"transcript": "think harder what is rice", "mode": "auto"}')
        status, body = app(req)
        self.assertEqual(status, 202)
        self.assertEqual(body["display_text"], R.deep_loading(0)[0].replace("0", "0"))
        self.assertTrue(tc.wait(lambda: (self.cache.lookup(rid) or None) and self.cache.lookup(rid).state == "done"))
        out = self.cache.lookup(rid).payload
        self.assertEqual((out["mode"], out["display_text"]), ("deep", "Rice is a grain."))
        self.assertIn(("stop", "llm-quick"), self.units)
        self.assertEqual(self.ctl.s.engines["deep"], "READY")
        # idle return after 10 min without DEEP requests
        self.ctl.cfg.deep_idle_s = 0
        self.ctl.deep_idle_tick()
        self.assertEqual(self.units[-2:], [("stop", "llm-deep"), ("start", "llm-quick")])

    def test_failure_falls_back_to_quick(self):
        self.deep_comes_up = False
        self.llama.replies.append((["Rice is a grain."], 0))
        out = self.ctl.run_query("think harder what is rice", "auto", None)
        self.assertTrue(out["display_text"].startswith(R.DEEP_UNAVAILABLE_PREFIX))
        self.assertTrue(self.ctl.deep_failed_this_boot)
        self.assertIn(("start", "llm-quick"), self.units)
        n = len(self.units)
        self.llama.replies.append((["Still a grain."], 0))
        out = self.ctl.run_query("think harder what is rice", "auto", None)
        self.assertTrue(out["display_text"].startswith(R.DEEP_UNAVAILABLE_PREFIX))
        self.assertEqual(len(self.units), n)                  # no new load attempt
        self.ctl.engine_restart("deep")
        self.assertFalse(self.ctl.deep_failed_this_boot)

    def test_deep_notes_question_keeps_hits_through_swap(self):
        self.unlock_ready()
        self.ctl.set_engine("quick", "READY")
        self.llama.replies.append((["Your wifi password is abc123."], 0))
        out = self.ctl.run_query("think harder what's my wifi password?", "auto", None)
        self.assertEqual((out["route"], out["mode"]), ("BRAIN_RAG", "deep"))
        self.assertEqual(out["display_text"], "Your wifi password is abc123.")

    def test_request_during_deep_load_does_not_clobber(self):
        self.unlock_ready()
        self.ctl.set_engine("quick", "READY")
        self.llama.replies.append((["Your wifi password is abc123."], 0))
        res = {}
        t = threading.Thread(target=lambda: res.update(
            self.ctl.run_query("think harder what's my wifi password?", "auto", None)))
        t.start()
        time.sleep(0.1)                                   # DEEP is loading now; the pipeline lock is free
        other = self.ctl.run_query("lock the vault vault hint", "auto", None)
        self.assertEqual(other["route"], "LOCK_HINT")    # a fixed reply is answered meanwhile
        t.join(10)
        self.assertEqual(res["route"], "BRAIN_RAG")
        self.assertEqual(self.units.count(("start", "llm-deep")), 1)

    def test_processing_reply_with_partial_load_seconds(self):
        self.ctl.cfg.load_seconds = {"quick": 45}
        self.assertEqual(self.ctl.processing_reply()["retry_window_s"], int(2 * 60 + 200 + 10))

    def test_fixed_routes_never_start_deep(self):
        out = self.ctl.run_query("think harder how much did I spend on GCash", "auto", None)
        self.assertEqual(out["route"], "FINANCE_DEFERRED")
        self.assertNotIn(("start", "llm-deep"), self.units)


if __name__ == "__main__":
    unittest.main()
