
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ssi_v5.director.catchup import DirectorCatchup


def make_events(root: Path):
    p = root / "events" / "world_event_journal.sqlite3"
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute(
        """
        CREATE TABLE world_events(
            event_id TEXT,
            sequence_number INTEGER,
            world_id TEXT,
            domain_id TEXT,
            cycle_id TEXT,
            event_type TEXT,
            payload_json TEXT,
            artifact_refs_json TEXT,
            created_unix INTEGER,
            content_hash TEXT
        )
        """
    )
    for i in range(1, 6):
        conn.execute(
            "INSERT INTO world_events VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"E{i}", i, "WORLD__SPORT", "DOMAIN__TENNIS", "C1",
             "WORLD_CYCLE_COMPLETED", "{}", "[]", i, f"H{i}")
        )
    conn.commit()
    conn.close()


class DirectorCatchupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_events(self.root)
        self.c = DirectorCatchup(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_initial_checkpoint_is_zero(self):
        self.assertEqual(0, self.c.checkpoint().last_event_sequence)

    def test_reads_only_new_events(self):
        self.c.advance_checkpoint(checkpoint_id="DIRECTOR_MAIN", new_sequence=3)
        batch = self.c.read_new_events()
        self.assertEqual([4, 5], [e["sequence_number"] for e in batch["events"]])

    def test_checkpoint_cannot_move_backwards(self):
        self.c.advance_checkpoint(checkpoint_id="DIRECTOR_MAIN", new_sequence=3)
        with self.assertRaises(ValueError):
            self.c.advance_checkpoint(checkpoint_id="DIRECTOR_MAIN", new_sequence=2)

    def test_read_does_not_advance_by_default(self):
        self.c.read_new_events()
        self.assertEqual(0, self.c.checkpoint().last_event_sequence)

    def test_explicit_catchup_can_advance(self):
        r = self.c.catch_up_once(advance=True)
        self.assertEqual(5, r["checkpoint_after"])

    def test_catchup_never_starts_world(self):
        r = self.c.catch_up_once()
        self.assertFalse(r["world_start_triggered"])

    def test_catchup_never_dispatches_compute(self):
        r = self.c.catch_up_once()
        self.assertFalse(r["compute_dispatch_triggered"])

    def test_director_output_not_executed(self):
        r = self.c.catch_up_once()
        self.assertFalse(r["director_output_executed"])

    def test_schedule_is_manual_world_start(self):
        s = self.c.schedule_contract()
        self.assertTrue(s["manual_world_start"])
        self.assertFalse(s["director_may_start_worlds_autonomously"])

    def test_no_cron_or_systemd_timer_required(self):
        s = self.c.schedule_contract()
        self.assertFalse(s["cron_required"])
        self.assertFalse(s["systemd_timer_required"])


if __name__ == "__main__":
    unittest.main()
