
import unittest

from root_control.authority.envelopes import (
    AuthorityEnvelopeRegistry,
    SchemaViolation,
    ConstitutionalViolation,
    EnvelopeExpired,
    EnvelopeRevoked,
    UsageLimitExceeded,
    ScopeEscalation,
    ResourceLimitExceeded,
)


def registry():
    return AuthorityEnvelopeRegistry(
        allowed_subjects={
            "DIRECTOR_INTERNAL",
            "AGENT_01",
            "AGENT_02",
            "AGENT_03",
            "AGENT_04",
            "AGENT_05",
            "AGENT_06",
        },
        allowed_operations={
            "READ_STATUS",
            "PREPARE_PROPOSAL",
            "READ_ARTIFACT",
        },
        protected_assets={
            "DIRECTOR_CORE",
            "ROOT_CONTROL",
            "COMMAND_REGISTRY",
        },
        max_risk_by_subject={
            "DIRECTOR_INTERNAL": "MEDIUM",
            "AGENT_01": "LOW",
            "AGENT_02": "LOW",
            "AGENT_03": "LOW",
            "AGENT_04": "LOW",
            "AGENT_05": "LOW",
            "AGENT_06": "LOW",
        },
    )


def valid():
    return {
        "envelope_id": "env-001",
        "subject": "AGENT_01",
        "operation": "READ_ARTIFACT",
        "resource": "WORLD_ARTIFACT_001",
        "purpose": "Read bounded world evidence",
        "environment": "CONTROL_PLANE",
        "context": {
            "world_id": "world-001",
            "session_id": "session-001",
        },
        "risk_limit": "LOW",
        "time_limit_seconds": 300,
        "usage_limit": 2,
        "resource_limits": {
            "bytes": 4096,
            "items": 10,
        },
        "required_tests": [
            "identity_valid",
            "scope_valid",
        ],
        "prohibitions": [
            "NO_RUNTIME_EXECUTION",
            "NO_SCOPE_ESCALATION",
        ],
        "expires_at": "2026-08-16T15:00:00+02:00",
        "issuer": "PROGRAMMER_ROOT",
        "issued_at": "2026-08-16T14:00:00+02:00",
    }


