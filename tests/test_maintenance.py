import os
import sqlite3
import tempfile
import unittest

from gladeon.brain.chunker import chunk_note
from gladeon.brain.index import Brain
from gladeon.brain.maintenance import daily, run_calibration, snapshot


def bag(text):
    v = [0.0] * 26
    for ch in text.casefold():
        if "a" <= ch <= "z":
            v[ord(ch) - 97] += 1
    return v


class MaintTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.b = Brain(os.path.join(self.d.name, "brain.db"), "k1")
        for i, t in enumerate(["Wifi password abc.", "Dentist on Friday.", "Bike lock pin 4471.",
                               "Rice recipe with garlic.", "Passport renewal in March.", "Vet visit for Bantay.",
                               "Car plate number ABC 123.", "Gym schedule Monday.", "Laptop warranty ends 2027.",
                               "Mom birthday in June.", "Rent due on the 5th.", "Water bill account 778."]):
            rel = f"n{i}.md"
            m, cs = chunk_note(rel, t, "2026-11-20", "2026-11-27")
            self.b.upsert_note(rel, m, cs, rel, len(t), 1, i)
            for c in cs:
                self.b.store_vector(c.text_hash, bag(c.header + c.body))

    def tearDown(self):
        self.b.db.close()
        self.d.cleanup()

    def test_snapshot_rotation_and_skip(self):
        bk = os.path.join(self.d.name, "backups")
        self.assertIsNone(snapshot(self.b, bk, clock_trusted=False, epoch=1, floor=0))
        p1 = snapshot(self.b, bk, True, 1, floor=0)
        self.assertTrue(p1 and os.path.exists(p1))
        self.assertIsNone(snapshot(self.b, bk, True, 2, floor=0))          # unchanged -> skipped
        for e in range(2, 6):
            m, cs = chunk_note(f"x{e}.md", f"more {e}.", "2026-11-20", "2026-11-27")
            self.b.upsert_note(f"x{e}.md", m, cs, str(e), 1, 1, 100 + e)
            snapshot(self.b, bk, True, e, floor=0)
        snaps = sorted(os.listdir(bk))
        self.assertEqual(snaps, ["brain-3.db", "brain-4.db", "brain-5.db"])
        db = sqlite3.connect(os.path.join(bk, snaps[-1]))
        self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
        db.close()
        self.assertIsNone(snapshot(self.b, bk, True, 9, floor=1 << 62))   # below disk floor

    def test_daily(self):
        self.b.purge_note("n0.md")
        h = self.b.db.execute("SELECT text_hash FROM chunks LIMIT 1").fetchone()[0]
        self.b.store_vector(h, None)
        r = daily(self.b, key_change_complete=False)
        self.assertEqual((r.vectors_deleted, r.failed_retried, r.integrity_ok), (1, 1, True))

    def test_calibration(self):
        lines = [f"{t} please => n{i}" for i, t in enumerate(
            ["wifi password", "dentist friday", "bike lock pin", "rice recipe garlic", "passport renewal march",
             "vet visit bantay", "car plate number", "gym schedule monday", "laptop warranty", "mom birthday june"])]
        lines += ["quantum physics lecture", "zzz", "how tall is everest", "jazz history", "xylophone music",
                  "kayak trips", "volcano facts", "jupiter moons", "quiz night", "zebra stripes", "Nope => missing-note"]
        r = run_calibration(self.b, "\n".join(lines), bag, pending=0, first_scan_done=True, unstable_files=0)
        self.assertTrue(r.ok, r)
        self.assertEqual((r.positives, r.negatives), (10, 10))
        self.assertIn("CALIBRATION_TARGET_UNRESOLVED", r.warnings)
        self.assertGreaterEqual(r.cos_floor_strict, r.cos_floor)
        self.assertEqual(float(self.b.get_meta("cos_floor:k1")), round(r.cos_floor, 6))
        self.assertFalse(run_calibration(self.b, "", bag, 1, True, 0).ok)


if __name__ == "__main__":
    unittest.main()
