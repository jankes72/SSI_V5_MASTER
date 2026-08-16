import copy
import json
from pathlib import Path
import tempfile
import unittest

from ssi_v5.governance.registry import (
    GovernanceDocument,
    GovernanceRegistry,
    build_default_registry,
)
from ssi_v5.worlds.registry import CANONICAL_WORLD_REGISTRY


class GovernanceRegistryTest(unittest.TestCase):
    def test_default_registry_is_valid(self):
        registry = build_default_registry()
        summary = registry.summary()
        self.assertEqual(summary["status"], "VALID")
        self.assertEqual(summary["active_by_kind"]["ROOT_CONSTITUTION"], 1)
        self.assertEqual(summary["active_by_kind"]["WORLD_CONSTITUTION"], 3)
        self.assertEqual(summary["active_by_kind"]["WORLD_POLICY"], 3)
        self.assertEqual(
            summary["active_by_kind"]["DOMAIN_POLICY"],
            sum(len(v) for v in CANONICAL_WORLD_REGISTRY.values()),
        )

    def test_every_domain_has_complete_chain(self):
        registry = build_default_registry()
        for world_id, domains in CANONICAL_WORLD_REGISTRY.items():
            for domain_id in domains:
                chain = registry.chain_for(world_id, domain_id)
                self.assertEqual(
                    [doc.kind for doc in chain],
                    ["ROOT_CONSTITUTION", "WORLD_CONSTITUTION", "WORLD_POLICY", "DOMAIN_POLICY"],
                )

    def test_hash_tampering_is_rejected(self):
        registry = build_default_registry()
        raw = registry.active("ROOT_CONSTITUTION").as_dict()
        raw["rules"] = dict(raw["rules"])
        raw["rules"]["programmer_root_authority"] = "LOW"
        with self.assertRaises(ValueError):
            GovernanceDocument.from_mapping(raw)

    def test_duplicate_governance_id_is_rejected(self):
        registry = build_default_registry()
        root = registry.active("ROOT_CONSTITUTION")
        with self.assertRaises(ValueError):
            GovernanceRegistry([root, root])

    def test_world_policy_keeps_60_40_and_20_trigger(self):
        registry = build_default_registry()
        for world_id in CANONICAL_WORLD_REGISTRY:
            policy = registry.active("WORLD_POLICY", world_id=world_id)
            self.assertEqual(policy.rules["training_fraction"], 0.60)
            self.assertEqual(policy.rules["observation_fraction"], 0.40)
            self.assertEqual(policy.rules["retrain_growth_trigger"], 0.20)


if __name__ == "__main__":
    unittest.main()
