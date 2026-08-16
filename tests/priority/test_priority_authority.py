import unittest

from ssi_v5.compute.fabric import ResourceRequest
from ssi_v5.compute.job_envelope import JobEnvelope
from ssi_v5.compute.priority_authority import (
    RootPriorityAuthority,
    WorldPolicyPriorityAuthority,
    priority_authority_status,
)


def env(**overrides):
    data = dict(
        source="WORLD",
        source_id="WORLD__SPORT",
        job_type="MODEL_TRAIN_AND_OBSERVE",
        world_id="WORLD__SPORT",
        domain_id="DOMAIN__TENNIS",
        discipline="TENNIS",
        owner_type="WORLD",
        owner_id="WORLD__SPORT",
        route_key="SPORT.TENNIS",
        payload={"x": 1},
        resources=ResourceRequest(cpu_cores=1, ram_mb=512, gpu_mode="CPU"),
        requested_priority_class="P0_SYSTEM_CRITICAL",
    )
    data.update(overrides)
    return JobEnvelope.create(**data)


class PriorityAuthorityTest(unittest.TestCase):
    def test_world_policy_blocks_self_escalation(self):
        resolved = WorldPolicyPriorityAuthority().resolve(env())
        self.assertEqual(resolved.requested_priority_class, "P0_SYSTEM_CRITICAL")
        self.assertEqual(resolved.final_priority_class, "P6_CHALLENGER_TRAINING")
        self.assertEqual(resolved.priority_resolution.authority_type, "WORLD_POLICY")

    def test_live_prediction_maps_to_p1(self):
        resolved = WorldPolicyPriorityAuthority().resolve(env(job_type="LIVE_PREDICTION"))
        self.assertEqual(resolved.final_priority_class, "P1_LIVE_PREDICTION")

    def test_unknown_job_type_uses_source_fallback(self):
        resolved = WorldPolicyPriorityAuthority().resolve(env(job_type="NEW_WORLD_JOB"))
        self.assertEqual(resolved.final_priority_class, "P6_CHALLENGER_TRAINING")

    def test_continuum_fallback_is_discovery(self):
        resolved = WorldPolicyPriorityAuthority().resolve(env(source="CONTINUUM", source_id="CONTINUUM", job_type="NEW_EXPERIMENT"))
        self.assertEqual(resolved.final_priority_class, "P8_DISCOVERY_EXPERIMENT")

    def test_root_override_can_assign_p0_explicitly(self):
        resolved = RootPriorityAuthority().resolve(env(requested_priority_class=None), final_priority_class="P0_SYSTEM_CRITICAL", reason="root emergency test")
        self.assertEqual(resolved.final_priority_class, "P0_SYSTEM_CRITICAL")
        self.assertEqual(resolved.priority_resolution.authority_type, "ROOT")

    def test_unknown_world_is_rejected(self):
        with self.assertRaises(ValueError):
            WorldPolicyPriorityAuthority().resolve(env(world_id="WORLD__UNKNOWN"))

    def test_double_resolution_is_rejected(self):
        first = WorldPolicyPriorityAuthority().resolve(env())
        with self.assertRaises(ValueError):
            WorldPolicyPriorityAuthority().resolve(first)

    def test_resolved_envelope_can_become_compute_job(self):
        resolved = WorldPolicyPriorityAuthority().resolve(env())
        job = resolved.to_compute_job()
        self.assertEqual(job.priority_class, "P6_CHALLENGER_TRAINING")
        self.assertEqual(job.payload["job_envelope"]["priority_resolution"]["authority_type"], "WORLD_POLICY")

    def test_status_declares_director_not_active(self):
        status = priority_authority_status()
        self.assertEqual(status["active_runtime_authority"], "WORLD_POLICY")
        self.assertEqual(status["director_authority"], "RESERVED_NOT_ACTIVE")
        self.assertFalse(status["self_escalation_allowed"])


if __name__ == "__main__":
    unittest.main()
