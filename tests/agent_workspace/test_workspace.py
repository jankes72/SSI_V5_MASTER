
import tempfile
import unittest
from pathlib import Path

from ssi_v5.agents.workspace import AgentWorkspace


class AgentWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.w = AgentWorkspace(self.root, "AGENT__1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_workspace_is_agent_scoped(self):
        self.assertIn("AGENT__1", str(self.w.workspace_dir))

    def test_remember_local_record(self):
        r = self.w.remember(category="OBSERVATION", payload={"x": 1})
        self.assertEqual("AGENT__1", r.agent_id)

    def test_duplicate_memory_id_rejected(self):
        self.w.remember(memory_id="AMEM__FIXED", category="X", payload={})
        with self.assertRaises(ValueError):
            self.w.remember(memory_id="AMEM__FIXED", category="Y", payload={})

    def test_payload_must_be_dict(self):
        with self.assertRaises(ValueError):
            self.w.remember(category="X", payload=["bad"])

    def test_tamper_detected(self):
        r = self.w.remember(category="X", payload={"x": 1})
        self.w.conn.execute(
            "UPDATE local_memory SET category='TAMPERED' WHERE memory_id=?",
            (r.memory_id,),
        )
        self.w.conn.commit()
        self.assertEqual("INVALID", self.w.verify(r.memory_id)["status"])

    def test_memory_is_not_canonical_world_state(self):
        s = self.w.evidence_contract()
        self.assertFalse(s["local_memory_is_canonical_world_state"])

    def test_agent_cannot_mutate_event_journal(self):
        s = self.w.evidence_contract()
        self.assertFalse(s["agent_can_mutate_event_journal"])

    def test_agent_cannot_mutate_global_artifacts(self):
        s = self.w.evidence_contract()
        self.assertFalse(s["agent_can_mutate_global_artifact_registry"])

    def test_agent_cannot_mutate_lifecycle_or_world_state(self):
        s = self.w.evidence_contract()
        self.assertFalse(s["agent_can_mutate_lifecycle"])
        self.assertFalse(s["agent_can_overwrite_canonical_world_state"])

    def test_evidence_ref_does_not_grant_authority(self):
        s = self.w.evidence_contract()
        self.assertTrue(s["local_memory_may_reference_evidence"])
        self.assertTrue(s["evidence_reference_does_not_grant_authority"])


if __name__ == "__main__":
    unittest.main()
