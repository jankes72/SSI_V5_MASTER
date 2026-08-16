from pathlib import Path
import tempfile
import unittest

from ssi_v5.runtime.manual_cycle import ManualCycleDependencies, ManualWorldCycle


class ManualCycleTest(unittest.TestCase):
    def _deps(self):
        state = {"jobs": 0}
        def summary():
            return {"jobs": [] if state["jobs"] == 0 else [{"status": "QUEUED", "dispatch_status": "LOCAL_ONLY", "n": state["jobs"]}], "by_world": [], "workers": []}
        def pipeline(root, sports_root, data_root, db):
            state["jobs"] = 2
            return {
                "sports_ingestion": {"details": {"SPORTS.TENNIS": {"rows": 10}}},
                "market_capital_ingestion": {"CAPITAL.STOCK": {"rows": 4, "experiences_inserted": 4, "routes": ["A"]}},
                "level1_gate_all_worlds": {"CAPITAL.STOCK": {"status": "WAITING_DATA"}},
                "parent_intelligence": {},
                "compute_queue": summary(),
            }
        return ManualCycleDependencies(
            node_check=lambda: {"ok": True, "target": "fake"},
            collect=lambda: {"collected": 0},
            dispatch=lambda: {"dispatched": 2},
            queue_summary=summary,
            pipeline=pipeline,
        )

    def test_all_world_cycle_creates_invocations_and_journal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "canonical"
            cycle = ManualWorldCycle(root, Path(td) / "sports", Path(td), self._deps())
            out = cycle.run("ALL")
            self.assertEqual(out["status"], "RUN_COMPLETED")
            self.assertEqual(len(out["invocation_artifacts"]), 3)
            self.assertEqual(out["event_integrity"]["status"], "VALID")
            self.assertEqual(out["artifact_integrity"]["status"], "VALID")
            self.assertGreater(out["event_integrity"]["event_count"], 6)

    def test_director_is_not_dependency(self):
        with tempfile.TemporaryDirectory() as td:
            out = ManualWorldCycle(Path(td) / "canonical", Path(td) / "sports", Path(td), self._deps()).run("ALL")
            self.assertFalse(out["director_runtime_required"])

    def test_non_all_selector_is_blocked_until_isolated_pipeline_exists(self):
        with tempfile.TemporaryDirectory() as td:
            cycle = ManualWorldCycle(Path(td) / "canonical", Path(td) / "sports", Path(td), self._deps())
            with self.assertRaisesRegex(ValueError, "all-worlds"):
                cycle.run("SPORT")
