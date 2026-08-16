import unittest
from ssi_v5.worlds.registry import CANONICAL_WORLD_REGISTRY, identity_for_legacy

class CapitalNamespaceTest(unittest.TestCase):
    def test_stock_mapping(self):
        x = identity_for_legacy('CAPITAL.STOCK')
        self.assertEqual((x.world_id, x.domain_id), ('WORLD__CAPITAL', 'DOMAIN__STOCK'))

    def test_future_asset_domains_reserved(self):
        domains = CANONICAL_WORLD_REGISTRY['WORLD__CAPITAL']
        self.assertIn('DOMAIN__CURRENCY_ASSET', domains)
        self.assertIn('DOMAIN__CRYPTO_ASSET', domains)

if __name__ == '__main__':
    unittest.main()
