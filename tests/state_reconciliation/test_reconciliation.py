
import tempfile
import unittest
from pathlib import Path

from ssi_v5.recovery.reconciliation import StateReconciler


class StateReconcilerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.r = StateReconciler(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_consistent_state(self):
        x = self.r.evaluate(journal_sequence=10, checkpoint_sequence=10)
        self.assertEqual("CONSISTENT", x["state"])
        self.assertTrue(x["safe_to_resume"])

    def test_checkpoint_behind_requires_replay(self):
        x = self.r.evaluate(journal_sequence=10, checkpoint_sequence=7)
        self.assertEqual("REBUILD_REQUIRED", x["state"])
        self.assertTrue(x["replay_required"])

    def test_checkpoint_ahead_quarantines(self):
        x = self.r.evaluate(journal_sequence=7, checkpoint_sequence=10)
        self.assertEqual("QUARANTINED", x["state"])
        self.assertFalse(x["safe_to_resume"])

    def test_unhealthy_registry_degrades(self):
        x = self.r.evaluate(
            journal_sequence=10,
            checkpoint_sequence=10,
            registry_health={"artifacts": False},
        )
        self.assertEqual("DEGRADED", x["state"])

    def test_no_historical_reexecution(self):
        x = self.r.evaluate(journal_sequence=10, checkpoint_sequence=5)
        self.assertFalse(x["historical_reexecution_required"])

    def test_negative_sequence_rejected(self):
        with self.assertRaises(ValueError):
            self.r.evaluate(journal_sequence=-1, checkpoint_sequence=0)

    def test_record_is_immutable(self):
        self.r.record(
            reconciliation_id="RECON__FIXED",
            journal_sequence=1,
            checkpoint_sequence=1,
        )
        with self.assertRaises(ValueError):
            self.r.record(
                reconciliation_id="RECON__FIXED",
                journal_sequence=2,
                checkpoint_sequence=2,
            )

    def test_restart_does_not_recreate_predictions(self):
        self.assertFalse(self.r.contract()["restart_recreates_predictions"])

    def test_no_destructive_auto_repair(self):
        self.assertFalse(self.r.contract()["automatic_destructive_repair"])

    def test_resume_requires_consistency(self):
        self.assertTrue(self.r.contract()["resume_requires_consistent_state"])


if __name__ == "__main__":
    unittest.main()
