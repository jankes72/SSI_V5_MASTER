
import tempfile
import unittest
from pathlib import Path

from ssi_v5.behavior.registry import BehaviorHealthFeatureRegistry


class BehaviorHealthFeatureRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = BehaviorHealthFeatureRegistry(Path(self.tmp.name) / "bhf.sqlite3")
        self.common = dict(
            predictor_artifact_id="PREDART__1",
            generation_id="GEN__1",
            world_id="WORLD__SPORT",
            domain_id="DOMAIN__TENNIS",
            network_id="NET__1",
            generation=1,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_behavior_record_is_immutable(self):
        r = self.registry.register(record_kind="BEHAVIOR", payload={"latency_ms": 12}, **self.common)
        self.assertEqual("VALID", self.registry.verify(r.record_id)["status"])
        with self.assertRaises(ValueError):
            self.registry.register(
                record_kind="BEHAVIOR", payload={"latency_ms": 13},
                record_id=r.record_id, **self.common
            )

    def test_health_state_is_validated(self):
        with self.assertRaises(ValueError):
            self.registry.register(record_kind="HEALTH", payload={"health_state": "MAGIC"}, **self.common)

    def test_feature_state_is_validated(self):
        with self.assertRaises(ValueError):
            self.registry.register(record_kind="FEATURE", payload={"feature_state": "MAGIC"}, **self.common)

    def test_health_record_can_be_degraded_without_lifecycle_change(self):
        r = self.registry.register(
            record_kind="HEALTH",
            payload={"health_state": "DEGRADED", "reason": "error_rate"},
            **self.common,
        )
        row = self.registry.get(r.record_id)
        self.assertEqual("DEGRADED", row["payload"]["health_state"])

    def test_feature_drift_is_evidence_only(self):
        r = self.registry.register(
            record_kind="FEATURE",
            payload={"feature_state": "DRIFT", "feature": "serve_speed"},
            **self.common,
        )
        self.assertEqual("VALID", self.registry.verify(r.record_id)["status"])

    def test_lineage_is_required(self):
        bad = dict(self.common)
        bad["generation_id"] = ""
        with self.assertRaises(ValueError):
            self.registry.register(record_kind="BEHAVIOR", payload={}, **bad)

    def test_tamper_is_detected(self):
        r = self.registry.register(record_kind="BEHAVIOR", payload={"x": 1}, **self.common)
        self.registry.conn.execute(
            "UPDATE bhf_records SET payload_json=? WHERE record_id=?",
            ('{"x":2}', r.record_id),
        )
        self.registry.conn.commit()
        self.assertEqual("INVALID", self.registry.verify(r.record_id)["status"])

    def test_prediction_evaluation_lineage_may_be_attached(self):
        r = self.registry.register(
            record_kind="BEHAVIOR",
            payload={"correct": True},
            source_prediction_id="PRED__1",
            source_evaluation_id="EVAL__1",
            **self.common,
        )
        row = self.registry.get(r.record_id)
        self.assertEqual("PRED__1", row["source_prediction_id"])
        self.assertEqual("EVAL__1", row["source_evaluation_id"])

    def test_summary_declares_no_authority(self):
        s = self.registry.summary()
        self.assertFalse(s["lifecycle_authority"])
        self.assertFalse(s["execution_authority"])
        self.assertFalse(s["prediction_mutation_allowed"])

    def test_all_three_record_kinds(self):
        for kind, payload in (
            ("BEHAVIOR", {"value": 1}),
            ("HEALTH", {"health_state": "HEALTHY"}),
            ("FEATURE", {"feature_state": "STABLE"}),
        ):
            self.registry.register(record_kind=kind, payload=payload, **self.common)
        self.assertEqual(3, self.registry.summary()["record_count"])


if __name__ == "__main__":
    unittest.main()
