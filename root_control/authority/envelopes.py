
from dataclasses import dataclass, asdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json


class AuthorityEnvelopeError(Exception):
    pass


class SchemaViolation(AuthorityEnvelopeError):
    pass


class ConstitutionalViolation(AuthorityEnvelopeError):
    pass


class EnvelopeExpired(AuthorityEnvelopeError):
    pass


class EnvelopeRevoked(AuthorityEnvelopeError):
    pass


class UsageLimitExceeded(AuthorityEnvelopeError):
    pass


class ScopeEscalation(AuthorityEnvelopeError):
    pass


class ResourceLimitExceeded(AuthorityEnvelopeError):
    pass


def canonical_hash(value):
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class AuthorityEnvelope:
    envelope_id: str
    subject: str
    operation: str
    resource: str
    purpose: str
    environment: str
    context: dict
    risk_limit: str
    time_limit_seconds: int
    usage_limit: int
    resource_limits: dict
    required_tests: tuple
    prohibitions: tuple
    expires_at: str
    issuer: str
    issued_at: str
    status: str = "ACTIVE"

    def material(self):
        d = asdict(self)
        d["required_tests"] = list(self.required_tests)
        d["prohibitions"] = list(self.prohibitions)
        return d

    @property
    def envelope_hash(self):
        return canonical_hash(self.material())


