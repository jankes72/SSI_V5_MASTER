
from dataclasses import dataclass

COMMAND_TYPES = {
    "SYSTEM_BUILD_DIRECTIVE",
    "SYSTEM_CHANGE_DIRECTIVE",
    "DEPLOYMENT_DIRECTIVE",
    "ANALYSIS_REQUEST",
    "INFORMATION_REQUEST",
    "TEST_DIRECTIVE",
    "STRATEGIC_GOAL",
    "PROJECT_DIRECTIVE",
    "DATA_REQUEST",
    "AGENT_OR_LAB_QUERY",
    "APPROVAL",
    "PERMISSION_GRANT",
    "PERMISSION_REVOCATION",
    "PAUSE",
    "RESUME",
    "CANCEL",
    "EMERGENCY_STOP",
    "CONSTITUTIONAL_AMENDMENT",
}

@dataclass(frozen=True)
class ValidationResult:
    status: str
    reason_code: str
    evidence: tuple
    normalized_record: dict | None

class RootCommandValidator:
    def validate(
        self,
        command,
        *,
        expected_root_identity_id,
        expected_session_id,
        authorized=True,
    ):
        required = {
            "command_id",
            "root_identity_id",
            "session_id",
            "original_content",
            "command_type",
            "priority",
            "risk_class",
            "required_approval",
            "ambiguity",
        }

        missing = sorted(required - set(command))

        if missing:
            return ValidationResult(
                "REJECTED",
                "MISSING_REQUIRED_FIELD",
                tuple(missing),
                None,
            )

        if command["root_identity_id"] != expected_root_identity_id:
            return ValidationResult(
                "REJECTED",
                "IDENTITY_BINDING_FAILED",
                ("root_identity_id",),
                None,
            )

        if command["session_id"] != expected_session_id:
            return ValidationResult(
                "REJECTED",
                "SESSION_BINDING_FAILED",
                ("session_id",),
                None,
            )

        if not authorized:
            return ValidationResult(
                "REJECTED",
                "AUTHORIZATION_FAILED",
                ("authority",),
                None,
            )

        if command["command_type"] not in COMMAND_TYPES:
            return ValidationResult(
                "REJECTED",
                "UNKNOWN_COMMAND_TYPE",
                ("command_type",),
                None,
            )

        if command["ambiguity"] == "MATERIAL":
            return ValidationResult(
                "CLARIFICATION_REQUIRED",
                "MATERIAL_AMBIGUITY",
                ("ambiguity",),
                None,
            )

        normalized = {
            "schema_version": 1,
            "source_command_id": command["command_id"],
            "normalized_content": command["original_content"].strip(),
            "derived_record": True,
        }

        if command["required_approval"]:
            return ValidationResult(
                "APPROVAL_REQUIRED",
                "ROOT_APPROVAL_REQUIRED",
                ("required_approval",),
                normalized,
            )

        return ValidationResult(
            "VALIDATED",
            "COMMAND_VALID",
            (
                "schema",
                "identity",
                "session",
                "authorization",
                "taxonomy",
            ),
            normalized,
        )