class AuthorityEnvelopeTests(unittest.TestCase):

    def test_valid_envelope(self):
        r = registry()
        e = r.create(valid())

        self.assertEqual(e.subject, "AGENT_01")
        self.assertEqual(e.operation, "READ_ARTIFACT")
        self.assertTrue(e.envelope_hash)

    def test_missing_schema_field_rejected(self):
        r = registry()
        d = valid()
        del d["usage_limit"]

        with self.assertRaises(SchemaViolation):
            r.create(d)

    def test_unknown_subject_rejected(self):
        r = registry()
        d = valid()
        d["subject"] = "UNBOUNDED_AGENT"

        with self.assertRaises(ConstitutionalViolation):
            r.create(d)

    def test_operation_escalation_rejected(self):
        r = registry()
        d = valid()
        d["operation"] = "EXECUTE_SHELL"

        with self.assertRaises(ConstitutionalViolation):
            r.create(d)

    def test_risk_escalation_rejected(self):
        r = registry()
        d = valid()
        d["risk_limit"] = "HIGH"

        with self.assertRaises(ConstitutionalViolation):
            r.create(d)

    def test_stage03_production_runtime_grant_rejected(self):
        r = registry()
        d = valid()
        d["subject"] = "DIRECTOR_INTERNAL"
        d["operation"] = "READ_STATUS"
        d["resource"] = "DIRECTOR_CORE"
        d["environment"] = "PRODUCTION_RUNTIME"

        with self.assertRaises(ConstitutionalViolation):
            r.create(d)

    def test_envelope_is_immutable(self):
        r = registry()
        r.create(valid())

        d = valid()
        d["purpose"] = "changed"

        with self.assertRaises(ConstitutionalViolation):
            r.create(d)

    def test_wrong_subject_use_rejected(self):
        r = registry()
        r.create(valid())

        with self.assertRaises(ScopeEscalation):
            r.authorize_use(
                "env-001",
                subject="AGENT_02",
                operation="READ_ARTIFACT",
                resource="WORLD_ARTIFACT_001",
                risk="LOW",
                resource_request={"bytes": 10, "items": 1},
                now_value="2026-08-16T14:10:00+02:00",
                usage_id="use-001",
            )

    def test_wrong_operation_use_rejected(self):
        r = registry()
        r.create(valid())

        with self.assertRaises(ScopeEscalation):
            r.authorize_use(
                "env-001",
                subject="AGENT_01",
                operation="READ_STATUS",
                resource="WORLD_ARTIFACT_001",
                risk="LOW",
                resource_request={"bytes": 10, "items": 1},
                now_value="2026-08-16T14:10:00+02:00",
                usage_id="use-001",
            )

    def test_resource_limit_enforced(self):
        r = registry()
        r.create(valid())

        with self.assertRaises(ResourceLimitExceeded):
            r.authorize_use(
                "env-001",
                subject="AGENT_01",
                operation="READ_ARTIFACT",
                resource="WORLD_ARTIFACT_001",
                risk="LOW",
                resource_request={"bytes": 999999, "items": 1},
                now_value="2026-08-16T14:10:00+02:00",
                usage_id="use-001",
            )

    def test_usage_limit_enforced(self):
        r = registry()
        r.create(valid())

        for i in range(2):
            r.authorize_use(
                "env-001",
                subject="AGENT_01",
                operation="READ_ARTIFACT",
                resource="WORLD_ARTIFACT_001",
                risk="LOW",
                resource_request={"bytes": 10, "items": 1},
                now_value="2026-08-16T14:10:00+02:00",
                usage_id=f"use-{i}",
            )

        with self.assertRaises(UsageLimitExceeded):
            r.authorize_use(
                "env-001",
                subject="AGENT_01",
                operation="READ_ARTIFACT",
                resource="WORLD_ARTIFACT_001",
                risk="LOW",
                resource_request={"bytes": 10, "items": 1},
                now_value="2026-08-16T14:11:00+02:00",
                usage_id="use-3",
            )

    def test_duplicate_usage_is_idempotent(self):
        r = registry()
        r.create(valid())

        args = dict(
            subject="AGENT_01",
            operation="READ_ARTIFACT",
            resource="WORLD_ARTIFACT_001",
            risk="LOW",
            resource_request={"bytes": 10, "items": 1},
            now_value="2026-08-16T14:10:00+02:00",
            usage_id="same-use",
        )

        a, d1 = r.authorize_use("env-001", **args)
        b, d2 = r.authorize_use("env-001", **args)

        self.assertFalse(d1)
        self.assertTrue(d2)
        self.assertEqual(a, b)

    def test_expiry_enforced(self):
        r = registry()
        r.create(valid())

        with self.assertRaises(EnvelopeExpired):
            r.authorize_use(
                "env-001",
                subject="AGENT_01",
                operation="READ_ARTIFACT",
                resource="WORLD_ARTIFACT_001",
                risk="LOW",
                resource_request={"bytes": 10, "items": 1},
                now_value="2026-08-16T15:01:00+02:00",
                usage_id="expired",
            )

    def test_immediate_revocation(self):
        r = registry()
        r.create(valid())

        r.revoke(
            "env-001",
            actor="PROGRAMMER_ROOT",
            reason="operator_revocation",
            revoked_at="2026-08-16T14:05:00+02:00",
        )

        view = r.view(
            "env-001",
            now_value="2026-08-16T14:06:00+02:00",
        )

        self.assertEqual(view["status"], "REVOKED")

        with self.assertRaises(EnvelopeRevoked):
            r.authorize_use(
                "env-001",
                subject="AGENT_01",
                operation="READ_ARTIFACT",
                resource="WORLD_ARTIFACT_001",
                risk="LOW",
                resource_request={"bytes": 10, "items": 1},
                now_value="2026-08-16T14:06:00+02:00",
                usage_id="after-revoke",
            )

    def test_usage_is_evidence_not_execution(self):
        r = registry()
        r.create(valid())

        use, _ = r.authorize_use(
            "env-001",
            subject="AGENT_01",
            operation="READ_ARTIFACT",
            resource="WORLD_ARTIFACT_001",
            risk="LOW",
            resource_request={"bytes": 10, "items": 1},
            now_value="2026-08-16T14:10:00+02:00",
            usage_id="use-evidence",
        )

        self.assertFalse(use["executed"])


if __name__ == "__main__":
    unittest.main()
