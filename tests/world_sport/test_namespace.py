import unittest
from ssi_v5.worlds.registry import identity_for_legacy

class SportNamespaceTest(unittest.TestCase):
    def test_tennis_mapping(self):
        x = identity_for_legacy('SPORTS.TENNIS')
        self.assertEqual(x.world_id, 'WORLD__SPORT')
        self.assertEqual(x.domain_id, 'DOMAIN__TENNIS')
        self.assertEqual(x.status, 'ACTIVE')

if __name__ == '__main__':
    unittest.main()
