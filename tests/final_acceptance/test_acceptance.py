
import tempfile
import unittest
from pathlib import Path

from ssi_v5.freeze.acceptance import CoreAcceptanceRegistry, REQUIRED_CAPABILITIES


class CoreAcceptanceRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = CoreAcceptanceRegistry(Path(self.tmp.name) / "freeze.sqlite3")
        self.ok = {c: True for c in REQUIRED_CAPABILITIES}

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_capabilities_required(self):
        x = dict(self.ok)
        x["director"] = False
        result = self.r.evaluate(
            capability_status=x,
            passed_test_count=316,
            minimum_test_count=316,
        )
        self.assertFalse(result["accepted"])
        self.assertIn("director", result["missing_capabilities"])

    def test_minimum_test_count_required(self):
        result = self.r.evaluate(
            capability_status=self.ok,
            passed_test_count=315,
            minimum_test_count=316,
        )
        self.assertFalse(result["accepted"])

    def test_acceptance_passes(self):
        result = self.r.evaluate(
            capability_status=self.ok,
            passed_test_count=316,
            minimum_test_count=316,
        )
        self.assertTrue(result["accepted"])
        self.assertEqual("PASSED", result["acceptance_status"])

    def test_freeze_record_created(self):
        rec = self.r.freeze(
            core_version="SSI_V5_CORE_1",
            capability_status=self.ok,
            passed_test_count=316,
            minimum_test_count=316,
        )
        self.assertEqual("PASSED", rec.acceptance_status)

    def test_freeze_rejected_if_capability_missing(self):
        x = dict(self.ok)
        x["iskra"] = False
        with self.assertRaises(ValueError):
            self.r.freeze(
                core_version="SSI_V5_CORE_1",
                capability_status=x,
                passed_test_count=316,
                minimum_test_count=316,
            )

    def test_duplicate_freeze_id_rejected(self):
        kw = dict(
            freeze_id="FREEZE__FIXED",
            core_version="SSI_V5_CORE_1",
            capability_status=self.ok,
            passed_test_count=316,
            minimum_test_count=316,
        )
        self.r.freeze(**kw)
        with self.assertRaises(ValueError):
            self.r.freeze(**kw)

    def test_freeze_is_not_execution(self):
        self.assertFalse(self.r.contract()["freeze_is_runtime_execution"])

    def test_freeze_grants_no_new_authority(self):
        self.assertFalse(self.r.contract()["freeze_grants_new_authority"])

    def test_core_boundaries_preserved(self):
        s = self.r.contract()
        self.assertTrue(s["replay_remains_non_execution"])
        self.assertTrue(s["director_remains_internal"])
        self.assertTrue(s["worker_remains_compute_only"])

    def test_post_freeze_change_requires_new_version(self):
        self.assertTrue(self.r.contract()["post_freeze_changes_require_new_version"])


if __name__ == "__main__":
    unittest.main()
