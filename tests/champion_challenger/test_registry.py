
import tempfile
import unittest
from pathlib import Path

from ssi_v5.champion.registry import ChampionChallengerRegistry


class ChampionChallengerRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = ChampionChallengerRegistry(Path(self.tmp.name) / "cc.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_child_lineage(self):
        row = self.r.register_child(
            network_id="NET__1",
            child_generation_id="GEN__2",
            parent_generation_id="GEN__1",
        )
        self.assertEqual("GEN__1", row["parent_generation_id"])

    def test_child_lineage_is_immutable(self):
        self.r.register_child(
            network_id="NET__1",
            child_generation_id="GEN__2",
            parent_generation_id="GEN__1",
        )
        with self.assertRaises(ValueError):
            self.r.register_child(
                network_id="NET__1",
                child_generation_id="GEN__2",
                parent_generation_id="GEN__X",
            )

    def test_self_parent_rejected(self):
        with self.assertRaises(ValueError):
            self.r.register_child(
                network_id="NET__1",
                child_generation_id="GEN__1",
                parent_generation_id="GEN__1",
            )

    def test_historical_evidence_cannot_promote(self):
        with self.assertRaises(ValueError):
            self.r.decide(
                network_id="NET__1",
                champion_generation_id="GEN__1",
                challenger_generation_id="GEN__2",
                decision="PROMOTE",
                authority_type="WORLD_POLICY",
                authority_id="WP__1",
                evidence_scope="HISTORICAL",
                evidence_ref="EVAL__1",
                reason="historical only",
            )

    def test_prospective_evidence_can_support_promote(self):
        d = self.r.decide(
            network_id="NET__1",
            champion_generation_id="GEN__1",
            challenger_generation_id="GEN__2",
            decision="PROMOTE",
            authority_type="WORLD_POLICY",
            authority_id="WP__1",
            evidence_scope="PROSPECTIVE",
            evidence_ref="EVAL__2",
            reason="prospective evidence",
        )
        self.assertEqual("PROMOTE", d.decision)

    def test_non_authority_rejected(self):
        with self.assertRaises(ValueError):
            self.r.decide(
                network_id="NET__1",
                challenger_generation_id="GEN__2",
                decision="REJECT",
                authority_type="AGENT",
                authority_id="AGENT__1",
                evidence_scope="PROSPECTIVE",
                evidence_ref="EVAL__2",
                reason="no authority",
            )

    def test_keep_as_shadow_allowed(self):
        d = self.r.decide(
            network_id="NET__1",
            challenger_generation_id="GEN__2",
            decision="KEEP_AS_SHADOW",
            authority_type="WORLD_POLICY",
            authority_id="WP__1",
            evidence_scope="HISTORICAL",
            evidence_ref="EVAL__1",
            reason="needs more evidence",
        )
        self.assertEqual("KEEP_AS_SHADOW", d.decision)

    def test_decision_tamper_detected(self):
        d = self.r.decide(
            network_id="NET__1",
            challenger_generation_id="GEN__2",
            decision="REJECT",
            authority_type="ROOT",
            authority_id="ROOT__1",
            evidence_scope="PROSPECTIVE",
            evidence_ref="EVAL__1",
            reason="bad",
        )
        self.r.conn.execute(
            "UPDATE champion_decisions SET reason='tampered' WHERE decision_id=?",
            (d.decision_id,),
        )
        self.r.conn.commit()
        self.assertEqual("INVALID", self.r.verify_decision(d.decision_id)["status"])

    def test_duplicate_decision_id_rejected(self):
        kw = dict(
            network_id="NET__1",
            challenger_generation_id="GEN__2",
            decision="REJECT",
            authority_type="ROOT",
            authority_id="ROOT__1",
            evidence_scope="PROSPECTIVE",
            evidence_ref="EVAL__1",
            reason="bad",
            decision_id="CCD__FIXED",
        )
        self.r.decide(**kw)
        with self.assertRaises(ValueError):
            self.r.decide(**kw)

    def test_summary_declares_no_worker_or_lifecycle_authority(self):
        s = self.r.summary()
        self.assertFalse(s["worker_has_champion_authority"])
        self.assertFalse(s["lifecycle_state_changed_here"])
        self.assertFalse(s["automatic_promotion"])


if __name__ == "__main__":
    unittest.main()
