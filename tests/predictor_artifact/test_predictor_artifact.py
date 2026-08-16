from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ssi_v5.lifecycle.generation_registry import GenerationLifecycleRegistry
from ssi_v5.predictors.artifact import PredictorArtifactError, PredictorArtifactRegistry


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PredictorArtifactTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "canonical"
        self.life = GenerationLifecycleRegistry(self.root / "lifecycle" / "generation_lifecycle.sqlite3")
        self.gen = self.life.create_generation(
            world_id="WORLD__SPORT", domain_id="DOMAIN__TENNIS", network_id="NET__TENNIS_MAIN",
            generation=1, governance_id="WORLD_POLICY__SPORT__V1", governance_hash="abc123",
        )
        self.life.transition(
            self.gen.generation_id, "QUEUED", authority_type="WORLD_POLICY", authority_id="WORLD_POLICY__SPORT__V1",
            reason="TEST", evidence_kind="GOVERNANCE", evidence_ref="WORLD_POLICY__SPORT__V1",
        )
        self.life.transition(
            self.gen.generation_id, "TRAINING", authority_type="WORLD_POLICY", authority_id="WORLD_POLICY__SPORT__V1",
            reason="TEST", evidence_kind="JOB", evidence_ref="JOB__1",
        )
        self.gen = self.life.get(self.gen.generation_id)
        self.registry = PredictorArtifactRegistry(self.root)
        self.source = Path(self.tmp.name) / "result"
        self.source.mkdir()
        (self.source / "model.joblib").write_bytes(b"MODEL-BYTES")
        (self.source / "feature_schema.json").write_text('{"features":["x"]}', encoding="utf-8")
        (self.source / "metrics.json").write_text('{"mae":0.1}', encoding="utf-8")
        self.write_manifest()

    def tearDown(self):
        self.registry.close()
        self.life.close()
        self.tmp.cleanup()

    def write_manifest(self, **updates):
        m = {
            "world_id": "WORLD__SPORT", "domain_id": "DOMAIN__TENNIS", "network_id": "NET__TENNIS_MAIN",
            "generation": 1, "generation_id": self.gen.generation_id,
            "governance_id": "WORLD_POLICY__SPORT__V1", "governance_hash": "abc123",
            "source_job_id": "JOB__1", "source_envelope_id": "JOBENV__1",
            "file_hashes": {
                "model.joblib": sha(self.source / "model.joblib"),
                "feature_schema.json": sha(self.source / "feature_schema.json"),
                "metrics.json": sha(self.source / "metrics.json"),
            },
        }
        m.update(updates)
        (self.source / "predictor_manifest.json").write_text(json.dumps(m), encoding="utf-8")

    def test_register_and_verify(self):
        rec = self.registry.register_from_directory(self.source, generation_id=self.gen.generation_id)
        self.assertEqual(self.registry.verify(rec.predictor_artifact_id)["status"], "VALID")

    def test_generation_must_exist(self):
        with self.assertRaises(PredictorArtifactError):
            self.registry.register_from_directory(self.source, generation_id="GEN__UNKNOWN")

    def test_planned_or_queued_generation_rejected(self):
        gen2 = self.life.create_generation(
            world_id="WORLD__SPORT", domain_id="DOMAIN__TENNIS", network_id="NET__SECOND", generation=1,
            governance_id="WORLD_POLICY__SPORT__V1", governance_hash="abc123",
        )
        with self.assertRaises(PredictorArtifactError):
            self.registry.register_from_directory(self.source, generation_id=gen2.generation_id)

    def test_lineage_mismatch_rejected(self):
        self.write_manifest(network_id="NET__OTHER")
        with self.assertRaises(PredictorArtifactError):
            self.registry.register_from_directory(self.source, generation_id=self.gen.generation_id)

    def test_hash_mismatch_rejected(self):
        self.write_manifest()
        (self.source / "model.joblib").write_bytes(b"TAMPER")
        with self.assertRaises(PredictorArtifactError):
            self.registry.register_from_directory(self.source, generation_id=self.gen.generation_id)

    def test_source_job_required(self):
        self.write_manifest(source_job_id="", job_id="")
        with self.assertRaises(PredictorArtifactError):
            self.registry.register_from_directory(self.source, generation_id=self.gen.generation_id)

    def test_duplicate_generation_artifact_rejected(self):
        self.registry.register_from_directory(self.source, generation_id=self.gen.generation_id)
        with self.assertRaises(PredictorArtifactError):
            self.registry.register_from_directory(self.source, generation_id=self.gen.generation_id)

    def test_tamper_after_registration_detected(self):
        rec = self.registry.register_from_directory(self.source, generation_id=self.gen.generation_id)
        path = Path(rec.storage_path) / "metrics.json"
        path.write_text('{"mae":999}', encoding="utf-8")
        result = self.registry.verify(rec.predictor_artifact_id)
        self.assertEqual(result["status"], "INVALID")
        self.assertTrue(any("metrics.json" in e for e in result["errors"]))

    def test_required_material_missing(self):
        (self.source / "metrics.json").unlink()
        with self.assertRaises(PredictorArtifactError):
            self.registry.register_from_directory(self.source, generation_id=self.gen.generation_id)

    def test_manifest_has_no_authority(self):
        rec = self.registry.register_from_directory(self.source, generation_id=self.gen.generation_id)
        manifest = json.loads((Path(rec.storage_path) / "predictor_artifact.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["execution_authority"])
        self.assertFalse(manifest["promotion_authority"])


if __name__ == "__main__":
    unittest.main()
