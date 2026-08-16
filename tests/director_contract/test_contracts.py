
import unittest
from dataclasses import replace

from ssi_v5.director.contracts import DirectorContract


class DirectorContractTest(unittest.TestCase):
    def make_context(self):
        return DirectorContract.create_context(
            checkpoint_event_sequence=15,
            world_id="WORLD__SPORT",
            domain_id="DOMAIN__TENNIS",
            governance_snapshot={"root": "ROOT_CONSTITUTION__V1"},
            queue_snapshot={"jobs": 0},
            lifecycle_snapshot={"active": []},
            evidence_snapshot={"evaluations": []},
        )

    def test_context_hash_verifies(self):
        ctx = self.make_context()
        self.assertTrue(DirectorContract.verify_context(ctx))

    def test_context_tamper_is_detected(self):
        ctx = self.make_context()
        tampered = replace(ctx, world_id="WORLD__FOREX")
        self.assertFalse(DirectorContract.verify_context(tampered))

    def test_output_hash_verifies(self):
        ctx = self.make_context()
        out = DirectorContract.create_output(
            context=ctx,
            decision="NO_ACTION",
            rationale="No actionable evidence.",
        )
        self.assertTrue(DirectorContract.verify_output(out))

    def test_output_tamper_is_detected(self):
        ctx = self.make_context()
        out = DirectorContract.create_output(
            context=ctx,
            decision="NO_ACTION",
            rationale="No actionable evidence.",
        )
        tampered = replace(out, rationale="tampered")
        self.assertFalse(DirectorContract.verify_output(tampered))

    def test_unknown_decision_rejected(self):
        ctx = self.make_context()
        with self.assertRaises(ValueError):
            DirectorContract.create_output(
                context=ctx,
                decision="EXECUTE_NOW",
                rationale="bad",
            )

    def test_confidence_range_enforced(self):
        ctx = self.make_context()
        with self.assertRaises(ValueError):
            DirectorContract.create_output(
                context=ctx,
                decision="NO_ACTION",
                rationale="bad confidence",
                confidence=1.5,
            )

    def test_director_output_is_not_execution(self):
        s = DirectorContract.status()
        self.assertFalse(s["director_output_is_execution"])
        self.assertFalse(s["director_can_enqueue_compute_job_directly"])

    def test_director_cannot_mutate_lifecycle_directly(self):
        s = DirectorContract.status()
        self.assertFalse(s["director_can_mutate_lifecycle_directly"])
        self.assertFalse(s["director_can_promote_directly"])

    def test_priority_authority_is_reserved_for_gate24(self):
        s = DirectorContract.status()
        self.assertEqual("RESERVED_FOR_GATE24", s["director_priority_authority"])

    def test_checkpoint_event_sequence_cannot_be_negative(self):
        with self.assertRaises(ValueError):
            DirectorContract.create_context(
                checkpoint_event_sequence=-1,
                governance_snapshot={},
                queue_snapshot={},
                lifecycle_snapshot={},
                evidence_snapshot={},
            )


if __name__ == "__main__":
    unittest.main()
