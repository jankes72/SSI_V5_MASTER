
import tempfile
import unittest
from pathlib import Path

from ssi_v5.continuum.development import ControlledDevelopmentRegistry


class ControlledDevelopmentRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = ControlledDevelopmentRegistry(Path(self.tmp.name) / "dev.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def make_dev(self, tests=True, evidence=True):
        return self.r.register_development(
            request_id="CONTREQ__1",
            implementation_ref="PATCH__1",
            changed_paths=["ssi_v5/x.py"],
            test_refs=["TEST__1"] if tests else [],
            evidence_refs=["EVID__1"] if evidence else [],
            governance_ref="GOV__1",
        )

    def test_register_development(self):
        d = self.make_dev()
        self.assertEqual("IMPLEMENTED", d.state)

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            self.r.register_development(
                request_id="R1",
                implementation_ref="P1",
                changed_paths=[],
                test_refs=[],
                evidence_refs=[],
                governance_ref="G1",
                state="DEPLOYED",
            )

    def test_duplicate_development_id_rejected(self):
        kw = dict(
            development_id="DEV__FIXED",
            request_id="R1",
            implementation_ref="P1",
            changed_paths=[],
            test_refs=[],
            evidence_refs=[],
            governance_ref="G1",
        )
        self.r.register_development(**kw)
        with self.assertRaises(ValueError):
            self.r.register_development(**kw)

    def test_candidate_requires_tests(self):
        d = self.make_dev(tests=False, evidence=True)
        with self.assertRaises(ValueError):
            self.r.decide_integration(
                development_id=d.development_id,
                decision="APPROVE_CANDIDATE",
                authority_type="DIRECTOR",
                authority_id="D1",
                test_evidence_ref="TE1",
                rationale="x",
            )

    def test_candidate_requires_evidence(self):
        d = self.make_dev(tests=True, evidence=False)
        with self.assertRaises(ValueError):
            self.r.decide_integration(
                development_id=d.development_id,
                decision="APPROVE_CANDIDATE",
                authority_type="DIRECTOR",
                authority_id="D1",
                test_evidence_ref="TE1",
                rationale="x",
            )

    def test_candidate_can_be_approved_with_tests_and_evidence(self):
        d = self.make_dev()
        x = self.r.decide_integration(
            development_id=d.development_id,
            decision="APPROVE_CANDIDATE",
            authority_type="DIRECTOR",
            authority_id="D1",
            test_evidence_ref="TE1",
            rationale="verified",
        )
        self.assertEqual("APPROVE_CANDIDATE", x.decision)

    def test_invalid_authority_rejected(self):
        d = self.make_dev()
        with self.assertRaises(ValueError):
            self.r.decide_integration(
                development_id=d.development_id,
                decision="APPROVE_CANDIDATE",
                authority_type="AGENT",
                authority_id="A1",
                test_evidence_ref="TE1",
                rationale="x",
            )

    def test_implementation_is_not_deployment(self):
        self.assertFalse(self.r.contract()["implementation_is_runtime_deployment"])

    def test_candidate_is_not_automatic_deployment(self):
        s = self.r.contract()
        self.assertFalse(s["integration_candidate_is_runtime_deployment"])
        self.assertFalse(s["runtime_deployment_automatic"])

    def test_continuum_cannot_self_authorize(self):
        s = self.r.contract()
        self.assertFalse(s["continuum_self_authorizes_integration"])
        self.assertTrue(s["authority_review_required"])
        self.assertTrue(s["rollback_required"])


if __name__ == "__main__":
    unittest.main()
