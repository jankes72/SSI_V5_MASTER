
from dataclasses import dataclass
from datetime import datetime, timezone

class InvalidTransition(Exception):
    pass

ALLOWED = {
    "RECEIVED": {
        "AUTHENTICATING",
    },
    "AUTHENTICATING": {
        "REJECTED_UNAUTHENTICATED",
        "VALIDATING",
    },
    "VALIDATING": {
        "CLARIFICATION_REQUIRED",
        "ACCEPTED",
        "FAILED",
    },
    "CLARIFICATION_REQUIRED": {
        "VALIDATING",
        "CANCELLED",
        "SUPERSEDED",
    },
    "ACCEPTED": {
        "PLANNING",
        "CANCELLED",
        "SUPERSEDED",
    },
    "PLANNING": {
        "WAITING_FOR_APPROVAL",
        "SCHEDULED",
        "BLOCKED",
        "FAILED",
        "CANCELLED",
        "SUPERSEDED",
    },
    "WAITING_FOR_APPROVAL": {
        "SCHEDULED",
        "BLOCKED",
        "CANCELLED",
        "SUPERSEDED",
    },
    "SCHEDULED": {
        "IN_PROGRESS",
        "BLOCKED",
        "CANCELLED",
        "SUPERSEDED",
    },
    "IN_PROGRESS": {
        "PAUSED",
        "BLOCKED",
        "COMPLETED",
        "COMPLETED_WITH_RESTRICTIONS",
        "FAILED",
        "CANCELLED",
        "SUPERSEDED",
    },
    "PAUSED": {
        "IN_PROGRESS",
        "BLOCKED",
        "CANCELLED",
        "SUPERSEDED",
    },
    "BLOCKED": {
        "PLANNING",
        "IN_PROGRESS",
        "FAILED",
        "CANCELLED",
        "SUPERSEDED",
    },
}

@dataclass(frozen=True)
class TransitionEvidence:
    command_id: str
    previous_status: str
    next_status: str
    previous_version: int
    new_version: int
    actor: str
    condition: str
    reason: str
    timestamp: str
    correlation_id: str

class CommandStateMachine:
    def __init__(self, registry):
        self.registry = registry
        self.audit = []

    def transition(
        self,
        command_id,
        *,
        expected_version,
        next_status,
        actor,
        condition,
        reason,
        correlation_id,
    ):
        current = self.registry.get(command_id)

        if current is None:
            raise KeyError(command_id)

        previous = current["status"]

        if next_status not in ALLOWED.get(previous, set()):
            raise InvalidTransition(
                f"INVALID_TRANSITION:{previous}->{next_status}"
            )

        timestamp = datetime.now(timezone.utc).isoformat()

        updated = self.registry.transition(
            command_id,
            expected_version=expected_version,
            new_status=next_status,
            updated_at=timestamp,
        )

        evidence = TransitionEvidence(
            command_id=command_id,
            previous_status=previous,
            next_status=next_status,
            previous_version=expected_version,
            new_version=updated["version"],
            actor=actor,
            condition=condition,
            reason=reason,
            timestamp=timestamp,
            correlation_id=correlation_id,
        )

        self.audit.append(evidence)

        return updated, evidence
