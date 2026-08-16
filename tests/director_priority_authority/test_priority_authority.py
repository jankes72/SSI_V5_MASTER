
import unittest

from ssi_v5.director.priority_authority import DirectorPriorityAuthority


class DirectorPriorityAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.a = DirectorPriorityAuthority(director_authority_id="DIRECTOR__TEST")

    def test_director_is_active_runtime_authority(self):
        self.assertEqual("DIRECTOR", self.a.status()["active_runtime_authority"])
        self.assertTrue(self.a.status()["director_priority_authority"])

    def test_caller_priority_is_not_authoritative(self):
        self.assertFalse(self.a.status()["caller_priority_is_authoritative"])

    def test_director_can_resolve_final_priority(self):
        r = self.a.resolve(
            requested_priority="P9_MAINTENANCE",
            director_priority="P3_FEATURE",
            caller_source="AGENT",
        )
        self.assertEqual("DIRECTOR", r.authority_type)
        self.assertEqual("P3_FEATURE", r.final_priority)

    def test_resolution_does_not_execute(self):
        r = self.a.resolve(
            requested_priority="P9_MAINTENANCE",
            director_priority="P6_TRAIN",
        )
        self.assertFalse(r.execution_started)

    def test_root_override_beats_director(self):
        r = self.a.resolve(
            requested_priority="P9_MAINTENANCE",
            director_priority="P6_TRAIN",
            root_override_priority="P0_CRITICAL",
            root_authority_id="ROOT__1",
        )
        self.assertEqual("ROOT", r.authority_type)
        self.assertEqual("P0_CRITICAL", r.final_priority)
        self.assertTrue(r.root_override_applied)

    def test_root_override_requires_authority_id(self):
        with self.assertRaises(ValueError):
            self.a.resolve(
                requested_priority="P9_MAINTENANCE",
                director_priority="P6_TRAIN",
                root_override_priority="P0_CRITICAL",
            )

    def test_unknown_priority_rejected(self):
        with self.assertRaises(ValueError):
            self.a.resolve(
                requested_priority="P99",
                director_priority="P6_TRAIN",
            )

    def test_director_priority_required_without_root(self):
        with self.assertRaises(ValueError):
            self.a.resolve(
                requested_priority="P9_MAINTENANCE",
            )

    def test_resolution_verifies(self):
        r = self.a.resolve(
            requested_priority="P9_MAINTENANCE",
            director_priority="P1_LIVE",
        )
        self.assertEqual("VALID", self.a.verify_resolution(r)["status"])

    def test_no_direct_compute_dispatch(self):
        s = self.a.status()
        self.assertFalse(s["priority_resolution_is_execution"])
        self.assertFalse(s["direct_compute_dispatch"])
        self.assertFalse(s["self_escalation"])


if __name__ == "__main__":
    unittest.main()
