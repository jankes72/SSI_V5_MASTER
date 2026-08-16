
from dataclasses import dataclass
import hashlib
import json
import uuid

class IntakeError(Exception):
    pass

class AuthenticationError(IntakeError):
    pass

class AuthorizationError(IntakeError):
    pass

class ValidationError(IntakeError):
    pass

class IdempotencyConflict(IntakeError):
    pass

class StoreUnavailable(IntakeError):
    pass

def canonical_hash(value):
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

@dataclass(frozen=True)
class IntakeReceipt:
    command_id: str
    receipt_id: str
    duplicate: bool
    persisted: bool
    status: str

class CommandIntakeService:
    def __init__(self, repository, session_validator, authorizer):
        self.repository = repository
        self.session_validator = session_validator
        self.authorizer = authorizer
        self.audit_events = []

    @staticmethod
    def stable_ids(root_identity_id, idempotency_key):
        namespace = uuid.UUID(
            "626f6f74-636f-6e74-726f-6c0000000003"
        )
        command_id = "cmd_" + uuid.uuid5(
            namespace,
            root_identity_id + "|" + idempotency_key
        ).hex
        receipt_id = "rcpt_" + uuid.uuid5(
            namespace,
            "receipt|" + root_identity_id + "|" + idempotency_key
        ).hex
        return command_id, receipt_id

    def submit(self, request):
        if not self.session_validator(request):
            raise AuthenticationError("INVALID_SESSION")

        if not self.authorizer(request):
            raise AuthorizationError("UNAUTHORIZED_REQUEST")

        required = {
            "idempotency_key",
            "root_identity_id",
            "session_id",
            "device_id",
            "original_content",
            "payload_hash",
        }

        missing = sorted(required - set(request))
        if missing:
            raise ValidationError(
                "MISSING_FIELDS:" + ",".join(missing)
            )

        expected_hash = canonical_hash({
            "original_content": request["original_content"]
        })

        if request["payload_hash"] != expected_hash:
            raise ValidationError("ALTERED_OR_INVALID_CONTENT")

        command_id, receipt_id = self.stable_ids(
            request["root_identity_id"],
            request["idempotency_key"]
        )

        record = {
            "command_id": command_id,
            "receipt_id": receipt_id,
            "idempotency_key": request["idempotency_key"],
            "root_identity_id": request["root_identity_id"],
            "session_id": request["session_id"],
            "device_id": request["device_id"],
            "source_channel": request.get(
                "source_channel",
                "UNSPECIFIED"
            ),
            "original_content": request["original_content"],
            "payload_hash": request["payload_hash"],

            # Future-compatible addressing.
            # These fields do NOT create authority.
            "target_scope": request.get("target_scope"),
            "target_agent_id": request.get("target_agent_id"),
            "target_world_id": request.get("target_world_id"),
            "target_domain_id": request.get("target_domain_id"),
        }

        persisted, duplicate = self.repository.persist_intake(
            record
        )

        # ACK/audit occurs only after repository confirmed persistence.
        self.audit_events.append({
            "type": "ROOT_COMMAND_INTAKE",
            "command_id": persisted["command_id"],
            "duplicate": duplicate,
        })

        return IntakeReceipt(
            command_id=persisted["command_id"],
            receipt_id=persisted["receipt_id"],
            duplicate=duplicate,
            persisted=True,
            status="RECEIVED",
        )
