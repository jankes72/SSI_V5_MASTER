import unittest

from ssi_v5.compute.fabric import ResourceRequest
from ssi_v5.compute.job_envelope import JOB_SOURCES, JobEnvelope, job_envelope_status


def envelope(**overrides):
    values = dict(
        source="WORLD",
        source_id="WORLD__SPORT",
        job_type="MODEL_TRAIN_AND_OBSERVE",
        world_id="WORLD__SPORT",
        domain_id="DOMAIN__TENNIS",
        discipline="SPORTS_TENNIS",
        owner_type="WORLD",
        owner_id="WORLD__SPORT",
        route_key="TENNIS_MODEL",
        payload={"purpose": "test"},
        resources=ResourceRequest(cpu_cores=1, ram_mb=512, gpu_mode="CPU"),
        requested_priority_class="P0_SYSTEM_CRITICAL",
        governance_refs=["ROOT_CONSTITUTION__V1"],
        evidence_refs=["EVIDENCE__1"],
    )
    values.update(overrides)
    return JobEnvelope.create(**values)


class JobEnvelopeTest(unittest.TestCase):
    def test_all_sources_are_declared(self):
        self.assertEqual(JOB_SOURCES, {"SYSTEM", "WORLD", "DIRECTOR", "AGENT", "CONTINUUM"})

    def test_requested_priority_is_not_authoritative(self):
        env = envelope(requested_priority_class="P0_SYSTEM_CRITICAL")
        self.assertFalse(env.is_priority_resolved)
        with self.assertRaises(ValueError):
            env.to_compute_job()

    def test_authority_may_resolve_different_priority(self):
        env = envelope(requested_priority_class="P0_SYSTEM_CRITICAL")
        resolved = env.resolve_priority(
            authority_type="WORLD_POLICY",
            authority_id="WORLD_POLICY__SPORT__V1",
            final_priority_class="P6_CHALLENGER_TRAINING",
            reason="World policy deterministic baseline",
        )
        job = resolved.to_compute_job()
        self.assertEqual(job.priority_class, "P6_CHALLENGER_TRAINING")
        self.assertEqual(job.payload["job_envelope"]["requested_priority_class"], "P0_SYSTEM_CRITICAL")

    def test_agent_cannot_be_priority_authority(self):
        env = envelope(source="AGENT", source_id="AGENT__01")
        with self.assertRaises(ValueError):
            env.resolve_priority(
                authority_type="AGENT",
                authority_id="AGENT__01",
                final_priority_class="P0_SYSTEM_CRITICAL",
                reason="self escalation",
            )

    def test_hash_changes_when_evidence_changes(self):
        a = envelope(evidence_refs=["EVIDENCE__1"])
        b = envelope(evidence_refs=["EVIDENCE__2"])
        self.assertNotEqual(a.content_hash, b.content_hash)

    def test_status_declares_no_execution_authority(self):
        status = job_envelope_status()
        self.assertFalse(status["caller_priority_is_authoritative"])
        self.assertFalse(status["director_output_is_execution"])
        self.assertFalse(status["node_worker_has_authority"])
        self.assertTrue(status["queue_submission_requires_priority_resolution"])


if __name__ == "__main__":
    unittest.main()
