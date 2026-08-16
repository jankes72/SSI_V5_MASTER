
import tempfile
import unittest
from pathlib import Path

from ssi_v5.agents.proposals import AgentProposalRegistry


class AgentProposalRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = AgentProposalRegistry(Path(self.tmp.name) / "proposals.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_proposal(self):
        p = self.r.create(
            agent_id="AGENT__1",
            intent="PROPOSE_EXPERIMENT",
            target_authority="DIRECTOR",
            payload={"topic": "feature drift"},
            rationale="test",
        )
        self.assertEqual("PROPOSE_EXPERIMENT", p.intent)

    def test_unknown_intent_rejected(self):
        with self.assertRaises(ValueError):
            self.r.create(
                agent_id="AGENT__1",
                intent="EXECUTE_NOW",
                target_authority="DIRECTOR",
                payload={},
                rationale="bad",
            )

    def test_unknown_target_rejected(self):
        with self.assertRaises(ValueError):
            self.r.create(
                agent_id="AGENT__1",
                intent="PROPOSE_JOB",
                target_authority="NODE01",
                payload={},
                rationale="bad",
            )

    def test_duplicate_proposal_id_rejected(self):
        kwargs = dict(
            proposal_id="APROP__FIXED",
            agent_id="AGENT__1",
            intent="PROPOSE_JOB",
            target_authority="DIRECTOR",
            payload={},
            rationale="x",
        )
        self.r.create(**kwargs)
        with self.assertRaises(ValueError):
            self.r.create(**kwargs)

    def test_tamper_detected(self):
        p = self.r.create(
            agent_id="AGENT__1",
            intent="PROPOSE_JOB",
            target_authority="DIRECTOR",
            payload={"x": 1},
            rationale="x",
        )
        self.r.conn.execute(
            "UPDATE agent_proposals SET rationale='tampered' WHERE proposal_id=?",
            (p.proposal_id,),
        )
        self.r.conn.commit()
        self.assertEqual("INVALID", self.r.verify(p.proposal_id)["status"])

    def test_proposal_is_not_execution(self):
        s = self.r.contract()
        self.assertFalse(s["proposal_is_execution"])
        self.assertFalse(s["proposal_creates_compute_job"])

    def test_proposal_does_not_resolve_priority(self):
        s = self.r.contract()
        self.assertFalse(s["proposal_resolves_final_priority"])
        self.assertFalse(s["requested_priority_is_authoritative"])

    def test_proposal_does_not_mutate_lifecycle(self):
        s = self.r.contract()
        self.assertFalse(s["proposal_mutates_lifecycle"])
        self.assertFalse(s["proposal_promotes_generation"])

    def test_authority_review_required(self):
        self.assertTrue(self.r.contract()["authority_review_required"])

    def test_payload_must_be_dict(self):
        with self.assertRaises(ValueError):
            self.r.create(
                agent_id="AGENT__1",
                intent="PROPOSE_JOB",
                target_authority="DIRECTOR",
                payload=["bad"],
                rationale="bad",
            )


if __name__ == "__main__":
    unittest.main()
