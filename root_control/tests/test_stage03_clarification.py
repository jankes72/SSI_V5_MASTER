
import unittest

from root_control.commands.clarification import (
    ClarificationService,
    ClarificationAlreadyResolved,
    ClarificationUnauthorized,
    ClarificationVersionConflict,
    ClarificationExpired,
    ClarificationError,
)


CTX = {
    "root_identity_id": "root-001",
    "session_id": "session-001",
    "device_id": "device-001",
}


def service():
    return ClarificationService(
        authorize=lambda ctx:
            ctx.get("root_identity_id") == "root-001"
            and ctx.get("session_id") == "session-001"
            and ctx.get("device_id") == "device-001"
    )


def create(svc, expires=None):
    return svc.create_request(
        clarification_id="clar-001",
        command_id="cmd-001",
        command_version=7,
        reason_code="MATERIAL_AMBIGUITY",
        question="Which project scope is authoritative?",
        requested_fields=["scope"],
        created_at="2026-08-16T12:00:00+02:00",
        expires_at=expires,
    )


class ClarificationTests(unittest.TestCase):

    def test_nominal_clarification(self):
        svc = service()
        create(svc)

        r = svc.respond(
            clarification_id="clar-001",
            base_command_version=7,
            answers={"scope": "SSI_V5_MASTER"},
            identity_context=CTX,
            now_value="2026-08-16T12:01:00+02:00",
        )

        self.assertEqual(
            r["status"],
            "CLARIFICATION_RECORDED"
        )
        self.assertFalse(r["command_executed"])
        self.assertFalse(r["authority_granted"])

    def test_duplicate_is_idempotent(self):
        svc = service()
        create(svc)

        kwargs = dict(
            clarification_id="clar-001",
            base_command_version=7,
            answers={"scope": "SSI_V5_MASTER"},
            identity_context=CTX,
            now_value="2026-08-16T12:01:00+02:00",
        )

        a = svc.respond(**kwargs)
        b = svc.respond(**kwargs)

        self.assertEqual(
            a["response"].response_hash,
            b["response"].response_hash
        )
        self.assertEqual(
            b["status"],
            "DUPLICATE_ACCEPTED"
        )

    def test_conflicting_second_response_rejected(self):
        svc = service()
        create(svc)

        svc.respond(
            clarification_id="clar-001",
            base_command_version=7,
            answers={"scope": "A"},
            identity_context=CTX,
            now_value="2026-08-16T12:01:00+02:00",
        )

        with self.assertRaises(
            ClarificationAlreadyResolved
        ):
            svc.respond(
                clarification_id="clar-001",
                base_command_version=7,
                answers={"scope": "B"},
                identity_context=CTX,
                now_value="2026-08-16T12:02:00+02:00",
            )

    def test_stale_version_rejected(self):
        svc = service()
        create(svc)

        with self.assertRaises(
            ClarificationVersionConflict
        ):
            svc.respond(
                clarification_id="clar-001",
                base_command_version=6,
                answers={"scope": "SSI_V5_MASTER"},
                identity_context=CTX,
                now_value="2026-08-16T12:01:00+02:00",
            )

    def test_wrong_identity_rejected(self):
        svc = service()
        create(svc)

        bad = dict(CTX)
        bad["root_identity_id"] = "attacker"

        with self.assertRaises(
            ClarificationUnauthorized
        ):
            svc.respond(
                clarification_id="clar-001",
                base_command_version=7,
                answers={"scope": "SSI_V5_MASTER"},
                identity_context=bad,
                now_value="2026-08-16T12:01:00+02:00",
            )

    def test_expired_rejected(self):
        svc = service()

        create(
            svc,
            expires="2026-08-16T12:05:00+02:00"
        )

        with self.assertRaises(
            ClarificationExpired
        ):
            svc.respond(
                clarification_id="clar-001",
                base_command_version=7,
                answers={"scope": "SSI_V5_MASTER"},
                identity_context=CTX,
                now_value="2026-08-16T12:06:00+02:00",
            )

    def test_missing_answer_rejected(self):
        svc = service()
        create(svc)

        with self.assertRaises(ClarificationError):
            svc.respond(
                clarification_id="clar-001",
                base_command_version=7,
                answers={},
                identity_context=CTX,
                now_value="2026-08-16T12:01:00+02:00",
            )

    def test_original_is_not_overwritten(self):
        svc = service()
        create(svc)

        svc.respond(
            clarification_id="clar-001",
            base_command_version=7,
            answers={"scope": "NEW_SCOPE"},
            identity_context=CTX,
            now_value="2026-08-16T12:01:00+02:00",
        )

        original = {
            "command_id": "cmd-001",
            "version": 7,
            "scope": "AMBIGUOUS",
            "original_content": "original evidence",
        }

        derived = svc.derive_command_revision(
            immutable_original=original,
            clarification_id="clar-001",
        )

        self.assertEqual(
            original["scope"],
            "AMBIGUOUS"
        )
        self.assertEqual(
            original["original_content"],
            "original evidence"
        )
        self.assertEqual(
            derived["clarified_fields"]["scope"],
            "NEW_SCOPE"
        )


if __name__ == "__main__":
    unittest.main()
