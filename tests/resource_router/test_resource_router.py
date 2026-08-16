import unittest

from ssi_v5.compute.fabric import ComputeJob, ResourceRequest
from ssi_v5.compute.resource_router import Node01ResourceRouter, resource_router_status


def job(*, job_type="HEALTHCHECK", priority="P9_MAINTENANCE", resources=None):
    return ComputeJob.create(
        job_type=job_type,
        priority_class=priority,
        world_key="WORLD__SYSTEM",
        domain_key="DOMAIN__TEST",
        discipline="SYSTEM",
        owner_type="SYSTEM",
        owner_id="TEST",
        route_key="TEST",
        network_id=None,
        generation=None,
        payload={},
        resources=resources or ResourceRequest(cpu_cores=1, ram_mb=128, gpu_mode="NONE"),
    )


class ResourceRouterTest(unittest.TestCase):
    def setUp(self):
        self.router = Node01ResourceRouter()

    def test_healthcheck_routes_cpu(self):
        d = self.router.route(job())
        self.assertEqual(d.status, "ROUTABLE")
        self.assertEqual(d.execution_mode, "CPU")
        self.assertEqual(d.worker_id, "node-01")

    def test_cpu_capacity_is_protected(self):
        d = self.router.route(job(resources=ResourceRequest(cpu_cores=4, ram_mb=128, gpu_mode="CPU")))
        self.assertEqual(d.status, "WAITING_RESOURCES")

    def test_soft_ram_limit_is_protected(self):
        d = self.router.route(job(resources=ResourceRequest(cpu_cores=1, ram_mb=12000, gpu_mode="CPU")))
        self.assertEqual(d.status, "WAITING_RESOURCES")

    def test_current_model_training_routes_cpu_even_auto(self):
        d = self.router.route(job(job_type="MODEL_TRAIN_AND_OBSERVE", resources=ResourceRequest(cpu_cores=2, ram_mb=2048, gpu_mode="AUTO")))
        self.assertEqual(d.status, "ROUTABLE")
        self.assertEqual(d.execution_mode, "CPU")

    def test_long_job_requires_checkpoint_contract(self):
        d = self.router.route(job(resources=ResourceRequest(cpu_cores=1, ram_mb=128, gpu_mode="CPU", expected_seconds=600)))
        self.assertTrue(d.checkpoint_required)
        self.assertEqual(d.preemption_mode, "COOPERATIVE_CHECKPOINT_ONLY")

    def test_higher_priority_does_not_allow_destructive_preemption(self):
        running = job(priority="P6_CHALLENGER_TRAINING", resources=ResourceRequest(cpu_cores=1, ram_mb=128, gpu_mode="CPU", expected_seconds=600))
        incoming = job(priority="P1_LIVE_PREDICTION")
        d = self.router.preemption_decision(running, incoming)
        self.assertEqual(d["action"], "REQUEST_COOPERATIVE_CHECKPOINT")
        self.assertFalse(d["destructive_preemption_allowed"])

    def test_short_job_is_not_killed_for_preemption(self):
        running = job(priority="P6_CHALLENGER_TRAINING", resources=ResourceRequest(cpu_cores=1, ram_mb=128, gpu_mode="CPU", expected_seconds=60))
        incoming = job(priority="P1_LIVE_PREDICTION")
        d = self.router.preemption_decision(running, incoming)
        self.assertEqual(d["action"], "DEFER_PREEMPTION")
        self.assertFalse(d["destructive_preemption_allowed"])

    def test_status_declares_no_worker_authority(self):
        s = resource_router_status()
        self.assertFalse(s["worker_has_authority"])
        self.assertFalse(s["destructive_preemption_allowed"])


if __name__ == '__main__':
    unittest.main()
