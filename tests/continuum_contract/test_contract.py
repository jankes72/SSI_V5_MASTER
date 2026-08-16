
import tempfile
import unittest
from pathlib import Path

from ssi_v5.continuum.contract import ContinuumEngineeringContract


class ContinuumEngineeringContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = ContinuumEngineeringContract(Path(self.tmp.name) / "continuum.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_request(self):
        r = self.r.create_request(
            source_type="DIRECTOR",
            source_id="DOUT__1",
            request_type="IMPLEMENT_FEATURE",
            requirements={"name": "x"},
            evidence_refs=["EVID__1"],
            governance_ref="GOV__1",
        )
        self.assertEqual("REQUESTED", r.state)

    def test_unknown_request_type_rejected(self):
        with self.assertRaises(ValueError):
            self.r.create_request(
                source_type="DIRECTOR",
                source_id="DOUT__1",
                request_type="DEPLOY_NOW",
                requirements={},
                evidence_refs=[],
                governance_ref="GOV__1",
            )

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            self.r.create_request(
                source_type="DIRECTOR",
                source_id="DOUT__1",
                request_type="ADD_TESTS",
                requirements={},
                evidence_refs=[],
                governance_ref="GOV__1",
                state="LIVE",
            )

    def test_governance_ref_required(self):
        with self.assertRaises(ValueError):
            self.r.create_request(
                source_type="DIRECTOR",
                source_id="DOUT__1",
                request_type="ADD_TESTS",
                requirements={},
                evidence_refs=[],
                governance_ref="",
            )

    def test_duplicate_request_id_rejected(self):
        kw = dict(
            request_id="CONTREQ__FIXED",
            source_type="DIRECTOR",
            source_id="DOUT__1",
            request_type="FIX_DEFECT",
            requirements={},
            evidence_refs=[],
            governance_ref="GOV__1",
        )
        self.r.create_request(**kw)
        with self.assertRaises(ValueError):
            self.r.create_request(**kw)

    def test_tamper_detected(self):
        r = self.r.create_request(
            source_type="DIRECTOR",
            source_id="DOUT__1",
            request_type="FIX_DEFECT",
            requirements={},
            evidence_refs=[],
            governance_ref="GOV__1",
        )
        self.r.conn.execute(
            "UPDATE continuum_requests SET request_type='ADD_TESTS' WHERE request_id=?",
            (r.request_id,),
        )
        self.r.conn.commit()
        self.assertEqual("INVALID", self.r.verify(r.request_id)["status"])

    def test_continuum_request_is_not_execution(self):
        self.assertFalse(self.r.contract()["continuum_request_is_execution"])

    def test_continuum_cannot_deploy_or_mutate_lifecycle(self):
        s = self.r.contract()
        self.assertFalse(s["continuum_can_deploy_directly"])
        self.assertFalse(s["continuum_can_mutate_lifecycle"])

    def test_continuum_cannot_bypass_tests_or_governance(self):
        s = self.r.contract()
        self.assertFalse(s["continuum_can_bypass_tests"])
        self.assertFalse(s["continuum_can_bypass_governance"])

    def test_integration_requires_separate_gate(self):
        s = self.r.contract()
        self.assertFalse(s["continuum_can_self_authorize_integration"])
        self.assertTrue(s["integration_requires_separate_gate"])


if __name__ == "__main__":
    unittest.main()
