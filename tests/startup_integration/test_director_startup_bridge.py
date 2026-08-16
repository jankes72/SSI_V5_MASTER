
import unittest
from pathlib import Path
from dataclasses import replace

from ssi_v5.director.contracts import DirectorContract
from ssi_v5.director.startup_bridge import DirectorStartupBridge


class DirectorStartupBridgeTest(unittest.TestCase):
    def setUp(self):
        self.bridge = DirectorStartupBridge(Path("/tmp/ssi-test"))

    def context(self):
        return self.bridge.build_context(
            checkpoint_event_sequence=1,
            governance_snapshot={"root": "ROOT_CONSTITUTION__V1"},
            queue_snapshot={},
            lifecycle_snapshot={},
            evidence_snapshot={},
        )

    def test_status_declares_internal_runtime(self):
        s = self.bridge.status()
        self.assertFalse(s["public_runtime"])
        self.assertTrue(s["starter_owned"])

    def test_start_ssi_remains_primary_entrypoint(self):
        self.assertTrue(self.bridge.status()["start_ssi_remains_primary_entrypoint"])

    def test_worlds_may_run_without_director(self):
        self.assertTrue(self.bridge.status()["worlds_can_run_without_director"])

    def test_director_output_is_not_execution(self):
        self.assertFalse(self.bridge.status()["director_output_is_execution"])
        self.assertFalse(self.bridge.status()["automatic_execution"])

    def test_context_built_through_contract(self):
        ctx = self.context()
        self.assertTrue(DirectorContract.verify_context(ctx))

    def test_valid_output_is_only_recommendation(self):
        ctx = self.context()
        out = DirectorContract.create_output(
            context=ctx,
            decision="NO_ACTION",
            rationale="No action required.",
        )
        accepted = self.bridge.accept_output(out)
        self.assertEqual("ACCEPTED_AS_RECOMMENDATION", accepted["status"])
        self.assertFalse(accepted["execution_started"])

    def test_tampered_output_rejected(self):
        ctx = self.context()
        out = DirectorContract.create_output(
            context=ctx,
            decision="NO_ACTION",
            rationale="No action required.",
        )
        bad = replace(out, rationale="tampered")
        with self.assertRaises(ValueError):
            self.bridge.accept_output(bad)

    def test_accept_output_does_not_resolve_priority(self):
        ctx = self.context()
        out = DirectorContract.create_output(
            context=ctx,
            decision="REQUEST_PRIORITY_REVIEW",
            rationale="Review requested.",
        )
        accepted = self.bridge.accept_output(out)
        self.assertFalse(accepted["priority_resolved"])

    def test_accept_output_does_not_change_lifecycle(self):
        ctx = self.context()
        out = DirectorContract.create_output(
            context=ctx,
            decision="REQUEST_PROMOTION_REVIEW",
            rationale="Review promotion.",
        )
        accepted = self.bridge.accept_output(out)
        self.assertFalse(accepted["lifecycle_changed"])

    def test_director_priority_authority_still_reserved(self):
        self.assertEqual("RESERVED_FOR_GATE24", self.bridge.status()["director_priority_authority"])


if __name__ == "__main__":
    unittest.main()
