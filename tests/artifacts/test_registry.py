import tempfile
import unittest
from pathlib import Path

from ssi_v5.artifacts.registry import ArtifactRegistry


class ArtifactRegistryTest(unittest.TestCase):
    def test_register_json_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ArtifactRegistry(Path(tmp))
            record = registry.register_json(
                {"prediction": 0.61},
                artifact_kind="PREDICTION_SET",
                world_id="WORLD__SPORT",
                domain_id="DOMAIN__TENNIS",
                entity_id="MATCH__1",
                route_key="ROUTE__PRIMARY",
                generation=1,
                governance_refs=["ROOT_CONSTITUTION__V1"],
            )
            self.assertTrue((Path(tmp) / record.storage_relpath).is_file())
            self.assertEqual(registry.verify_integrity()["status"], "VALID")

    def test_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ArtifactRegistry(root)
            record = registry.register_json(
                {"x": 1}, artifact_kind="RAW_EVIDENCE", world_id="WORLD__FOREX", domain_id="DOMAIN__CURRENCY_PAIRS"
            )
            (root / record.storage_relpath).write_text('{"x":999}\n', encoding="utf-8")
            self.assertEqual(registry.verify_integrity()["status"], "INVALID")

    def test_same_artifact_id_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ArtifactRegistry(Path(tmp))
            kwargs = dict(artifact_kind="HEALTH", world_id="WORLD__CAPITAL", domain_id="DOMAIN__STOCK", artifact_id="ART__FIXED")
            registry.register_json({"health": "OK"}, **kwargs)
            with self.assertRaises(ValueError):
                registry.register_json({"health": "BAD"}, **kwargs)

    def test_generation_path_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ArtifactRegistry(Path(tmp))
            record = registry.register_json(
                {"model": "x"}, artifact_kind="NETWORK_GENERATION", world_id="WORLD__CAPITAL",
                domain_id="DOMAIN__STOCK", entity_id="AAPL", route_key="ROUTE__MAIN", generation=12,
            )
            self.assertIn("generations/GEN__000012", record.storage_relpath)
