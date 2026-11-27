"""6.3.5 reconciliation at proxy start (P23, P23d, P48-like)."""
import os
import time
import unittest

from gladeon.privd.daemon import Result
from tests import test_controller as tc


class ReconcileTest(tc.ControllerTest):
    test_bad_passphrase_lockout = test_unlock_index_query_lock = test_lock_during_unlock = None
    test_lock_failed = test_auto_lock = test_still_reading_while_new_note_unstable = test_shutdown = None
    test_maintenance_calibrates_and_snapshots = test_clock_trust_and_correction = None

    def status_is(self, **d):
        self.privd.verbs["vault-status"] = lambda c: (self.calls.append("vault-status"), Result(0, d))[1]

    def test_p23_adopt_with_saved_activity(self):
        self.status_is(mapping_open=True, mounted=True, id_ok=True, lock_intent=False)
        os.makedirs(self.ctl.cfg.run_dir, exist_ok=True)
        saved = time.monotonic() - 100
        with open(os.path.join(self.ctl.cfg.run_dir, "last_activity"), "w") as f:
            f.write(str(saved))
        self.assertEqual(self.ctl.reconcile(), "adopted")
        self.assertEqual(self.ctl.s.vault, "READY")
        self.assertAlmostEqual(self.ctl.last_activity, saved, places=2)
        self.assertIsNotNone(self.ctl.brain)
        self.ctl.cfg.auto_lock_s = 50                   # saved activity is 100 s old: auto-lock fires
        self.ctl.auto_lock_tick()
        self.assertTrue(tc.wait(lambda: self.ctl.s.vault == "LOCKED"))

    def test_adopt_without_activity_locks(self):
        self.status_is(mapping_open=True, mounted=True, id_ok=True, lock_intent=False)
        self.assertEqual(self.ctl.reconcile(), "locked-no-activity")
        self.assertEqual(self.ctl.s.vault, "LOCKED")
        self.assertIn("vault-close", self.calls)

    def test_lock_intent_resumes_lock(self):
        self.status_is(mapping_open=True, mounted=False, lock_intent=True)
        self.assertEqual(self.ctl.reconcile(), "resumed-lock")
        self.assertEqual(self.ctl.s.vault, "LOCKED")

    def test_p23d_open_still_running(self):
        self.status_is(mapping_open=False, lock_holder="vault-open")
        n = {"busy": 2}

        def close(c):
            self.calls.append("vault-close")
            if n["busy"]:
                n["busy"] -= 1
                return Result(29, {"lock_holder": "vault-open"})
            return Result(0)
        self.privd.verbs["vault-close"] = close
        self.assertEqual(self.ctl.reconcile(busy_retry_s=0.05), "closed-after-open")
        self.assertEqual(self.ctl.s.vault, "LOCKED")
        self.assertLess(self.calls.index("lock-intent-set"), self.calls.index("vault-close"))
        self.assertEqual(self.calls.count("vault-close"), 3)

    def test_half_open_closed(self):
        self.status_is(mapping_open=True, mounted=False, id_ok=False, lock_intent=False)
        self.assertEqual(self.ctl.reconcile(), "closed-half-open")
        self.assertEqual(self.ctl.s.vault, "LOCKED")

    def test_clean_locked(self):
        self.status_is(mapping_open=False, mounted=False)
        self.assertEqual(self.ctl.reconcile(), "locked")


if __name__ == "__main__":
    unittest.main()
