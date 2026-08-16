import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssi_v5.compute.fabric import bootstrap_default_store, sync_node01_worker_status


class _Proc:
    returncode = 0
    stdout = json.dumps({"worker_id":"node-01","status":"IDLE","updated_unix":9999999999,"current_job_id":None})
    stderr = ""


class WorkerSyncTest(unittest.TestCase):
    def test_sync_updates_local_worker_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch("ssi_v5.compute.fabric.subprocess.run", return_value=_Proc()):
                result = sync_node01_worker_status(root)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "IDLE")
            row = bootstrap_default_store(root).summary()["workers"][0]
            self.assertEqual(row["status"], "IDLE")

    def test_reregister_does_not_erase_runtime_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = bootstrap_default_store(root)
            store.conn.execute("UPDATE compute_workers SET status='IDLE',last_heartbeat_unix=123 WHERE worker_id='node-01'")
            store.conn.commit()
            again = bootstrap_default_store(root)
            row = again.summary()["workers"][0]
            self.assertEqual(row["status"], "IDLE")
            self.assertEqual(row["last_heartbeat_unix"], 123)
