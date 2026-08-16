import unittest

from ssi_v5.compute.fabric import ComputeBackend, ComputeJob
from ssi_v5.compute.remote_gateway import (
    UnifiedRemoteJobGateway,
    node01_healthcheck_envelope,
    remote_gateway_status,
)


class CaptureBackend(ComputeBackend):
    def __init__(self):
        self.jobs = []

    def submit(self, job: ComputeJob) -> str:
        self.jobs.append(job)
        return job.job_id


class UnifiedRemoteJobGatewayTest(unittest.TestCase):
    def test_world_policy_resolves_before_backend_submission(self):
        backend = CaptureBackend()
        gateway = UnifiedRemoteJobGateway(backend)
        envelope = node01_healthcheck_envelope()

        receipt = gateway.submit(envelope)

        self.assertEqual(len(backend.jobs), 1)
        job = backend.jobs[0]
        self.assertEqual(job.priority_class, "P9_MAINTENANCE")
        self.assertEqual(receipt.final_priority_class, "P9_MAINTENANCE")
        self.assertEqual(receipt.authority_type, "WORLD_POLICY")
        self.assertNotEqual(job.priority_class, envelope.requested_priority_class)

    def test_requested_p0_cannot_self_escalate(self):
        backend = CaptureBackend()
        receipt = UnifiedRemoteJobGateway(backend).submit(node01_healthcheck_envelope())
        self.assertEqual(receipt.final_priority_class, "P9_MAINTENANCE")

    def test_pre_resolved_envelope_is_rejected_on_normal_path(self):
        backend = CaptureBackend()
        env = node01_healthcheck_envelope().resolve_priority(
            authority_type="ROOT",
            authority_id="ROOT_CONSTITUTION__V1",
            final_priority_class="P0_SYSTEM_CRITICAL",
            reason="forged outside gateway",
        )
        with self.assertRaises(ValueError):
            UnifiedRemoteJobGateway(backend).submit(env)
        self.assertEqual(backend.jobs, [])

    def test_root_override_is_explicit_separate_path(self):
        backend = CaptureBackend()
        gateway = UnifiedRemoteJobGateway(backend)
        receipt = gateway.submit_root_override(
            node01_healthcheck_envelope(),
            final_priority_class="P0_SYSTEM_CRITICAL",
            reason="explicit root emergency test",
        )
        self.assertEqual(receipt.authority_type, "ROOT")
        self.assertEqual(receipt.final_priority_class, "P0_SYSTEM_CRITICAL")
        self.assertEqual(backend.jobs[0].priority_class, "P0_SYSTEM_CRITICAL")

    def test_envelope_lineage_is_embedded_in_compute_job(self):
        backend = CaptureBackend()
        env = node01_healthcheck_envelope()
        UnifiedRemoteJobGateway(backend).submit(env)
        lineage = backend.jobs[0].payload["job_envelope"]
        self.assertEqual(lineage["envelope_id"], env.envelope_id)
        self.assertEqual(lineage["source"], "SYSTEM")
        self.assertEqual(lineage["requested_priority_class"], "P0_SYSTEM_CRITICAL")
        self.assertEqual(lineage["priority_resolution"]["authority_type"], "WORLD_POLICY")
        self.assertTrue(lineage["content_hash"])

    def test_status_declares_worker_has_no_authority(self):
        status = remote_gateway_status()
        self.assertFalse(status["node_worker_has_authority"])
        self.assertFalse(status["node_worker_can_promote"])
        self.assertFalse(status["direct_compute_job_input_allowed"])
        self.assertFalse(status["caller_pre_resolved_submission_allowed"])


if __name__ == "__main__":
    unittest.main()
