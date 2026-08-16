import sqlite3
import tempfile
import unittest
from pathlib import Path

from ssi_v5.events.journal import WorldEventJournal


class WorldEventJournalTest(unittest.TestCase):
    def test_append_sequence_and_hash_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = WorldEventJournal(Path(tmp) / "journal.sqlite3")
            a = journal.append(world_id="WORLD__SPORT", domain_id="DOMAIN__TENNIS", cycle_id="C1", event_type="WORLD_CYCLE_STARTED")
            b = journal.append(world_id="WORLD__SPORT", domain_id="DOMAIN__TENNIS", cycle_id="C1", event_type="GOVERNANCE_VALIDATED")
            self.assertEqual((a.sequence_number, b.sequence_number), (1, 2))
            self.assertEqual(b.previous_hash, a.content_hash)
            self.assertEqual(journal.verify_integrity()["status"], "VALID")

    def test_unknown_event_type_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = WorldEventJournal(Path(tmp) / "journal.sqlite3")
            with self.assertRaises(ValueError):
                journal.append(world_id="WORLD__SPORT", cycle_id="C1", event_type="MADE_UP")

    def test_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.sqlite3"
            journal = WorldEventJournal(path)
            journal.append(world_id="WORLD__SPORT", cycle_id="C1", event_type="WORLD_CYCLE_STARTED", payload={"x": 1})
            with sqlite3.connect(path) as con:
                con.execute("UPDATE world_events SET payload_json = '{\"x\":2}' WHERE sequence_number = 1")
            self.assertEqual(journal.verify_integrity()["status"], "INVALID")
