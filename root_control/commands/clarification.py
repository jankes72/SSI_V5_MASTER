
from dataclasses import dataclass, asdict
from copy import deepcopy
import hashlib
import json


class ClarificationError(Exception):
    pass


class ClarificationNotRequired(ClarificationError):
    pass


class ClarificationAlreadyResolved(ClarificationError):
    pass


class ClarificationVersionConflict(ClarificationError):
    pass


class ClarificationUnauthorized(ClarificationError):
    pass


class ClarificationExpired(ClarificationError):
    pass


@dataclass(frozen=True)
class ClarificationRequest:
    clarification_id: str
    command_id: str
    command_version: int
    reason_code: str
    question: str
    requested_fields: tuple[str, ...]
    created_at: str
    expires_at: str | None = None

    def to_dict(self):
        d = asdict(self)
        d["requested_fields"] = list(self.requested_fields)
        return d


@dataclass(frozen=True)
class ClarificationResponse:
    clarification_id: str
    command_id: str
    base_command_version: int
    answers: dict
    root_identity_id: str
    session_id: str
    device_id: str
    response_hash: str

    def to_dict(self):
        return asdict(self)


def canonical_hash(value: dict) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


class ClarificationService:
    """
    Stage 03 clarification boundary.

    Clarification does not execute a command.
    Clarification does not grant authority.
    Original command content is never overwritten.
    """

    def __init__(self, *, authorize):
        self.authorize = authorize
        self._requests = {}
        self._responses = {}

    def create_request(
        self,
        *,
        clarification_id,
        command_id,
        command_version,
        reason_code,
        question,
        requested_fields,
        created_at,
        expires_at=None,
    ):
        if not reason_code or not question:
            raise ClarificationNotRequired(
                "MATERIAL_AMBIGUITY_REQUIRED"
            )

        req = ClarificationRequest(
            clarification_id=clarification_id,
            command_id=command_id,
            command_version=command_version,
            reason_code=reason_code,
            question=question,
            requested_fields=tuple(requested_fields),
            created_at=created_at,
            expires_at=expires_at,
        )

        old = self._requests.get(clarification_id)

        if old is not None and old != req:
            raise ClarificationVersionConflict(
                "CLARIFICATION_ID_CONFLICT"
            )

        self._requests[clarification_id] = req
        return req

    def respond(
        self,
        *,
        clarification_id,
        base_command_version,
        answers,
        identity_context,
        now_value,
    ):
        req = self._requests.get(clarification_id)

        if req is None:
            raise ClarificationError(
                "UNKNOWN_CLARIFICATION"
            )

        if not self.authorize(identity_context):
            raise ClarificationUnauthorized(
                "ROOT_AUTHORIZATION_REQUIRED"
            )

        if req.expires_at is not None and now_value > req.expires_at:
            raise ClarificationExpired(
                "CLARIFICATION_EXPIRED"
            )

        if base_command_version != req.command_version:
            raise ClarificationVersionConflict(
                "STALE_COMMAND_VERSION"
            )

        required = set(req.requested_fields)

        if not required.issubset(set(answers)):
            raise ClarificationError(
                "MISSING_CLARIFICATION_FIELDS"
            )

        payload = {
            "clarification_id": clarification_id,
            "command_id": req.command_id,
            "base_command_version": base_command_version,
            "answers": deepcopy(answers),
            "root_identity_id":
                identity_context["root_identity_id"],
            "session_id": identity_context["session_id"],
            "device_id": identity_context["device_id"],
        }

        response_hash = canonical_hash(payload)

        response = ClarificationResponse(
            **payload,
            response_hash=response_hash,
        )

        old = self._responses.get(clarification_id)

        if old is not None:
            if old == response:
                return {
                    "status": "DUPLICATE_ACCEPTED",
                    "response": old,
                    "command_executed": False,
                    "authority_granted": False,
                }

            raise ClarificationAlreadyResolved(
                "CONFLICTING_SECOND_RESPONSE"
            )

        self._responses[clarification_id] = response

        return {
            "status": "CLARIFICATION_RECORDED",
            "response": response,
            "command_executed": False,
            "authority_granted": False,
        }

    def derive_command_revision(
        self,
        *,
        immutable_original: dict,
        clarification_id: str,
    ):
        response = self._responses.get(clarification_id)

        if response is None:
            raise ClarificationError(
                "CLARIFICATION_NOT_RESOLVED"
            )

        # Preserve immutable original evidence.
        derived = deepcopy(immutable_original)

        derived["derived_from_command_version"] = (
            response.base_command_version
        )
        derived["clarification_id"] = clarification_id
        derived["clarification_response_hash"] = (
            response.response_hash
        )
        derived["clarified_fields"] = deepcopy(
            response.answers
        )

        return derived
