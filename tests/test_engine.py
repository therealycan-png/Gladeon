import threading
import time
import unittest

from gladeon.engine.answer import answer
from gladeon.engine.client import ChatParams, DeadlineExceeded, Engine, EngineError
from tests.fake_llama import FakeLlama


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.f = FakeLlama()
        self.e = Engine("quick", "k", port=self.f.port)

    def tearDown(self):
        self.f.close()

    def dl(self, s=10):
        return time.monotonic() + s

    def test_params(self):
        b = ChatParams("QUICK").body([])
        self.assertEqual((b["temperature"], b["max_tokens"], b["repeat_penalty"], b["stream"], b["cache_prompt"]),
                         (0.3, 160, 1.1, True, True))
        b = ChatParams("DEEP", brain=True, supports_enable_thinking=True).body([])
        self.assertEqual((b["temperature"], b["max_tokens"], b["repeat_penalty"]), (0.2, 450, 1.0))
        self.assertEqual(b["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("seed", b)

    def test_stream_aggregates(self):
        self.f.replies.append((["Hel", "lo", " there."], 0))
        r = self.e.chat([{"role": "user", "content": "hi"}], ChatParams("QUICK"), self.dl())
        self.assertEqual((r.text, r.finish_reason, r.tokens), ("Hello there.", "stop", 3))

    def test_chunked_stream(self):
        self.f.chunked = True
        self.f.replies.append((["Hel", "lo", " there."], 0))
        r = self.e.chat([], ChatParams("QUICK"), self.dl())
        self.assertEqual((r.text, r.finish_reason), ("Hello there.", "stop"))

    def test_auth_required(self):
        bad = Engine("quick", "wrong", port=self.f.port)
        self.assertRaises(EngineError, bad.chat, [], ChatParams("QUICK"), self.dl())
        self.assertFalse(bad.health())
        self.assertTrue(self.e.health())

    def test_deadline_and_cancel(self):
        self.f.replies.append((["a"] * 50, 0.05))
        with self.assertRaises(DeadlineExceeded):
            self.e.chat([], ChatParams("QUICK"), self.dl(0.3))
        self.f.replies.append((["a"] * 50, 0.05))
        ev = threading.Event()
        threading.Timer(0.2, ev.set).start()
        a = answer(self.e, [], ChatParams("QUICK"), self.dl(), (), cancel=ev)
        self.assertEqual(a.error, "CANCELLED")
        time.sleep(0.3)
        self.assertGreaterEqual(self.f.cancelled, 1)     # server saw the closed stream

    def test_long_silence_before_first_token(self):
        import gladeon.engine.client as C
        C.READ_LOOP_S = 0.2                      # a 0.7 s silence spans several read slices
        try:
            self.f.replies.append((["Slow", " but fine."], 0.7))
            r = self.e.chat([], ChatParams("DEEP"), self.dl())
            self.assertEqual(r.text, "Slow but fine.")
        finally:
            C.READ_LOOP_S = 5.0

    def test_early_stop_callback(self):
        self.f.replies.append((["I don't", " see that", " in your notes.", " extra"], 0.01))
        seen = []
        r = self.e.chat([], ChatParams("QUICK"), self.dl(), on_text=lambda t: seen.append(t) or len(seen) < 2)
        self.assertEqual(r.finish_reason, "client_stop")
        self.assertEqual(r.text, "I don't see that")

    def test_empty_retry_then_ok(self):
        self.f.replies.append((["<think>hmm</think>", "  "], 0))
        self.f.replies.append((["Fine."], 0))
        a = answer(self.e, [], ChatParams("QUICK"), self.dl(), ())
        self.assertEqual((a.text, a.attempts), ("Fine.", 2))
        self.assertAlmostEqual(self.f.requests[-1][1]["temperature"], 0.6)

    def test_empty_twice(self):
        self.f.replies += [(["<|im_end|>"], 0), (["<|im_end|>"], 0)]
        a = answer(self.e, [], ChatParams("QUICK"), self.dl(), ("<|im_end|>",))
        self.assertEqual((a.text, a.attempts), ("", 2))

    def test_tokenize_and_embed(self):
        self.assertEqual(self.e.tokenize("a b c", self.dl()), 3)
        self.assertEqual(self.e.embed("xy", self.dl()), [1.0, 0.0, 2.0])
        self.f.embed_fail.add("bad")
        self.assertIsNone(self.e.embed("bad", self.dl()))
        dead = Engine("embed", "k", port=1)
        self.assertRaises(EngineError, dead.embed, "xy", self.dl(1))


if __name__ == "__main__":
    unittest.main()
