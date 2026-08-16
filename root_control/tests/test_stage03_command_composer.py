
import unittest

from root_control.ui.command_composer import (
    CommandComposer,
    CommandDraft,
    MissingField,
    ExpiredSession,
    ServerRejected,
    OfflineSubmitForbidden,
)


class FakeIntake:
    def __init__(self, reject=False):
        self.reject = reject
        self.calls = []
        self.by_key = {}

    def submit_candidate(self, candidate):
        self.calls.append(candidate.copy())

        if self.reject:
            return {
                "accepted": False,
                "reason": "POLICY_REJECTED",
            }

        key = candidate["idempotency_key"]

        if key in self.by_key:
            old = self.by_key[key]
            return {
                **old,
                "duplicate": True,
            }

        result = {
            "accepted": True,
            "command_id": "cmd-server-001",
            "receipt_id": "receipt-server-001",
            "duplicate": False,
            "server_record": candidate.copy(),
        }

        self.by_key[key] = result
        return result


def valid_draft():
    return CommandDraft(
        original_content="Analyze the selected project state",
        goal="Produce evidence-based analysis",
        scope="PROJECT",
        priority="P3",
        deadline="2026-08-17T12:00:00+02:00",
        constraints=[
            "Do not execute Runtime",
            "Preserve original content",
        ],
        attachments_metadata=[
            {
                "name": "evidence.txt",
                "media_type": "text/plain",
                "size": 128,
            }
        ],
    )


class CommandComposerTests(unittest.TestCase):

    def test_valid_form_and_server_assigned_ids(self):
        intake = FakeIntake()
        ui = CommandComposer(intake)
        draft = valid_draft()

        preview = ui.preview(draft)

        self.assertFalse(preview["authoritative"])
        self.assertTrue(
            preview["requires_explicit_confirmation"]
        )

        result = ui.submit(
            draft,
            session_valid=True,
            online=True,
            explicit_confirmation=True,
            idempotency_key="idem-ui-001",
            profile="LOCAL",
        )

        self.assertEqual(
            result["command_id"],
            "cmd-server-001"
        )
        self.assertEqual(
            result["receipt_id"],
            "receipt-server-001"
        )

        # Client draft never received authority.
        self.assertIsNone(draft.command_id)
        self.assertIsNone(draft.receipt_id)
        self.assertEqual(draft.authority, "NONE")

    def test_missing_field(self):
        ui = CommandComposer(FakeIntake())
        draft = valid_draft()
        draft.goal = ""

        with self.assertRaises(MissingField):
            ui.preview(draft)

    def test_duplicate_submit_same_logical_result(self):
        intake = FakeIntake()
        ui = CommandComposer(intake)
        draft = valid_draft()

        a = ui.submit(
            draft,
            session_valid=True,
            online=True,
            explicit_confirmation=True,
            idempotency_key="same-key",
            profile="LOCAL",
        )

        b = ui.submit(
            draft,
            session_valid=True,
            online=True,
            explicit_confirmation=True,
            idempotency_key="same-key",
            profile="MOBILE",
        )

        self.assertEqual(a["command_id"], b["command_id"])
        self.assertEqual(a["receipt_id"], b["receipt_id"])
        self.assertTrue(b["duplicate"])

    def test_expired_session(self):
        ui = CommandComposer(FakeIntake())

        with self.assertRaises(ExpiredSession):
            ui.submit(
                valid_draft(),
                session_valid=False,
                online=True,
                explicit_confirmation=True,
                idempotency_key="expired-1",
                profile="MOBILE",
            )

    def test_offline_draft_does_not_create_command(self):
        intake = FakeIntake()
        ui = CommandComposer(intake)
        draft = valid_draft()

        saved = ui.save_offline_draft(draft)

        self.assertTrue(saved["saved"])
        self.assertFalse(saved["command_created"])
        self.assertFalse(saved["authority_created"])
        self.assertEqual(len(intake.calls), 0)

        with self.assertRaises(OfflineSubmitForbidden):
            ui.submit(
                draft,
                session_valid=True,
                online=False,
                explicit_confirmation=True,
                idempotency_key="offline-1",
                profile="MOBILE",
            )

        self.assertEqual(len(intake.calls), 0)

    def test_server_rejection(self):
        ui = CommandComposer(FakeIntake(reject=True))

        with self.assertRaises(ServerRejected):
            ui.submit(
                valid_draft(),
                session_valid=True,
                online=True,
                explicit_confirmation=True,
                idempotency_key="reject-1",
                profile="LOCAL",
            )

    def test_local_mobile_server_record_parity(self):
        draft = valid_draft()

        local_intake = FakeIntake()
        mobile_intake = FakeIntake()

        local = CommandComposer(local_intake).submit(
            draft,
            session_valid=True,
            online=True,
            explicit_confirmation=True,
            idempotency_key="parity-key",
            profile="LOCAL",
        )

        mobile = CommandComposer(mobile_intake).submit(
            draft,
            session_valid=True,
            online=True,
            explicit_confirmation=True,
            idempotency_key="parity-key",
            profile="MOBILE",
        )

        self.assertEqual(
            local["server_record"],
            mobile["server_record"]
        )

    def test_explicit_confirmation_required(self):
        ui = CommandComposer(FakeIntake())

        with self.assertRaises(Exception):
            ui.submit(
                valid_draft(),
                session_valid=True,
                online=True,
                explicit_confirmation=False,
                idempotency_key="confirm-1",
                profile="LOCAL",
            )


if __name__ == "__main__":
    unittest.main()
