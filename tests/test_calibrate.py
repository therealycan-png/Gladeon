import unittest

from gladeon.brain.calibrate import (PartialState, embed_restart_due, floors, parse, partial,
                                     percentile, resolve_target)


def st(**kw):
    base = dict(embed_ready=True, embed_down_since=None, pending=0, unstable_files=0, first_scan_done=True,
                embed_failed=0, over_ceiling=False, disk_low=False)
    base.update(kw)
    return PartialState(**base)


class CalibrationTest(unittest.TestCase):
    def test_parse(self):
        cs = parse("# title\n- wifi password? => Wifi\n* who is my dentist?\n\nq2 => notes/a.md\nplain negative")
        self.assertEqual(cs.positives, [("wifi password?", "Wifi"), ("q2", "notes/a.md")])
        self.assertEqual(cs.negatives, ["who is my dentist?", "plain negative"])

    def test_percentile(self):
        self.assertAlmostEqual(percentile([1, 2, 3, 4, 5], 10), 1.4)
        self.assertAlmostEqual(percentile([1, 2, 3, 4, 5], 90), 4.6)

    def test_floors(self):
        pos = [0.7 + i * 0.01 for i in range(10)]
        neg = [0.3 + i * 0.01 for i in range(10)]
        f = floors(pos, neg)
        self.assertAlmostEqual(f.cos_floor, (percentile(pos, 10) + percentile(neg, 90)) / 2)
        self.assertGreaterEqual(f.cos_floor_strict, f.cos_floor)
        self.assertIsNone(floors(pos[:9], neg))
        # strict floor rises above the midpoint when negatives score high
        f2 = floors([0.5] * 10, [0.1] * 9 + [0.9])
        self.assertGreater(f2.cos_floor_strict, f2.cos_floor)

    def test_resolve(self):
        notes = [("Wifi.md", "Wifi"), ("a/Trip.md", "Trip"), ("b/Trip.md", "Trip")]
        self.assertEqual(resolve_target("Wifi", notes), "Wifi.md")
        self.assertEqual(resolve_target("a/Trip.md", notes), "a/Trip.md")
        self.assertIsNone(resolve_target("Trip", notes))
        self.assertIsNone(resolve_target("Nope", notes))


class PartialTest(unittest.TestCase):
    def test_clean(self):
        p = partial(st(), 0)
        self.assertFalse(p.transient or p.permanent)

    def test_pending_is_transient(self):
        self.assertTrue(partial(st(pending=3), 0).transient)
        self.assertTrue(partial(st(first_scan_done=False), 0).transient)

    def test_failed_and_ceiling_do_not_suppress_transient(self):
        p = partial(st(pending=1, embed_failed=2, over_ceiling=True), 0)
        self.assertTrue(p.transient and p.permanent)
        self.assertNotIn("INDEX_PARTIAL", p.warnings)

    def test_embed_down_short_then_long(self):
        self.assertTrue(partial(st(embed_ready=False, embed_down_since=0), 599).transient)
        p = partial(st(embed_ready=False, embed_down_since=0, pending=5), 600)
        self.assertFalse(p.transient)
        self.assertTrue(p.permanent)
        self.assertIn("EMBED_FAILED", p.warnings)
        self.assertIn("INDEX_PARTIAL", p.warnings)

    def test_disk_low_overrides(self):
        p = partial(st(pending=5, disk_low=True), 0)
        self.assertFalse(p.transient or p.indexing_allowed)
        self.assertTrue(p.permanent)

    def test_restart_schedule(self):
        self.assertFalse(embed_restart_due(0, 299, 0))
        self.assertTrue(embed_restart_due(0, 300, 0))
        self.assertFalse(embed_restart_due(0, 899, 1))
        self.assertTrue(embed_restart_due(0, 3600, 2))
        self.assertFalse(embed_restart_due(0, 7199, 3))
        self.assertTrue(embed_restart_due(0, 7200, 3))


if __name__ == "__main__":
    unittest.main()
