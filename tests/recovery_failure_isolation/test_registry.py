
import tempfile
import unittest
from pathlib import Path

from ssi_v5.recovery.registry import RecoveryRegistry


class RecoveryRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = RecoveryRegistry(Path(self.tmp.name) / "recovery.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_failure(self):
        f = self.r.record_failure(
            component_type="NETWORK",
            component_id="NET__1",
            failure_class="NETWORK_FAILURE",
            state="DEGRADED",
            reason="timeout",
            evidence_ref="EVID__1",
        )
        self.assertEqual("DEGRADED", f.state)

    def test_unknown_failure_class_rejected(self):
        with self.assertRaises(ValueError):
            self.r.record_failure(
                component_type="X",
                component_id="X1",
                failure_class="BAD",
                state="FAILED",
                reason="x",
                evidence_ref="E1",
            )

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            self.r.record_failure(
                component_type="X",
                component_id="X1",
                failure_class="UNKNOWN_FAILURE",
                state="DESTROYED",
                reason="x",
                evidence_ref="E1",
            )

    def test_duplicate_failure_id_rejected(self):
        kw = dict(
            failure_id="FAIL__FIXED",
            component_type="WORKER",
            component_id="NODE01",
            failure_class="WORKER_FAILURE",
            state="FAILED",
            reason="x",
            evidence_ref="E1",
        )
        self.r.record_failure(**kw)
        with self.assertRaises(ValueError):
            self.r.record_failure(**kw)

    def test_tamper_detected(self):
        f = self.r.record_failure(
            component_type="AGENT",
            component_id="A1",
            failure_class="AGENT_FAILURE",
            state="QUARANTINED",
            reason="x",
            evidence_ref="E1",
        )
        self.r.conn.execute(
            "UPDATE failures SET reason='tampered' WHERE failure_id=?",
            (f.failure_id,),
        )
        self.r.conn.commit()
        self.assertEqual("INVALID", self.r.verify(f.failure_id)["status"])

    def test_component_failure_does_not_imply_director_failure(self):
        self.assertFalse(
            self.r.contract()["component_failure_implies_director_failure"]
        )

    def test_director_runtime_isolation_required(self):
        self.assertTrue(self.r.contract()["director_runtime_isolation_required"])

    def test_failure_can_degrade_or_quarantine_component(self):
        s = self.r.contract()
        self.assertTrue(s["failed_component_may_be_degraded"])
        self.assertTrue(s["failed_component_may_be_quarantined"])

    def test_recovery_does_not_auto_promote_or_mutate_lifecycle(self):
        s = self.r.contract()
        self.assertFalse(s["automatic_promotion_on_recovery"])
        self.assertFalse(s["automatic_lifecycle_mutation_on_recovery"])

    def test_recovery_requires_verification_and_no_restart_authority(self):
        s = self.r.contract()
        self.assertFalse(s["automatic_restart_authority"])
        self.assertTrue(s["failure_evidence_preserved"])
        self.assertTrue(s["recovery_requires_separate_verification"])


if __name__ == "__main__":
    unittest.main()
