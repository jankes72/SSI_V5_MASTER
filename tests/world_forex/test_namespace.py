import unittest
from ssi_v5.worlds.registry import CANONICAL_WORLD_REGISTRY, identity_for_legacy

class ForexNamespaceTest(unittest.TestCase):
    def test_pair_world_declared(self):
        self.assertIn('DOMAIN__CURRENCY_PAIRS', CANONICAL_WORLD_REGISTRY['WORLD__FOREX'])
        self.assertIn('DOMAIN__CRYPTO_PAIRS', CANONICAL_WORLD_REGISTRY['WORLD__FOREX'])

    def test_legacy_series_not_lied_about(self):
        x = identity_for_legacy('MARKETS.CURRENCY')
        self.assertEqual(x.world_id, 'WORLD__FOREX')
        self.assertEqual(x.domain_id, 'DOMAIN__CURRENCY_SERIES_LEGACY')
        self.assertEqual(x.status, 'LEGACY_INPUT_ONLY')

if __name__ == '__main__':
    unittest.main()
