
import tempfile
import unittest
from pathlib import Path

from ssi_v5.scaling.routing import WorkerRoutingRegistry


class WorkerRoutingRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = WorkerRoutingRegistry(Path(self.tmp.name) / "routing.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_worker(self):
        w = self.r.register_worker(
            worker_id="NODE01",
            endpoint="ssh://node01",
            capabilities=["MODEL_TRAIN_AND_OBSERVE"],
            max_concurrency=1,
        )
        self.assertEqual("READY", w.state)

    def test_duplicate_worker_rejected(self):
        kw = dict(
            worker_id="NODE01",
            endpoint="ssh://node01",
            capabilities=["HEALTHCHECK"],
            max_concurrency=1,
        )
        self.r.register_worker(**kw)
        with self.assertRaises(ValueError):
            self.r.register_worker(**kw)

    def test_route_deterministically(self):
        self.r.register_worker(
            worker_id="NODE02",
            endpoint="ssh://node02",
            capabilities=["HEALTHCHECK"],
            max_concurrency=1,
        )
        self.r.register_worker(
            worker_id="NODE01",
            endpoint="ssh://node01",
            capabilities=["HEALTHCHECK"],
            max_concurrency=1,
        )
        d = self.r.route(
            job_id="JOB__1",
            required_capability="HEALTHCHECK",
            final_priority=5,
            policy_ref="POLICY__1",
        )
        self.assertEqual("NODE01", d.selected_worker_id)

    def test_no_eligible_worker_rejected(self):
        with self.assertRaises(RuntimeError):
            self.r.route(
                job_id="JOB__1",
                required_capability="MODEL_TRAIN_AND_OBSERVE",
                final_priority=5,
                policy_ref="POLICY__1",
            )

    def test_priority_range_enforced(self):
        self.r.register_worker(
            worker_id="NODE01",
            endpoint="ssh://node01",
            capabilities=["HEALTHCHECK"],
            max_concurrency=1,
        )
        with self.assertRaises(ValueError):
            self.r.route(
                job_id="JOB__1",
                required_capability="HEALTHCHECK",
                final_priority=10,
                policy_ref="POLICY__1",
            )

    def test_worker_is_compute_only(self):
        self.assertTrue(self.r.contract()["worker_is_compute_only"])

    def test_worker_cannot_resolve_priority_or_lifecycle(self):
        s = self.r.contract()
        self.assertFalse(s["worker_resolves_final_priority"])
        self.assertFalse(s["worker_mutates_lifecycle"])

    def test_worker_cannot_self_route(self):
        self.assertFalse(self.r.contract()["worker_self_routes"])

    def test_routing_requires_governed_inputs(self):
        s = self.r.contract()
        self.assertTrue(s["routing_requires_policy_ref"])
        self.assertTrue(s["routing_uses_final_priority"])

    def test_multiworker_and_failure_boundary(self):
        s = self.r.contract()
        self.assertTrue(s["multiple_workers_supported"])
        self.assertTrue(s["routing_is_deterministic"])
        self.assertTrue(s["node_failure_does_not_grant_authority"])


if __name__ == "__main__":
    unittest.main()
