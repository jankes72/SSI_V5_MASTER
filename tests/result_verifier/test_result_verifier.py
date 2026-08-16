import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ssi_v5.compute.fabric import (
    ComputeJob,
    ComputeQueueStore,
    JobBundleBuilder,
    ResourceRequest,
    collect_remote_results,
)
from ssi_v5.compute.result_verifier import ResultBundleVerifier, result_verifier_status


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


class FakeTransport:
    def __init__(self, result_dir: Path):
        self.result_dir = Path(result_dir)
        self.acked = []

    def list_result_names(self):
        return [self.result_dir.name]

    def fetch_result(self, result_name, local_root):
        target = Path(local_root) / result_name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.result_dir, target)
        return target

    def acknowledge_result(self, result_name):
        self.acked.append(result_name)


class ResultVerifierTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.queue = ComputeQueueStore(self.root / "queue.sqlite3")
        self.builder = JobBundleBuilder(self.root / "outbox")

    def tearDown(self):
        self.tmp.cleanup()

    def make_job(self, *, envelope=True):
        payload = {"message": "health"}
        if envelope:
            payload["job_envelope"] = {
                "envelope_id": "JOBENV__TEST",
                "source": "SYSTEM",
                "source_id": "TEST",
                "requested_priority_class": "P0_SYSTEM_CRITICAL",
                "priority_resolution": {
                    "authority_type": "WORLD_POLICY",
                    "authority_id": "ROOT_CONSTITUTION__V1",
                    "final_priority_class": "P9_MAINTENANCE",
                    "reason": "test",
                },
                "governance_refs": [],
                "evidence_refs": [],
                "content_hash": "abc123",
            }
        return ComputeJob.create(
            job_type="HEALTHCHECK",
            priority_class="P9_MAINTENANCE",
            world_key="WORLD__SYSTEM",
            domain_key="DOMAIN__NODE01_HEALTHCHECK",
            discipline="SYSTEM",
            owner_type="SYSTEM",
            owner_id="TEST",
            route_key="NODE01_HEALTHCHECK",
            network_id=None,
            generation=None,
            payload=payload,
            resources=ResourceRequest(cpu_cores=1, ram_mb=128, gpu_mode="NONE"),
        )

    def make_result_bundle(self, job: ComputeJob):
        bundle = self.builder.build(job)
        self.queue.enqueue(job, local_bundle_path=str(bundle))
        result_dir = self.root / "remote" / f"{job.job_id}.done"
        result_dir.mkdir(parents=True)
        shutil.copy2(bundle / "job.json", result_dir / "job.json")
        shutil.copy2(bundle / "manifest.json", result_dir / "input_manifest.json")
        write_json(result_dir / "result.json", {
            "status": "COMPLETED",
            "job_id": job.job_id,
            "worker_id": "node-01",
            "world_key": job.world_key,
            "domain_key": job.domain_key,
            "discipline": job.discipline,
            "network_id": job.network_id,
            "generation": job.generation,
        })
        files = sorted(p for p in result_dir.rglob("*") if p.is_file())
        write_json(result_dir / "result_manifest.json", {
            "job_id": job.job_id,
            "worker_id": "node-01",
            "files": [
                {"path": str(p.relative_to(result_dir)), "size": p.stat().st_size, "sha256": sha256_file(p)}
                for p in files
            ],
        })
        return result_dir

    def test_status_requires_verification(self):
        s = result_verifier_status()
        self.assertFalse(s["accept_before_verification"])
        self.assertTrue(s["result_manifest_hashes_required"])

    def test_valid_bundle_is_verified(self):
        job = self.make_job()
        result_dir = self.make_result_bundle(job)
        v = ResultBundleVerifier(self.queue).verify(result_dir)
        self.assertTrue(v.ok, v.errors)
        self.assertEqual(v.status, "VERIFIED")
        self.assertEqual(v.envelope_id, "JOBENV__TEST")

    def test_legacy_job_without_envelope_can_be_verified(self):
        job = self.make_job(envelope=False)
        result_dir = self.make_result_bundle(job)
        v = ResultBundleVerifier(self.queue).verify(result_dir)
        self.assertTrue(v.ok, v.errors)
        self.assertTrue(v.checks["envelope_lineage"])

    def test_unknown_job_is_rejected(self):
        job = self.make_job()
        result_dir = self.make_result_bundle(job)
        self.queue.conn.execute("DELETE FROM compute_jobs WHERE job_id=?", (job.job_id,))
        self.queue.conn.commit()
        v = ResultBundleVerifier(self.queue).verify(result_dir)
        self.assertFalse(v.ok)
        self.assertFalse(v.checks["job_exists_in_queue"])

    def test_tampered_job_json_is_rejected(self):
        job = self.make_job()
        result_dir = self.make_result_bundle(job)
        data = json.loads((result_dir / "job.json").read_text())
        data["owner_id"] = "ATTACKER"
        write_json(result_dir / "job.json", data)
        v = ResultBundleVerifier(self.queue).verify(result_dir)
        self.assertFalse(v.ok)

    def test_tampered_result_file_is_rejected(self):
        job = self.make_job()
        result_dir = self.make_result_bundle(job)
        data = json.loads((result_dir / "result.json").read_text())
        data["status"] = "FAILED"
        write_json(result_dir / "result.json", data)
        v = ResultBundleVerifier(self.queue).verify(result_dir)
        self.assertFalse(v.ok)
        self.assertFalse(v.checks["result_file_hashes"])

    def test_foreign_input_manifest_is_rejected(self):
        job = self.make_job()
        result_dir = self.make_result_bundle(job)
        data = json.loads((result_dir / "input_manifest.json").read_text())
        data["world_key"] = "WORLD__FOREIGN"
        write_json(result_dir / "input_manifest.json", data)
        v = ResultBundleVerifier(self.queue).verify(result_dir)
        self.assertFalse(v.ok)

    def test_collector_does_not_acknowledge_rejected_result(self):
        job = self.make_job()
        result_dir = self.make_result_bundle(job)
        (result_dir / "result.json").write_text("{}", encoding="utf-8")
        transport = FakeTransport(result_dir)
        out = collect_remote_results(self.queue, transport, self.root / "collected")
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["rejected_count"], 1)
        self.assertEqual(transport.acked, [])
        row = self.queue.conn.execute("SELECT status FROM compute_jobs WHERE job_id=?", (job.job_id,)).fetchone()
        self.assertNotEqual(row["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
