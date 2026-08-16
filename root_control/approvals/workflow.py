
from dataclasses import dataclass, asdict
from copy import deepcopy
import hashlib
import json


class ApprovalError(Exception):
    pass

class StaleProposal(ApprovalError):
    pass

class WrongScope(ApprovalError):
    pass

class SessionExpired(ApprovalError):
    pass

class StepUpRequired(ApprovalError):
    pass

class ReplayConflict(ApprovalError):
    pass


def digest(v):
    return hashlib.sha256(
        json.dumps(
            v,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    version: int
    goal: str
    plan: str
    scope: str
    effects: tuple
    risk: str
    data_classes: tuple
    estimated_cost: str
    timeout_seconds: int
    rollback: str
    required_assurance: str

    def snapshot(self):
        return asdict(self)


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    proposal_id: str
    proposal_version: int
    proposal_hash: str
    scope: str
    decision: str
    actor_id: str
    session_id: str
    device_id: str
    assurance: str
    reason: str | None


class ApprovalWorkflow:

    VALID_DECISIONS = {
        "APPROVE",
        "REJECT",
        "REQUEST_CHANGES",
        "EXPIRE",
    }

    def __init__(self):
        self._proposals = {}
        self._decisions = {}
        self._counter = 0

    def register_proposal(self, proposal):
        key = proposal.proposal_id
        current = self._proposals.get(key)

        if current and proposal.version < current.version:
            raise StaleProposal("PROPOSAL_VERSION_REGRESSION")

        self._proposals[key] = proposal
        return deepcopy(proposal)

    def view(self, proposal_id):
        p = self._proposals[proposal_id]
        return {
            **p.snapshot(),
            "proposal_hash": digest(p.snapshot()),
            "exceptions_hidden": False,
            "constraints_hidden": False,
            "mobile_fields_collapsible": False,
        }

    def decide(
        self,
        *,
        proposal_id,
        proposal_version,
        scope,
        decision,
        identity,
        session_valid,
        assurance,
        step_up_confirmed,
        reason=None,
        idempotency_key,
    ):
        if not session_valid:
            raise SessionExpired("SESSION_EXPIRED")

        if decision not in self.VALID_DECISIONS:
            raise ApprovalError("INVALID_DECISION")

        p = self._proposals[proposal_id]

        if proposal_version != p.version:
            raise StaleProposal("STALE_PROPOSAL")

        if scope != p.scope:
            raise WrongScope("SCOPE_MISMATCH")

        high = p.risk in {"HIGH", "CRITICAL"}

        if high:
            if assurance != p.required_assurance:
                raise StepUpRequired("ASSURANCE_TOO_LOW")
            if step_up_confirmed is not True:
                raise StepUpRequired(
                    "FINAL_VERSION_STEP_UP_REQUIRED"
                )

        decision_material = {
            "proposal_id": p.proposal_id,
            "proposal_version": p.version,
            "proposal_hash": digest(p.snapshot()),
            "scope": p.scope,
            "decision": decision,
            "actor_id": identity["root_identity_id"],
            "session_id": identity["session_id"],
            "device_id": identity["device_id"],
            "assurance": assurance,
            "reason": reason,
        }

        existing = self._decisions.get(idempotency_key)

        if existing:
            if asdict(existing) == {
                **decision_material,
                "approval_id": existing.approval_id,
            }:
                return existing, True
            raise ReplayConflict("IDEMPOTENCY_CONFLICT")

        self._counter += 1

        rec = ApprovalDecision(
            approval_id=f"approval-{self._counter:04d}",
            **decision_material,
        )

        self._decisions[idempotency_key] = rec
        return rec, False

    def is_valid_for_current_proposal(self, approval):
        current = self._proposals.get(approval.proposal_id)

        if current is None:
            return False

        return (
            approval.proposal_version == current.version
            and approval.proposal_hash ==
                digest(current.snapshot())
            and approval.scope == current.scope
            and approval.decision == "APPROVE"
        )
