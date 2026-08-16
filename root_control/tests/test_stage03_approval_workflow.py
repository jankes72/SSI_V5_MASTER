
import unittest

from root_control.approvals.workflow import (
    ApprovalWorkflow,
    Proposal,
    StaleProposal,
    WrongScope,
    SessionExpired,
    StepUpRequired,
)


IDENTITY = {
    "root_identity_id": "root-001",
    "session_id": "session-001",
    "device_id": "device-001",
}


def proposal(version=1, risk="HIGH"):
    return Proposal(
        proposal_id="proposal-001",
        version=version,
        goal="Controlled maintenance",
        plan="Validate then prepare controlled change",
        scope="SSI_V5_MASTER",
        effects=("configuration_change",),
        risk=risk,
        data_classes=("INTERNAL",),
        estimated_cost="LOW",
        timeout_seconds=300,
        rollback="restore-scoped-backup",
        required_assurance="STEP_UP",
    )


class ApprovalTests(unittest.TestCase):

    def test_correct_high_risk_approval(self):
        w = ApprovalWorkflow()
        w.register_proposal(proposal())

        a, duplicate = w.decide(
            proposal_id="proposal-001",
            proposal_version=1,
            scope="SSI_V5_MASTER",
            decision="APPROVE",
            identity=IDENTITY,
            session_valid=True,
            assurance="STEP_UP",
            step_up_confirmed=True,
            idempotency_key="k1",
        )

        self.assertFalse(duplicate)
        self.assertTrue(w.is_valid_for_current_proposal(a))

    def test_stale_proposal_rejected(self):
        w = ApprovalWorkflow()
        w.register_proposal(proposal(version=2))

        with self.assertRaises(StaleProposal):
            w.decide(
                proposal_id="proposal-001",
                proposal_version=1,
                scope="SSI_V5_MASTER",
                decision="APPROVE",
                identity=IDENTITY,
                session_valid=True,
                assurance="STEP_UP",
                step_up_confirmed=True,
                idempotency_key="k2",
            )

    def test_wrong_scope_rejected(self):
        w = ApprovalWorkflow()
        w.register_proposal(proposal())

        with self.assertRaises(WrongScope):
            w.decide(
                proposal_id="proposal-001",
                proposal_version=1,
                scope="OTHER",
                decision="APPROVE",
                identity=IDENTITY,
                session_valid=True,
                assurance="STEP_UP",
                step_up_confirmed=True,
                idempotency_key="k3",
            )

    def test_expired_session_rejected(self):
        w = ApprovalWorkflow()
        w.register_proposal(proposal())

        with self.assertRaises(SessionExpired):
            w.decide(
                proposal_id="proposal-001",
                proposal_version=1,
                scope="SSI_V5_MASTER",
                decision="APPROVE",
                identity=IDENTITY,
                session_valid=False,
                assurance="STEP_UP",
                step_up_confirmed=True,
                idempotency_key="k4",
            )

    def test_high_risk_requires_step_up(self):
        w = ApprovalWorkflow()
        w.register_proposal(proposal())

        with self.assertRaises(StepUpRequired):
            w.decide(
                proposal_id="proposal-001",
                proposal_version=1,
                scope="SSI_V5_MASTER",
                decision="APPROVE",
                identity=IDENTITY,
                session_valid=True,
                assurance="STEP_UP",
                step_up_confirmed=False,
                idempotency_key="k5",
            )

    def test_replay_and_double_click_are_idempotent(self):
        w = ApprovalWorkflow()
        w.register_proposal(proposal())

        args = dict(
            proposal_id="proposal-001",
            proposal_version=1,
            scope="SSI_V5_MASTER",
            decision="APPROVE",
            identity=IDENTITY,
            session_valid=True,
            assurance="STEP_UP",
            step_up_confirmed=True,
            idempotency_key="double-click",
        )

        a, d1 = w.decide(**args)
        b, d2 = w.decide(**args)

        self.assertFalse(d1)
        self.assertTrue(d2)
        self.assertEqual(a.approval_id, b.approval_id)

    def test_new_proposal_version_invalidates_old_approval(self):
        w = ApprovalWorkflow()
        w.register_proposal(proposal(version=1))

        a, _ = w.decide(
            proposal_id="proposal-001",
            proposal_version=1,
            scope="SSI_V5_MASTER",
            decision="APPROVE",
            identity=IDENTITY,
            session_valid=True,
            assurance="STEP_UP",
            step_up_confirmed=True,
            idempotency_key="old",
        )

        self.assertTrue(w.is_valid_for_current_proposal(a))

        w.register_proposal(proposal(version=2))

        self.assertFalse(w.is_valid_for_current_proposal(a))

    def test_reject_and_request_changes_are_not_approval(self):
        for decision in ("REJECT", "REQUEST_CHANGES", "EXPIRE"):
            w = ApprovalWorkflow()
            w.register_proposal(proposal())

            a, _ = w.decide(
                proposal_id="proposal-001",
                proposal_version=1,
                scope="SSI_V5_MASTER",
                decision=decision,
                identity=IDENTITY,
                session_valid=True,
                assurance="STEP_UP",
                step_up_confirmed=True,
                idempotency_key=decision,
            )

            self.assertFalse(w.is_valid_for_current_proposal(a))


if __name__ == "__main__":
    unittest.main()
