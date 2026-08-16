
from dataclasses import dataclass
import hashlib
import json

class RequestEnvelopeError(Exception):
    pass

@dataclass(frozen=True)
class RootRequestEnvelope:
    request_id: str
    idempotency_key: str
    root_identity_id: str
    session_id: str
    device_id: str
    request_type: str
    issued_at: str
    expires_at: str
    payload: dict
    payload_hash: str

    @staticmethod
    def hash_payload(payload):
        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":")
        ).encode("utf-8")

        return hashlib.sha256(raw).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        request_id,
        idempotency_key,
        root_identity_id,
        session_id,
        device_id,
        request_type,
        issued_at,
        expires_at,
        payload
    ):
        if not request_id:
            raise RequestEnvelopeError("MISSING_REQUEST_ID")

        if not idempotency_key:
            raise RequestEnvelopeError("MISSING_IDEMPOTENCY_KEY")

        if not root_identity_id or not session_id or not device_id:
            raise RequestEnvelopeError("MISSING_IDENTITY_BINDING")

        return cls(
            request_id=request_id,
            idempotency_key=idempotency_key,
            root_identity_id=root_identity_id,
            session_id=session_id,
            device_id=device_id,
            request_type=request_type,
            issued_at=issued_at,
            expires_at=expires_at,
            payload=payload,
            payload_hash=cls.hash_payload(payload),
        )

    def verify_integrity(self):
        if self.payload_hash != self.hash_payload(self.payload):
            raise RequestEnvelopeError("PAYLOAD_INTEGRITY_FAILURE")

        return True
