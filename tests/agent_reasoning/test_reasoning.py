
import tempfile
import unittest
from pathlib import Path

from ssi_v5.agents.reasoning import AgentReasoningRegistry


class AgentReasoningRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = AgentReasoningRegistry(Path(self.tmp.name) / "reasoning.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_reasoning(self):
        rec = self.r.create(
            agent_id="AGENT__1",
            hypothesis="Possible drift.",
            analysis={"signals": 2},
            state="READY",
        )
        self.assertEqual("READY", rec.state)

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            self.r.create(
                agent_id="AGENT__1",
                hypothesis="x",
                analysis={},
                state="EXECUTE",
            )

    def test_confidence_range_enforced(self):
        with self.assertRaises(ValueError):
            self.r.create(
                agent_id="AGENT__1",
                hypothesis="x",
                analysis={},
                confidence=1.1,
            )

    def test_duplicate_reasoning_id_rejected(self):
        kw = dict(
            reasoning_id="AREASON__FIXED",
            agent_id="AGENT__1",
            hypothesis="x",
            analysis={},
        )
        self.r.create(**kw)
        with self.assertRaises(ValueError):
            self.r.create(**kw)

    def test_tamper_detected(self):
        rec = self.r.create(
            agent_id="AGENT__1",
            hypothesis="x",
            analysis={"a": 1},
        )
        self.r.conn.execute(
            "UPDATE agent_reasoning SET hypothesis='tampered' WHERE reasoning_id=?",
            (rec.reasoning_id,),
        )
        self.r.conn.commit()
        self.assertEqual("INVALID", self.r.verify(rec.reasoning_id)["status"])

    def test_local_reasoning_is_not_authority(self):
        self.assertFalse(self.r.boundary_contract()["local_reasoning_is_authority"])

    def test_local_strategy_is_not_execution(self):
        s = self.r.boundary_contract()
        self.assertFalse(s["local_strategy_is_execution"])
        self.assertFalse(s["local_strategy_mutates_world_state"])

    def test_local_strategy_cannot_change_lifecycle_or_priority(self):
        s = self.r.boundary_contract()
        self.assertFalse(s["local_strategy_mutates_lifecycle"])
        self.assertFalse(s["local_strategy_resolves_final_priority"])

    def test_reasoning_may_only_cross_boundary_as_proposal(self):
        s = self.r.boundary_contract()
        self.assertTrue(s["local_reasoning_may_create_proposal"])
        self.assertTrue(s["proposal_boundary_required"])

    def test_abstain_and_conflict_are_valid(self):
        s = self.r.boundary_contract()
        self.assertTrue(s["abstain_is_valid"])
        self.assertTrue(s["conflict_is_preserved"])


if __name__ == "__main__":
    unittest.main()