class AuthorityEnvelopeRegistry:
    """
    Stage 03 control-plane representation only.

    This registry does NOT connect to Runtime.
    It does NOT execute delegated operations.
    """

    REQUIRED = (
        "envelope_id",
        "subject",
        "operation",
        "resource",
        "purpose",
        "environment",
        "context",
        "risk_limit",
        "time_limit_seconds",
        "usage_limit",
        "resource_limits",
        "required_tests",
        "prohibitions",
        "expires_at",
        "issuer",
        "issued_at",
    )

    RISK_ORDER = {
        "NONE": 0,
        "LOW": 1,
        "MEDIUM": 2,
        "HIGH": 3,
        "CRITICAL": 4,
    }

    def __init__(
        self,
        *,
        allowed_subjects,
        allowed_operations,
        protected_assets,
        max_risk_by_subject,
    ):
        self.allowed_subjects = set(allowed_subjects)
        self.allowed_operations = set(allowed_operations)
        self.protected_assets = set(protected_assets)
        self.max_risk_by_subject = dict(max_risk_by_subject)
        self._records = {}
        self._usage = {}
        self._revocations = {}

    def validate_schema(self, data):
        missing = [
            k for k in self.REQUIRED
            if k not in data or data[k] in (None, "", [], ())
        ]
        if missing:
            raise SchemaViolation(
                "MISSING_REQUIRED_FIELDS:" + ",".join(missing)
            )

        if not isinstance(data["context"], dict):
            raise SchemaViolation("CONTEXT_MUST_BE_OBJECT")

        if not isinstance(data["resource_limits"], dict):
            raise SchemaViolation("RESOURCE_LIMITS_MUST_BE_OBJECT")

        if not isinstance(data["usage_limit"], int) or data["usage_limit"] < 1:
            raise SchemaViolation("INVALID_USAGE_LIMIT")

        if (
            not isinstance(data["time_limit_seconds"], int)
            or data["time_limit_seconds"] < 1
        ):
            raise SchemaViolation("INVALID_TIME_LIMIT")

        if data["risk_limit"] not in self.RISK_ORDER:
            raise SchemaViolation("INVALID_RISK_LIMIT")

        return True

    def validate_constitution(self, data):
        if data["subject"] not in self.allowed_subjects:
            raise ConstitutionalViolation("SUBJECT_NOT_ALLOWED")

        if data["operation"] not in self.allowed_operations:
            raise ConstitutionalViolation("OPERATION_NOT_ALLOWED")

        subject_max = self.max_risk_by_subject.get(
            data["subject"], "NONE"
        )

        if (
            self.RISK_ORDER[data["risk_limit"]]
            > self.RISK_ORDER[subject_max]
        ):
            raise ConstitutionalViolation(
                "RISK_EXCEEDS_SUBJECT_AUTHORITY"
            )

        # Stage 03 may describe protected resources,
        # but cannot create a direct production execution grant.
        if (
            data["resource"] in self.protected_assets
            and data["environment"] == "PRODUCTION_RUNTIME"
        ):
            raise ConstitutionalViolation(
                "STAGE03_PRODUCTION_RUNTIME_GRANT_FORBIDDEN"
            )

        return True

    def create(self, data):
        data = deepcopy(data)
        self.validate_schema(data)
        self.validate_constitution(data)

        eid = data["envelope_id"]

        env = AuthorityEnvelope(
            envelope_id=eid,
            subject=data["subject"],
            operation=data["operation"],
            resource=data["resource"],
            purpose=data["purpose"],
            environment=data["environment"],
            context=deepcopy(data["context"]),
            risk_limit=data["risk_limit"],
            time_limit_seconds=data["time_limit_seconds"],
            usage_limit=data["usage_limit"],
            resource_limits=deepcopy(data["resource_limits"]),
            required_tests=tuple(data["required_tests"]),
            prohibitions=tuple(data["prohibitions"]),
            expires_at=data["expires_at"],
            issuer=data["issuer"],
            issued_at=data["issued_at"],
        )

        old = self._records.get(eid)

        if old is not None:
            if old == env:
                return old
            raise ConstitutionalViolation(
                "IMMUTABLE_ENVELOPE_ID_CONFLICT"
            )

        self._records[eid] = env
        self._usage[eid] = []
        return env

    def revoke(self, envelope_id, *, actor, reason, revoked_at):
        env = self._records[envelope_id]

        old = self._revocations.get(envelope_id)

        record = {
            "envelope_id": envelope_id,
            "envelope_hash": env.envelope_hash,
            "actor": actor,
            "reason": reason,
            "revoked_at": revoked_at,
        }

        if old is not None:
            if old == record:
                return deepcopy(old)
            raise ConstitutionalViolation(
                "REVOCATION_CONFLICT"
            )

        self._revocations[envelope_id] = record
        return deepcopy(record)

    def status(self, envelope_id, *, now_value):
        env = self._records[envelope_id]

        if envelope_id in self._revocations:
            return "REVOKED"

        if now_value >= env.expires_at:
            return "EXPIRED"

        if len(self._usage[envelope_id]) >= env.usage_limit:
            return "USAGE_EXHAUSTED"

        return "ACTIVE"

    def authorize_use(
        self,
        envelope_id,
        *,
        subject,
        operation,
        resource,
        risk,
        resource_request,
        now_value,
        usage_id,
    ):
        env = self._records[envelope_id]

        status = self.status(envelope_id, now_value=now_value)

        if status == "REVOKED":
            raise EnvelopeRevoked("ENVELOPE_REVOKED")

        if status == "EXPIRED":
            raise EnvelopeExpired("ENVELOPE_EXPIRED")

        if status == "USAGE_EXHAUSTED":
            raise UsageLimitExceeded("USAGE_LIMIT_EXCEEDED")

        if subject != env.subject:
            raise ScopeEscalation("SUBJECT_MISMATCH")

        if operation != env.operation:
            raise ScopeEscalation("OPERATION_MISMATCH")

        if resource != env.resource:
            raise ScopeEscalation("RESOURCE_MISMATCH")

        if self.RISK_ORDER[risk] > self.RISK_ORDER[env.risk_limit]:
            raise ScopeEscalation("RISK_LIMIT_EXCEEDED")

        for key, requested in resource_request.items():
            limit = env.resource_limits.get(key)

            if limit is None:
                raise ResourceLimitExceeded(
                    "UNDECLARED_RESOURCE:" + key
                )

            if requested > limit:
                raise ResourceLimitExceeded(
                    "RESOURCE_LIMIT_EXCEEDED:" + key
                )

        # Idempotent usage evidence.
        for old in self._usage[envelope_id]:
            if old["usage_id"] == usage_id:
                return deepcopy(old), True

        use = {
            "usage_id": usage_id,
            "subject": subject,
            "operation": operation,
            "resource": resource,
            "risk": risk,
            "resource_request": deepcopy(resource_request),
            "observed_at": now_value,
            "executed": False,
        }

        self._usage[envelope_id].append(use)
        return deepcopy(use), False

    def view(self, envelope_id, *, now_value):
        env = self._records[envelope_id]

        return {
            "envelope": env.material(),
            "envelope_hash": env.envelope_hash,
            "status": self.status(
                envelope_id,
                now_value=now_value,
            ),
            "usage_count": len(self._usage[envelope_id]),
            "usage_history": deepcopy(
                self._usage[envelope_id]
            ),
            "revocation": deepcopy(
                self._revocations.get(envelope_id)
            ),
        }
