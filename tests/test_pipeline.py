"""End-to-end pipeline tests with a fake llama-server and an in-memory brain."""
import time
import unittest

from gladeon.brain.chunker import chunk_note
from gladeon.brain.index import Brain
from gladeon.engine.client import Engine
from gladeon.pipeline import Deps, Pipeline, Profile
from gladeon.voice import replies as R
from tests.fake_llama import FakeLlama


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.f = FakeLlama()
        self.brain = Brain(":memory:", "k1")
        self.vault = True
        self.ready = {"quick": True, "deep": False}
        self.transient = False
        self.cmds = []
        eng = Engine("quick", "k", port=self.f.port)
        self.p = Pipeline(Deps(
            engines={"quick": eng, "deep": eng},
            profiles={"quick": Profile(control_tokens=("<|im_end|>",)), "deep": Profile()},
            engine_ready=lambda n: self.ready[n],
            vault_ready=lambda: self.vault,
            search=lambda t, floor: self.brain.search(t, None, 0.5),
            partial_transient=lambda: self.transient,
            still_reading_n=lambda: 3,
            command=lambda c: self.cmds.append(c) or ("Locking.", "Locking."),
        ))

    def tearDown(self):
        self.f.close()

    def note(self, rel, text):
        m, cs = chunk_note(rel, text, "2026-11-20", "2026-11-27")
        self.brain.upsert_note(rel, m, cs, rel, len(text), 1, 0)

    def say(self, *pieces):
        self.f.replies.append((list(pieces), 0))

    def last_prompt(self):
        return self.f.requests[-1][1]["messages"]

    def test_fixed_routes(self):
        self.assertEqual(self.p.handle("Gladeon").display_text, "Yes?")
        self.assertEqual(self.p.handle("lock the vault please").display_text, "Locking.")
        self.assertEqual(self.cmds, ["lock"])
        r = self.p.handle("How much did I spend on GCash?")
        self.assertEqual((r.route, r.display_text), ("FINANCE_DEFERRED", R.FINANCE_DEFERRED[0]))
        self.assertEqual(self.p.followup.route, "FINANCE_DEFERRED")
        self.assertEqual(self.f.requests, [])     # no engine call for fixed replies

    def test_personal_locked_and_missing(self):
        self.vault = False
        self.assertEqual(self.p.handle("What's my wifi password?").route, "VAULT_LOCKED")
        self.vault = True
        self.assertEqual(self.p.handle("What's my wifi password?").route, "NOT_IN_NOTES")
        self.transient = True
        r = self.p.handle("What's my wifi password?")
        self.assertEqual(r.route, "STILL_READING")
        self.assertIn("(3 left)", r.display_text)

    def test_personal_brain(self):
        self.note("Wifi.md", "Wifi password: abc123.")
        self.say("Your wifi password is abc123.")
        r = self.p.handle("What's my wifi password?")
        self.assertEqual((r.route, r.display_text), ("BRAIN_RAG", "Your wifi password is abc123."))
        user = self.last_prompt()[-1]["content"]
        self.assertIn("<<<VAULT_DATA_", user)
        self.assertIn("Wifi password: abc123.", user)
        self.assertEqual(self.f.requests[-1][1]["temperature"], 0.2)
        # sticky follow-up keeps BRAIN
        self.say("It was saved on November 20, 2026.")
        r2 = self.p.handle("what about the date?")
        self.assertEqual(r2.route, "BRAIN_RAG")

    def test_leak_blocked(self):
        self.note("Wifi.md", "Wifi password: abc123.")
        self.say("Sure: <<<VAULT_DATA_ and the rules say answer only with facts stated in those notes.")
        r = self.p.handle("What's my wifi password?")
        self.assertEqual(r.route, "NOT_IN_NOTES")
        self.assertIn("RAG_LEAK_BLOCKED", r.warnings)

    def test_p8c_implicit_fallback(self):
        self.note("Wifi.md", "Wifi password: abc123. To change it, open the router page.")
        self.say("Open the router page and pick a new one.")
        r = self.p.handle("How do I change my wifi password?")
        self.assertEqual(r.route, "BRAIN_RAG")
        self.say("**I don't see that in your notes**")
        self.say("Usually you log in to the router admin page.")
        self.p.followup.wipe()
        r = self.p.handle("How do I change my wifi password?")
        self.assertEqual(r.route, "GENERAL_FALLBACK")
        self.assertNotIn("VAULT_DATA", self.last_prompt()[-1]["content"])

    def test_general_without_note(self):
        self.say("Usually you log in to the router admin page.")
        r = self.p.handle("How do I change my wifi password?")
        self.assertEqual(r.route, "GENERAL_CHAT")
        self.assertEqual(self.f.requests[-1][1]["temperature"], 0.3)

    def test_deep_unavailable_prefix(self):
        self.say("Rice is a grain.")
        r = self.p.handle("think harder what is rice")
        self.assertEqual(r.mode, "deep")
        self.assertTrue(r.display_text.startswith(R.DEEP_UNAVAILABLE_PREFIX))
        self.assertIn("DEEP_UNAVAILABLE", r.warnings)

    def test_engine_empty_and_tts(self):
        self.say("<|im_end|>")
        self.say("<|im_end|>")
        r = self.p.handle("tell me a joke")
        self.assertEqual(r.display_text, R.ENGINE_EMPTY[0])
        self.say("It costs ₱1,200.50 on 2026-12-05.")
        r = self.p.handle("tell me a price")
        self.assertIn("1,200 pesos and 50 centavos", r.tts_text)
        self.assertIn("December 5, 2026", r.tts_text)

    def test_busy_after_5s(self):
        import threading
        import gladeon.pipeline as P
        from gladeon.pipeline import Busy
        old = P.BUSY_WAIT_S
        P.BUSY_WAIT_S = 0.2
        try:
            self.f.replies.append((["slow."] * 10, 0.1))
            t = threading.Thread(target=self.p.handle, args=("tell me something slow",))
            t.start()
            time.sleep(0.1)
            with self.assertRaises(Busy):
                self.p.handle("hello")
            t.join()
        finally:
            P.BUSY_WAIT_S = old

    def test_mid_stream_deadline(self):
        self.f.replies.append((["First sentence.", " Second", " part"] + [" more"] * 40, 0.05))
        r = self.p.handle("tell me a story", start=time.monotonic() - 59.6)
        self.assertEqual(r.display_text, "First sentence.")
        self.assertIn("ANSWER_TRUNCATED", r.warnings)
        self.f.replies.append(([" no end"] * 40, 0.05))
        r = self.p.handle("tell me a story", start=time.monotonic() - 59.7)
        self.assertEqual(r.display_text, R.OUT_OF_TIME[0])


if __name__ == "__main__":
    unittest.main()
