import tempfile
import unittest
from pathlib import Path

from ssi_v5.runtime.incremental import SourceCheckpoint, build_source_watermark


class IncrementalCheckpointTest(unittest.TestCase):
    def test_existing_state_bootstraps_without_ingestion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sports = root / "sports"
            data = root / "data"
            sports.mkdir()
            (sports / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            cp = SourceCheckpoint(root / "canonical")
            decision = cp.decide(sports, data, canonical_state_exists=True)
            self.assertEqual(decision.action, "BOOTSTRAP_CHECKPOINT")
            self.assertFalse(decision.should_ingest)

    def test_unchanged_sources_skip_ingestion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sports = root / "sports"
            data = root / "data"
            sports.mkdir()
            (sports / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            cp = SourceCheckpoint(root / "canonical")
            wm = build_source_watermark(sports, data)
            cp.save(wm, cycle_id="C1", mode="TEST")
            decision = cp.decide(sports, data, canonical_state_exists=True)
            self.assertEqual(decision.action, "SKIP_INGESTION")
            self.assertFalse(decision.should_ingest)

    def test_changed_sources_run_ingestion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sports = root / "sports"
            data = root / "data"
            sports.mkdir()
            f = sports / "x.csv"
            f.write_text("a,b\n1,2\n", encoding="utf-8")
            cp = SourceCheckpoint(root / "canonical")
            cp.save(build_source_watermark(sports, data), cycle_id="C1", mode="TEST")
            f.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
            decision = cp.decide(sports, data, canonical_state_exists=True)
            self.assertEqual(decision.action, "RUN_INGESTION")
            self.assertTrue(decision.should_ingest)

    def test_canonical_root_is_not_in_source_roots(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sports = root / "sports"
            data = root / "dane"
            canonical = data / "ssi_canonical"
            sports.mkdir(parents=True)
            canonical.mkdir(parents=True)
            (sports / "a.csv").write_text("x", encoding="utf-8")
            first = build_source_watermark(sports, data)
            (canonical / "queue.sqlite3").write_text("changed", encoding="utf-8")
            second = build_source_watermark(sports, data)
            self.assertEqual(first["fingerprint"], second["fingerprint"])
