
import tempfile
import unittest
from pathlib import Path

from ssi_v5.iskra.proposal_boundary import IskraProposalBoundary


class IskraProposalBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = IskraProposalBoundary(Path(self.tmp.name) / "links.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_link(self):
        x = self.r.create_link(
            discovery_id="ISKRA__1",
            agent_id="AGENT__1",
            intent="PROPOSE_EXPERIMENT",
            target_authority="DIRECTOR",
            proposal_payload={"topic": "relation"},
            evidence_refs=["EVID__1"],
        )
        self.assertEqual("ISKRA__1", x.discovery_id)

    def test_requires_evidence(self):
        with self.assertRaises(ValueError):
            self.r.create_link(
                discovery_id="ISKRA__1",
                agent_id="AGENT__1",
                intent="PROPOSE_EXPERIMENT",
                target_authority="DIRECTOR",
                proposal_payload={},
                evidence_refs=[],
            )

    def test_unknown_intent_rejected(self):
        with self.assertRaises(ValueError):
            self.r.create_link(
                discovery_id="ISKRA__1",
                agent_id="AGENT__1",
                intent="EXECUTE_NOW",
                target_authority="DIRECTOR",
                proposal_payload={},
                evidence_refs=["E1"],
            )

    def test_node01_is_not_target_authority(self):
        with self.assertRaises(ValueError):
            self.r.create_link(
                discovery_id="ISKRA__1",
                agent_id="AGENT__1",
                intent="PROPOSE_JOB",
                target_authority="NODE01",
                proposal_payload={},
                evidence_refs=["E1"],
            )

    def test_duplicate_link_id_rejected(self):
        kw = dict(
            link_id="ISKRALINK__FIXED",
            discovery_id="ISKRA__1",
            agent_id="AGENT__1",
            intent="PROPOSE_FEATURE",
            target_authority="DIRECTOR",
            proposal_payload={},
            evidence_refs=["E1"],
        )
        self.r.create_link(**kw)
        with self.assertRaises(ValueError):
            self.r.create_link(**kw)

    def test_tamper_detected(self):
        x = self.r.create_link(
            discovery_id="ISKRA__1",
            agent_id="AGENT__1",
            intent="PROPOSE_FEATURE",
            target_authority="DIRECTOR",
            proposal_payload={"x": 1},
            evidence_refs=["E1"],
        )
        self.r.conn.execute(
            "UPDATE iskra_proposal_links SET intent='PROPOSE_JOB' WHERE link_id=?",
            (x.link_id,),
        )
        self.r.conn.commit()
        self.assertEqual("INVALID", self.r.verify(x.link_id)["status"])

    def test_discovery_does_not_create_job_or_lifecycle_change(self):
        s = self.r.contract()
        self.assertFalse(s["discovery_directly_creates_job"])
        self.assertFalse(s["discovery_directly_mutates_lifecycle"])

    def test_discovery_cannot_promote_or_resolve_priority(self):
        s = self.r.contract()
        self.assertFalse(s["discovery_directly_promotes_generation"])
        self.assertFalse(s["discovery_resolves_final_priority"])

    def test_review_and_lineage_required(self):
        s = self.r.contract()
        self.assertTrue(s["proposal_review_required"])
        self.assertTrue(s["evidence_lineage_required"])
        self.assertTrue(s["director_or_governance_boundary_required"])

    def test_proposal_is_not_execution(self):
        s = self.r.contract()
        self.assertFalse(s["proposal_is_execution"])
        self.assertTrue(s["node01_is_not_target_authority"])


if __name__ == "__main__":
    unittest.main()
