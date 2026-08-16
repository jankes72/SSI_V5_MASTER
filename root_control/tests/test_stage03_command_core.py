
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from root_control.commands.intake import (
    CommandIntakeService,
    AuthenticationError,
    ValidationError,
    canonical_hash,
)
from root_control.commands.registry import (
    CommandRegistry,
    IdempotencyConflict,
    ConcurrentUpdate,
)
from root_control.commands.validator import (
    RootCommandValidator,
)
from root_control.commands.state_machine import (
    CommandStateMachine,
    InvalidTransition,
)
from root_control.commands.ordering import (
    RootCommandOrderingService,
)

class Stage03CommandCoreTests(unittest.TestCase):

    def make_request(self):
        return {
            "idempotency_key": "idem-001",
            "root_identity_id": "root-1",
            "session_id": "session-1",
            "device_id": "device-1",
            "source_channel": "LOCAL",
            "original_content": "Analyze system state",
            "payload_hash": canonical_hash({
                "original_content": "Analyze system state"
            }),
            "target_scope": "DIRECTOR",
            "target_agent_id": None,
            "target_world_id": None,
            "target_domain_id": None,
        }

    def test_317_durable_intake_retry_same_ids(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry(Path(td) / "registry.sqlite3")

            svc = CommandIntakeService(
                reg,
                session_validator=lambda r: True,
                authorizer=lambda r: True,
            )

            req = self.make_request()

            first = svc.submit(req)
            second = svc.submit(req)

            self.assertEqual(first.command_id, second.command_id)
            self.assertEqual(first.receipt_id, second.receipt_id)

            self.assertFalse(first.duplicate)
            self.assertTrue(second.duplicate)

            self.assertEqual(len(reg.all_commands()), 1)

            reg.close()

    def test_317_invalid_session_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry(Path(td) / "registry.sqlite3")

            svc = CommandIntakeService(
                reg,
                session_validator=lambda r: False,
                authorizer=lambda r: True,
            )

            with self.assertRaises(AuthenticationError):
                svc.submit(self.make_request())

            self.assertEqual(len(reg.all_commands()), 0)
            reg.close()

    def test_317_altered_original_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry(Path(td) / "registry.sqlite3")

            svc = CommandIntakeService(
                reg,
                session_validator=lambda r: True,
                authorizer=lambda r: True,
            )

            req = self.make_request()
            req["original_content"] = "ALTERED"

            with self.assertRaises(ValidationError):
                svc.submit(req)

            reg.close()

    def validator_command(self):
        return {
            "command_id": "cmd-test",
            "root_identity_id": "root-1",
            "session_id": "session-1",
            "original_content": "Analyze world",
            "command_type": "ANALYSIS_REQUEST",
            "priority": "P3",
            "risk_class": "LOW",
            "required_approval": False,
            "ambiguity": "NONE",
        }

    def test_318_valid(self):
        v = RootCommandValidator()

        result = v.validate(
            self.validator_command(),
            expected_root_identity_id="root-1",
            expected_session_id="session-1",
        )

        self.assertEqual(result.status, "VALIDATED")

    def test_318_wrong_identity(self):
        v = RootCommandValidator()

        result = v.validate(
            self.validator_command(),
            expected_root_identity_id="other",
            expected_session_id="session-1",
        )

        self.assertEqual(
            result.reason_code,
            "IDENTITY_BINDING_FAILED",
        )

    def test_318_material_ambiguity(self):
        c = self.validator_command()
        c["ambiguity"] = "MATERIAL"

        v = RootCommandValidator()

        result = v.validate(
            c,
            expected_root_identity_id="root-1",
            expected_session_id="session-1",
        )

        self.assertEqual(
            result.status,
            "CLARIFICATION_REQUIRED",
        )

    def test_319_restart_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "registry.sqlite3"

            reg = CommandRegistry(path)

            svc = CommandIntakeService(
                reg,
                session_validator=lambda r: True,
                authorizer=lambda r: True,
            )

            receipt = svc.submit(self.make_request())
            reg.close()

            reg2 = CommandRegistry(path)

            row = reg2.get(receipt.command_id)

            self.assertIsNotNone(row)
            self.assertEqual(
                row["original_content"],
                "Analyze system state",
            )
            self.assertTrue(reg2.integrity_check())

            reg2.close()

    def test_319_concurrent_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "registry.sqlite3"
            reg = CommandRegistry(path)

            svc = CommandIntakeService(
                reg,
                session_validator=lambda r: True,
                authorizer=lambda r: True,
            )

            req = self.make_request()

            def call(_):
                return svc.submit(req).command_id

            with ThreadPoolExecutor(max_workers=5) as ex:
                ids = list(ex.map(call, range(10)))

            self.assertEqual(len(set(ids)), 1)
            self.assertEqual(len(reg.all_commands()), 1)

            reg.close()

    def test_319_corruption_detection(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.sqlite3"
            bad.write_bytes(b"this is not sqlite")

            self.assertFalse(
                CommandRegistry.file_integrity_check(bad)
            )

    def test_320_state_machine_and_stale_version(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry(Path(td) / "r.sqlite3")

            svc = CommandIntakeService(
                reg,
                session_validator=lambda r: True,
                authorizer=lambda r: True,
            )

            receipt = svc.submit(self.make_request())

            sm = CommandStateMachine(reg)

            row, evidence = sm.transition(
                receipt.command_id,
                expected_version=1,
                next_status="AUTHENTICATING",
                actor="ROOT_CONTROL_SERVER",
                condition="authentication_started",
                reason="ROOT_COMMAND_RECEIVED",
                correlation_id="corr-001",
            )

            self.assertEqual(row["version"], 2)
            self.assertEqual(row["status"], "AUTHENTICATING")

            with self.assertRaises(InvalidTransition):
                sm.transition(
                    receipt.command_id,
                    expected_version=2,
                    next_status="COMPLETED",
                    actor="TEST",
                    condition="invalid",
                    reason="INVALID_JUMP",
                    correlation_id="corr-002",
                )

            with self.assertRaises(ConcurrentUpdate):
                reg.transition(
                    receipt.command_id,
                    expected_version=1,
                    new_status="VALIDATING",
                    updated_at="2026-08-16T12:00:00+00:00",
                )

            reg.close()

    def test_321_idempotent_approval(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry(Path(td) / "r.sqlite3")
            svc = RootCommandOrderingService(reg)

            a, duplicate_a = svc.record_idempotent_operation(
                operation_type="APPROVAL",
                idempotency_key="approval-001",
                payload={
                    "command_id": "cmd-1",
                    "version": 1,
                },
                result_ref="approval-result-1",
            )

            b, duplicate_b = svc.record_idempotent_operation(
                operation_type="APPROVAL",
                idempotency_key="approval-001",
                payload={
                    "command_id": "cmd-1",
                    "version": 1,
                },
                result_ref="ignored-second-result",
            )

            self.assertFalse(duplicate_a)
            self.assertTrue(duplicate_b)
            self.assertEqual(
                a["result_ref"],
                b["result_ref"],
            )

            reg.close()

    def test_321_idempotency_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            reg = CommandRegistry(Path(td) / "r.sqlite3")
            svc = RootCommandOrderingService(reg)

            svc.record_idempotent_operation(
                operation_type="APPROVAL",
                idempotency_key="same-key",
                payload={"version": 1},
                result_ref="result-1",
            )

            with self.assertRaises(IdempotencyConflict):
                svc.record_idempotent_operation(
                    operation_type="APPROVAL",
                    idempotency_key="same-key",
                    payload={"version": 2},
                    result_ref="result-2",
                )

            reg.close()

    def test_321_emergency_has_ordering_precedence(self):
        svc = RootCommandOrderingService(None)

        commands = [
            {
                "command_id": "cmd-normal",
                "command_type": "PROJECT_DIRECTIVE",
                "priority": "P0",
                "created_at": "2026-08-16T10:00:00Z",
            },
            {
                "command_id": "cmd-stop",
                "command_type": "EMERGENCY_STOP",
                "priority": "P9",
                "created_at": "2026-08-16T11:00:00Z",
            },
        ]

        ordered = svc.order(commands)

        self.assertEqual(
            ordered[0]["command_id"],
            "cmd-stop",
        )

if __name__ == "__main__":
    unittest.main()
