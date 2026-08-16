
import tempfile
import unittest
from pathlib import Path

from ssi_v5.agents.observations import AgentObservationRegistry


class AgentObservationRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = AgentObservationRegistry(Path(self.tmp.name) / "obs.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_observation(self):
        o = self.r.record(
            agent_id="AGENT__1",
            source_ref="SRC__1",
            payload={"x": 1},
            observed_at_unix=100,
        )
        self.assertEqual("OBSERVED", o.state)

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            self.r.record(
                agent_id="AGENT__1",
                source_ref="SRC__1",
                payload={},
                observed_at_unix=100,
                state="BAD",
            )

    def test_duplicate_observation_id_rejected(self):
        kw = dict(
            observation_id="AOBS__FIXED",
            agent_id="AGENT__1",
            source_ref="SRC__1",
            payload={},
            observed_at_unix=100,
        )
        self.r.record(**kw)
        with self.assertRaises(ValueError):
            self.r.record(**kw)

    def test_tamper_detected(self):
        o = self.r.record(
            agent_id="AGENT__1",
            source_ref="SRC__1",
            payload={"x": 1},
            observed_at_unix=100,
        )
        self.r.conn.execute(
            "UPDATE agent_observations SET state='QUARANTINED' WHERE observation_id=?",
            (o.observation_id,),
        )
        self.r.conn.commit()
        self.assertEqual("INVALID", self.r.verify(o.observation_id)["status"])

    def test_observation_is_not_experience(self):
        self.assertFalse(self.r.contract()["observation_is_experience"])

    def test_same_generation_training_is_forbidden(self):
        self.assertFalse(
            self.r.contract()["observation_is_training_material_same_generation"]
        )

    def test_observation_does_not_mutate_world_or_lifecycle(self):
        s = self.r.contract()
        self.assertFalse(s["observation_mutates_canonical_world_state"])
        self.assertFalse(s["observation_mutates_lifecycle"])

    def test_observation_does_not_create_prediction(self):
        self.assertFalse(self.r.contract()["observation_creates_prediction"])

    def test_unknown_and_conflict_are_preserved(self):
        s = self.r.contract()
        self.assertTrue(s["conflicting_observation_preserved"])
        self.assertTrue(s["unknown_observation_preserved"])

    def test_promotion_requires_separate_pipeline(self):
        self.assertTrue(self.r.contract()["promotion_requires_separate_pipeline"])


if __name__ == "__main__":
    unittest.main()
