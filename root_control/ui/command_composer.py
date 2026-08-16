
from dataclasses import dataclass, field, asdict
from copy import deepcopy
from typing import Protocol


class ComposerError(Exception):
    pass


class MissingField(ComposerError):
    pass


class ExpiredSession(ComposerError):
    pass


class ServerRejected(ComposerError):
    pass


class OfflineSubmitForbidden(ComposerError):
    pass


class IntakeClient(Protocol):
    def submit_candidate(self, candidate: dict) -> dict:
        ...


@dataclass
class CommandDraft:
    original_content: str = ""
    goal: str = ""
    scope: str = ""
    priority: str = "P3"
    deadline: str | None = None
    constraints: list[str] = field(default_factory=list)
    attachments_metadata: list[dict] = field(default_factory=list)

    # Draft is deliberately non-authoritative.
    command_id: None = None
    receipt_id: None = None
    authority: str = "NONE"
    record_type: str = "COMMAND_DRAFT"

    def to_dict(self):
        return asdict(self)


class CommandComposer:
    REQUIRED = (
        "original_content",
        "goal",
        "scope",
        "priority",
    )

    def __init__(self, intake_client: IntakeClient):
        self.intake_client = intake_client
        self._offline_draft = None

    @staticmethod
    def preliminary_risk(draft: CommandDraft) -> str:
        text = (
            draft.original_content + " " +
            draft.goal + " " +
            draft.scope
        ).lower()

        high_markers = (
            "delete",
            "production",
            "deploy",
            "secret",
            "credential",
            "emergency",
            "shutdown",
        )

        if any(x in text for x in high_markers):
            return "REVIEW_REQUIRED"

        return "UNCLASSIFIED_PRELIMINARY"

    def validate_draft(self, draft: CommandDraft):
        data = draft.to_dict()

        missing = [
            name for name in self.REQUIRED
            if not str(data.get(name) or "").strip()
        ]

        if missing:
            raise MissingField(
                "MISSING_REQUIRED_FIELDS:" + ",".join(missing)
            )

        return True

    def preview(self, draft: CommandDraft) -> dict:
        self.validate_draft(draft)

        return {
            "record_type": "COMMAND_CANDIDATE_PREVIEW",
            "authoritative": False,
            "original_content": draft.original_content,
            "goal": draft.goal,
            "scope": draft.scope,
            "priority": draft.priority,
            "deadline": draft.deadline,
            "constraints": deepcopy(draft.constraints),
            "attachments_metadata": deepcopy(
                draft.attachments_metadata
            ),
            "preliminary_risk": self.preliminary_risk(draft),
            "requires_explicit_confirmation": True,
        }

    def save_offline_draft(self, draft: CommandDraft) -> dict:
        self.validate_draft(draft)
        self._offline_draft = deepcopy(draft)

        return {
            "saved": True,
            "record_type": "COMMAND_DRAFT",
            "command_created": False,
            "authority_created": False,
        }

    def submit(
        self,
        draft: CommandDraft,
        *,
        session_valid: bool,
        online: bool,
        explicit_confirmation: bool,
        idempotency_key: str,
        profile: str,
    ) -> dict:

        self.validate_draft(draft)

        if not session_valid:
            raise ExpiredSession("SESSION_EXPIRED")

        if not online:
            raise OfflineSubmitForbidden(
                "OFFLINE_DRAFT_MUST_NOT_BECOME_COMMAND"
            )

        if explicit_confirmation is not True:
            raise ComposerError(
                "EXPLICIT_CONFIRMATION_REQUIRED"
            )

        # LOCAL and MOBILE intentionally produce the same
        # semantic candidate. Profile is transport/UI context only.
        candidate = {
            "record_type": "COMMAND_CANDIDATE",
            "original_content": draft.original_content,
            "goal": draft.goal,
            "scope": draft.scope,
            "priority": draft.priority,
            "deadline": draft.deadline,
            "constraints": deepcopy(draft.constraints),
            "attachments_metadata": deepcopy(
                draft.attachments_metadata
            ),
            "idempotency_key": idempotency_key,
        }

        response = self.intake_client.submit_candidate(candidate)

        if not response.get("accepted"):
            raise ServerRejected(
                response.get("reason", "SERVER_REJECTED")
            )

        if not response.get("command_id"):
            raise ServerRejected(
                "SERVER_DID_NOT_ASSIGN_COMMAND_ID"
            )

        if not response.get("receipt_id"):
            raise ServerRejected(
                "SERVER_DID_NOT_ASSIGN_RECEIPT"
            )

        return {
            "command_id": response["command_id"],
            "receipt_id": response["receipt_id"],
            "duplicate": bool(response.get("duplicate", False)),
            "server_record": deepcopy(
                response.get("server_record", candidate)
            ),
            "profile": profile,
        }
