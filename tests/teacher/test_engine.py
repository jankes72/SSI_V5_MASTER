import unittest

from ssi_v5.teacher import WorldTeacherEngine, teacher_status


class WorldTeacherEngineTest(unittest.TestCase):
    def setUp(self):
        self.teacher = WorldTeacherEngine()

    def test_status_preserves_60_40_and_20(self):
        status = teacher_status()
        self.assertEqual(status["train_fraction"], 0.60)
        self.assertEqual(status["observation_fraction"], 0.40)
        self.assertEqual(status["retrain_growth_trigger"], 0.20)
        self.assertFalse(status["observation_trains_same_generation"])

    def test_insufficient_data_abstains(self):
        d = self.teacher.decide(
            world_id="WORLD__SPORT", domain_id="DOMAIN__TENNIS",
            sample_count=20, period_count=1, target_diversity=1,
            missing_fraction=0.0, last_train_samples=0,
        )
        self.assertEqual(d.action, "ABSTAIN")

    def test_first_generation_trains_only_when_ready(self):
        d = self.teacher.decide(
            world_id="WORLD__SPORT", domain_id="DOMAIN__TENNIS",
            sample_count=100, period_count=8, target_diversity=3,
            missing_fraction=0.0, last_train_samples=0,
        )
        self.assertEqual(d.action, "TRAIN")
        self.assertGreaterEqual(d.complexity_tier, 2)

    def test_retrain_requires_20_percent_growth(self):
        wait = self.teacher.decide(
            world_id="WORLD__CAPITAL", domain_id="DOMAIN__STOCK",
            sample_count=119, period_count=12, target_diversity=3,
            missing_fraction=0.0, last_train_samples=100,
        )
        self.assertEqual(wait.action, "ABSTAIN")  # readiness still dominates
        ready = self.teacher.decide(
            world_id="WORLD__CAPITAL", domain_id="DOMAIN__STOCK",
            sample_count=144, period_count=12, target_diversity=3,
            missing_fraction=0.0, last_train_samples=120,
        )
        self.assertEqual(ready.action, "RETRAIN")

    def test_wait_when_ready_but_growth_too_small(self):
        d = self.teacher.decide(
            world_id="WORLD__FOREX", domain_id="DOMAIN__CURRENCY_PAIRS",
            sample_count=130, period_count=6, target_diversity=3,
            missing_fraction=0.0, last_train_samples=120,
        )
        self.assertEqual(d.action, "WAIT")

    def test_governance_lineage_is_attached(self):
        d = self.teacher.decide(
            world_id="WORLD__FOREX", domain_id="DOMAIN__CURRENCY_PAIRS",
            sample_count=144, period_count=6, target_diversity=3,
            missing_fraction=0.0, last_train_samples=120,
        )
        self.assertEqual(len(d.governance_ids), 4)
        self.assertEqual(d.governance_ids[0], "ROOT_CONSTITUTION__V1")
        self.assertTrue(d.governance_ids[-1].startswith("DOMAIN_POLICY__FOREX__CURRENCY_PAIRS"))

    def test_teacher_never_claims_execution_or_promotion(self):
        status = teacher_status()
        self.assertEqual(status["role"], "PLAN_ONLY_NO_EXECUTION_NO_PROMOTION")


if __name__ == "__main__":
    unittest.main()
