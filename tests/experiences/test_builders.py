import unittest

from ssi_v5.experiences import (
    SportExperienceBuilder,
    PairExperienceBuilder,
    CapitalAssetExperienceBuilder,
)


class ExperienceBuilderTest(unittest.TestCase):
    def test_sport_builder_keeps_world_and_raw_lineage(self):
        raw = {
            "source_id": "SRC1", "domain_id": "DOMAIN__TENNIS", "entity_id": "MATCH__1",
            "event_unix": 100, "period_key": "MATCH", "features": {"odds": 1.9},
            "target": 1, "target_complete": True, "verified": True,
        }
        exp = SportExperienceBuilder().build(raw)
        self.assertEqual(exp.world_id, "WORLD__SPORT")
        self.assertTrue(exp.provenance["raw_record_preserved"])
        self.assertEqual(exp.domain_id, "DOMAIN__TENNIS")

    def test_forex_builder_requires_pair_semantics(self):
        raw = {
            "source_id": "SRC2", "domain_id": "DOMAIN__CURRENCY_PAIRS", "base_asset": "EUR",
            "quote_asset": "USD", "event_unix": 200, "period_key": "H1",
            "features": {"mid": 1.1}, "target": 0.01, "target_complete": True,
            "verified": True, "target_time": "T+1H",
        }
        exp = PairExperienceBuilder().build(raw)
        self.assertEqual(exp.entity_id, "EUR/USD")
        self.assertEqual(exp.world_id, "WORLD__FOREX")
        self.assertEqual(exp.quality["pair_semantics"], "EXPLICIT")

    def test_forex_builder_rejects_legacy_series_domain(self):
        raw = {
            "source_id": "SRC2", "domain_id": "DOMAIN__CURRENCY_SERIES_LEGACY", "base_asset": "EUR",
            "quote_asset": "USD", "event_unix": 200, "period_key": "H1",
            "features": {}, "target": 0.0, "target_complete": False,
            "verified": False, "target_time": "T+1H",
        }
        with self.assertRaises(ValueError):
            PairExperienceBuilder().build(raw)

    def test_capital_builder_requires_target_definition(self):
        raw = {
            "source_id": "SRC3", "domain_id": "DOMAIN__STOCK", "entity_id": "AAPL",
            "event_unix": 300, "period_key": "DAY", "features": {"close": 100},
            "target": 0.02, "target_complete": True, "verified": True,
        }
        with self.assertRaises(ValueError):
            CapitalAssetExperienceBuilder().build(raw)

    def test_capital_builder_is_not_forex_pair(self):
        raw = {
            "source_id": "SRC4", "domain_id": "DOMAIN__CURRENCY_ASSET", "entity_id": "USD",
            "event_unix": 400, "period_key": "DAY", "features": {"reference_currency": "EUR"},
            "target": 0.001, "target_complete": True, "verified": True,
            "target_definition": "future_return_vs_reference_currency",
        }
        exp = CapitalAssetExperienceBuilder().build(raw)
        self.assertEqual(exp.world_id, "WORLD__CAPITAL")
        self.assertEqual(exp.entity_id, "USD")
        self.assertEqual(exp.experience_kind, "CAPITAL_ASSET")


if __name__ == '__main__':
    unittest.main()
