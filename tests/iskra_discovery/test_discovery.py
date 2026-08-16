
import tempfile
import unittest
from pathlib import Path

from ssi_v5.iskra.discovery import IskraDiscoveryEnvironment


class IskraDiscoveryEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = IskraDiscoveryEnvironment(Path(self.tmp.name) / "iskra.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_discovery(self):
        d = self.r.create(
            agent_id="AGENT__1",
            question="What pattern may exist?",
            hypothesis="There may be a relation.",
            method={"kind": "local_analysis"},
            state="DRAFT",
        )
        self.assertEqual("DRAFT", d.state)

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            self.r.create(
                agent_id="AGENT__1",
                question="x",
                hypothesis="y",
                method={},
                state="EXECUTE",
            )

    def test_method_must_be_dict(self):
        with self.assertRaises(ValueError):
            self.r.create(
                agent_id="AGENT__1",
                question="x",
                hypothesis="y",
                method=["bad"],
            )

    def test_duplicate_discovery_id_rejected(self):
        kw = dict(
            discovery_id="ISKRA__FIXED",
            agent_id="AGENT__1",
            question="x",
            hypothesis="y",
            method={},
        )
        self.r.create(**kw)
        with self.assertRaises(ValueError):
            self.r.create(**kw)

    def test_tamper_detected(self):
        d = self.r.create(
            agent_id="AGENT__1",
            question="x",
            hypothesis="y",
            method={},
        )
        self.r.conn.execute(
            "UPDATE discoveries SET hypothesis='tampered' WHERE discovery_id=?",
            (d.discovery_id,),
        )
        self.r.conn.commit()
        self.assertEqual("INVALID", self.r.verify(d.discovery_id)["status"])

    def test_discovery_is_not_canonical_truth(self):
        self.assertFalse(self.r.contract()["discovery_is_canonical_truth"])

    def test_discovery_is_not_execution(self):
        s = self.r.contract()
        self.assertFalse(s["discovery_is_execution"])
        self.assertFalse(s["discovery_creates_compute_job"])

    def test_discovery_cannot_mutate_lifecycle_or_promote(self):
        s = self.r.contract()
        self.assertFalse(s["discovery_mutates_lifecycle"])
        self.assertFalse(s["discovery_promotes_generation"])

    def test_discovery_may_only_cross_as_proposal(self):
        s = self.r.contract()
        self.assertTrue(s["discovery_may_support_proposal"])
        self.assertTrue(s["proposal_boundary_required"])

    def test_iskra_principle_and_uncertainty(self):
        s = self.r.contract()
        self.assertEqual("DESIGN_WORLD_NOT_ANSWER", s["principle"])
        self.assertTrue(s["abstain_is_valid"])
        self.assertTrue(s["conflict_is_preserved"])
        self.assertTrue(s["evidence_required_for_claims"])


if __name__ == "__main__":
    unittest.main()
