
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ssi_v5.replay.engine import ReplayRebuildEngine


def make_db(root: Path):
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
    rows = [
        ("E1",1,"WORLD__SPORT","DOMAIN__TENNIS","C1","WORLD_CYCLE_STARTED","{}","[]",1,"H1"),
        ("E2",2,"WORLD__SPORT","DOMAIN__TENNIS","C1","WORLD_CYCLE_COMPLETED","{}","[]",2,"H2"),
        ("E3",3,"WORLD__FOREX","DOMAIN__CURRENCY_PAIRS","C2","WORLD_CYCLE_STARTED","{}","[]",3,"H3"),
    ]
    conn.executemany("INSERT INTO world_events VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class ReplayRebuildEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_db(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rebuild_reads_events_in_order(self):
        r = ReplayRebuildEngine(self.root).rebuild_state()
        self.assertEqual(3, r["event_count"])
        self.assertEqual(3, r["state"]["last_sequence_number"])

    def test_rebuild_is_deterministic(self):
        r = ReplayRebuildEngine(self.root).verify_determinism()
        self.assertTrue(r["deterministic"])
        self.assertEqual(r["first_state_hash"], r["second_state_hash"])

    def test_replay_does_not_execute_jobs(self):
        s = ReplayRebuildEngine(self.root).summary()
        self.assertFalse(s["replay_executes_jobs"])

    def test_replay_does_not_create_predictions(self):
        s = ReplayRebuildEngine(self.root).summary()
        self.assertFalse(s["replay_creates_predictions"])

    def test_replay_does_not_create_outcomes(self):
        s = ReplayRebuildEngine(self.root).summary()
        self.assertFalse(s["replay_creates_outcomes"])

    def test_replay_does_not_mutate_source_registries(self):
        s = ReplayRebuildEngine(self.root).summary()
        self.assertFalse(s["replay_mutates_source_registries"])

    def test_replay_requires_no_network(self):
        s = ReplayRebuildEngine(self.root).summary()
        self.assertFalse(s["replay_requires_network_access"])

    def test_sequence_gap_is_invalid(self):
        db = self.root / "events" / "world_event_journal.sqlite3"
        conn = sqlite3.connect(db)
        conn.execute("UPDATE world_events SET sequence_number=7 WHERE event_id='E2'")
        conn.commit()
        conn.close()
        r = ReplayRebuildEngine(self.root).rebuild_state()
        self.assertEqual("INVALID", r["status"])

    def test_world_domain_counts_rebuild(self):
        r = ReplayRebuildEngine(self.root).rebuild_state()
        self.assertEqual(2, r["state"]["worlds"]["WORLD__SPORT"]["event_count"])
        self.assertEqual(2, r["state"]["worlds"]["WORLD__SPORT"]["domains"]["DOMAIN__TENNIS"])

    def test_empty_journal_is_valid(self):
        other = Path(self.tmp.name) / "empty"
        r = ReplayRebuildEngine(other).summary()
        self.assertEqual("READY", r["status"])
        self.assertEqual(0, r["event_count"])


if __name__ == "__main__":
    unittest.main()
