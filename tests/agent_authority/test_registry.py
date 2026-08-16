
import tempfile
import unittest
from pathlib import Path

from ssi_v5.agents.registry import AgentRegistry


class AgentRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = AgentRegistry(Path(self.tmp.name) / "agents.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_agent(self):
        a = self.r.register(
            agent_type="DISCOVERY_AGENT",
            capabilities=["OBSERVE", "PROPOSE_EXPERIMENT"],
            state="ACTIVE",
        )
        self.assertEqual("ACTIVE", a.state)

    def test_identity_is_immutable(self):
        self.r.register(
            agent_id="AGENT__FIXED",
            agent_type="A",
            capabilities=["OBSERVE"],
        )
        with self.assertRaises(ValueError):
            self.r.register(
                agent_id="AGENT__FIXED",
                agent_type="B",
                capabilities=["ANALYZE"],
            )

    def test_unknown_capability_rejected(self):
        with self.assertRaises(ValueError):
            self.r.register(
                agent_type="BAD",
                capabilities=["DIRECT_EXECUTION"],
            )

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            self.r.register(
                agent_type="BAD",
                capabilities=["OBSERVE"],
                state="RUNNING_FOREVER",
            )

    def test_active_agent_can_use_declared_capability(self):
        a = self.r.register(
            agent_type="A",
            capabilities=["OBSERVE"],
            state="ACTIVE",
        )
        self.assertTrue(self.r.can(a.agent_id, "OBSERVE"))

    def test_planned_agent_cannot_use_capability(self):
        a = self.r.register(
            agent_type="A",
            capabilities=["OBSERVE"],
            state="PLANNED",
        )
        self.assertFalse(self.r.can(a.agent_id, "OBSERVE"))

    def test_tamper_detected(self):
        a = self.r.register(
            agent_type="A",
            capabilities=["OBSERVE"],
        )
        self.r.conn.execute(
            "UPDATE agents SET agent_type='TAMPERED' WHERE agent_id=?",
            (a.agent_id,),
        )
        self.r.conn.commit()
        self.assertEqual("INVALID", self.r.verify(a.agent_id)["status"])

    def test_agent_is_not_execution_authority(self):
        s = self.r.authority_contract()
        self.assertFalse(s["agent_can_execute_directly"])
        self.assertFalse(s["agent_can_mutate_lifecycle_directly"])

    def test_agent_is_not_promotion_or_root_authority(self):
        s = self.r.authority_contract()
        self.assertFalse(s["agent_can_promote_directly"])
        self.assertFalse(s["agent_can_root_override"])

    def test_agent_is_not_final_priority_authority(self):
        s = self.r.authority_contract()
        self.assertFalse(s["agent_can_be_final_priority_authority"])
        self.assertFalse(s["trust_is_authority"])
        self.assertFalse(s["capability_is_authority"])


if __name__ == "__main__":
    unittest.main()
