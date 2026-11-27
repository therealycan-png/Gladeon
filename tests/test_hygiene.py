import unittest

from gladeon.hygiene import Liveness, _CodeSink


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class HygieneTest(unittest.TestCase):
    def test_sink_drops(self):
        s = _CodeSink()
        s.write("vault/secret-note.md not found")
        self.assertEqual(s.dropped, 1)

    def test_liveness(self):
        c = Clock()
        lv = Liveness(c)
        lv.beat("a")
        lv.beat("b")
        c.t = 11
        self.assertEqual(sorted(lv.stale()), ["a", "b"])
        lv.beat("a")
        with lv.op("b", 120):
            c.t = 100
            self.assertEqual(lv.stale(), ["a"])
            c.t = 140
            self.assertIn("b", lv.stale())
        self.assertNotIn("b", lv.stale())


if __name__ == "__main__":
    unittest.main()
