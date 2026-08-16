import tempfile
import unittest
from pathlib import Path

from ssi_v5.lifecycle.generation_registry import GenerationLifecycleRegistry, LifecycleError


class GenerationLifecycleRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = GenerationLifecycleRegistry(Path(self.tmp.name) / "lifecycle.sqlite3")
        self.g = self.registry.create_generation(
            world_id="WORLD__SPORT",
            domain_id="DOMAIN__TENNIS",
            network_id="NET__TENNIS_WINNER",
            generation=1,
            governance_id="WORLD_POLICY__SPORT__V1",
            governance_hash="a" * 64,
        )

    def tearDown(self):
        self.registry.close()
        self.tmp.cleanup()

    def tr(self, state, evidence_kind="SYSTEM", evidence_ref="EV__1", authority_type="WORLD_POLICY"):
        self.g = self.registry.transition(
            self.g.generation_id,
            state,
            authority_type=authority_type,
            authority_id="WORLD_POLICY__SPORT__V1",
            reason=f"TO_{state}",
            evidence_kind=evidence_kind,
            evidence_ref=evidence_ref,
        )
        return self.g

    def test_canonical_happy_path(self):
        self.tr("QUEUED")
        self.tr("TRAINING")
        self.tr("HISTORICALLY_EVALUATED", "HISTORICAL", "EVAL__1")
        self.tr("SHADOW", "HISTORICAL", "EVAL__1")
        self.tr("ACTIVE", "PROSPECTIVE", "PROSPECTIVE__1")
        self.assertEqual(self.g.state, "ACTIVE")

    def test_historical_evidence_cannot_activate(self):
        self.tr("QUEUED")
        self.tr("TRAINING")
        self.tr("HISTORICALLY_EVALUATED", "HISTORICAL", "EVAL__1")
        self.tr("SHADOW", "HISTORICAL", "EVAL__1")
        with self.assertRaises(LifecycleError):
            self.tr("ACTIVE", "HISTORICAL", "EVAL__1")

    def test_active_requires_recognized_authority(self):
        self.tr("QUEUED")
        self.tr("TRAINING")
        self.tr("HISTORICALLY_EVALUATED", "HISTORICAL", "EVAL__1")
        self.tr("SHADOW", "HISTORICAL", "EVAL__1")
        with self.assertRaises(LifecycleError):
            self.tr("ACTIVE", "PROSPECTIVE", "PROSPECTIVE__1", authority_type="AGENT")

    def test_illegal_jump_is_rejected(self):
        with self.assertRaises(LifecycleError):
            self.tr("ACTIVE", "PROSPECTIVE", "PROSPECTIVE__1")

    def test_shadow_requires_historical_evidence(self):
        self.tr("QUEUED")
        self.tr("TRAINING")
        self.tr("HISTORICALLY_EVALUATED", "HISTORICAL", "EVAL__1")
        with self.assertRaises(LifecycleError):
            self.tr("SHADOW", "PROSPECTIVE", "PROSPECTIVE__1")

    def test_terminal_quarantine_cannot_return(self):
        self.tr("QUARANTINED", "CONFLICT", "CONFLICT__1")
        with self.assertRaises(LifecycleError):
            self.tr("PLANNED")

    def test_duplicate_network_generation_rejected(self):
        with self.assertRaises(LifecycleError):
            self.registry.create_generation(
                world_id="WORLD__SPORT",
                domain_id="DOMAIN__TENNIS",
                network_id="NET__TENNIS_WINNER",
                generation=1,
                governance_id="WORLD_POLICY__SPORT__V1",
                governance_hash="b" * 64,
            )

    def test_history_is_hash_chained_and_valid(self):
        self.tr("QUEUED")
        self.tr("TRAINING")
        status = self.registry.verify_integrity()
        self.assertEqual(status["status"], "VALID")
        self.assertEqual(status["transition_count"], 3)

    def test_tamper_is_detected(self):
        self.tr("QUEUED")
        self.registry.conn.execute(
            "UPDATE generation_transitions SET reason='TAMPERED' WHERE sequence_number=1"
        )
        self.registry.conn.commit()
        self.assertEqual(self.registry.verify_integrity()["status"], "INVALID")

    def test_parent_generation_must_be_same_network_and_older(self):
        self.registry.create_generation(
            world_id="WORLD__SPORT",
            domain_id="DOMAIN__TENNIS",
            network_id="NET__TENNIS_WINNER",
            generation=2,
            governance_id="WORLD_POLICY__SPORT__V1",
            governance_hash="a" * 64,
            parent_generation_id=self.g.generation_id,
        )
        with self.assertRaises(LifecycleError):
            self.registry.create_generation(
                world_id="WORLD__SPORT",
                domain_id="DOMAIN__TENNIS",
                network_id="NET__OTHER",
                generation=2,
                governance_id="WORLD_POLICY__SPORT__V1",
                governance_hash="a" * 64,
                parent_generation_id=self.g.generation_id,
            )


if __name__ == "__main__":
    unittest.main()
