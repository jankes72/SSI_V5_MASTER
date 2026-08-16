from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from ssi_v5.compute.fabric import PRIORITY
from ssi_v5.compute.job_envelope import JobEnvelope


# Deterministic baseline used while Director is not the active priority authority.
# Callers may request a priority, but this map is authoritative for normal world jobs.
JOB_TYPE_PRIORITY: Dict[str, str] = {
    "HEALTHCHECK": "P9_MAINTENANCE",
    "LIVE_PREDICTION": "P1_LIVE_PREDICTION",
    "OUTCOME_OBSERVATION": "P2_OUTCOME_OBSERVATION",
    "FEATURE_PREDICTION": "P3_FEATURE_PREDICTION",
    "MODEL_RETRAIN": "P4_REQUIRED_RETRAIN",
    "MODEL_TRAIN_AND_OBSERVE": "P6_CHALLENGER_TRAINING",
    "AGENT_ACTIVE_LAB": "P5_AGENT_ACTIVE_LAB",
    "CHALLENGER_TRAINING": "P6_CHALLENGER_TRAINING",
    "FEATURE_MICRONET_LEARNING": "P7_FEATURE_MICRONET_LEARNING",
    "DISCOVERY_EXPERIMENT": "P8_DISCOVERY_EXPERIMENT",
    "MAINTENANCE": "P9_MAINTENANCE",
}

SOURCE_FALLBACK_PRIORITY: Dict[str, str] = {
    "SYSTEM": "P9_MAINTENANCE",
    "WORLD": "P6_CHALLENGER_TRAINING",
    "DIRECTOR": "P6_CHALLENGER_TRAINING",
    "AGENT": "P5_AGENT_ACTIVE_LAB",
    "CONTINUUM": "P8_DISCOVERY_EXPERIMENT",
}


@dataclass(frozen=True)
class PriorityDecision:
    authority_type: str
    authority_id: str
    final_priority_class: str
    reason: str

    def validate(self) -> None:
        if self.authority_type not in {"ROOT", "WORLD_POLICY", "DIRECTOR"}:
            raise ValueError(f"Unsupported authority type: {self.authority_type}")
        if not self.authority_id.strip():
            raise ValueError("authority_id is required")
        if self.final_priority_class not in PRIORITY:
            raise ValueError(f"Unknown priority class: {self.final_priority_class}")
        if not self.reason.strip():
            raise ValueError("reason is required")


class WorldPolicyPriorityAuthority:
    """Deterministic priority authority used when Director is not active.

    Requested priority is evidence only and never self-authorizes escalation.
    """

    authority_type = "WORLD_POLICY"

    def __init__(self, *, policy_ids: Optional[Mapping[str, str]] = None) -> None:
        self.policy_ids = dict(policy_ids or {
            "WORLD__SPORT": "WORLD_POLICY__SPORT__V1",
            "WORLD__FOREX": "WORLD_POLICY__FOREX__V1",
            "WORLD__CAPITAL": "WORLD_POLICY__CAPITAL__V1",
            "WORLD__SYSTEM": "ROOT_CONSTITUTION__V1",
        })

    def decide(self, envelope: JobEnvelope) -> PriorityDecision:
        envelope.validate()
        if envelope.priority_resolution is not None:
            raise ValueError("Envelope priority is already resolved")

        authority_id = self.policy_ids.get(envelope.world_id)
        if not authority_id:
            raise ValueError(f"No world priority policy registered for: {envelope.world_id}")

        final_priority = JOB_TYPE_PRIORITY.get(
            envelope.job_type,
            SOURCE_FALLBACK_PRIORITY[envelope.source],
        )

        requested = envelope.requested_priority_class or "NONE"
        reason = (
            f"WORLD_POLICY deterministic mapping: job_type={envelope.job_type}; "
            f"source={envelope.source}; requested={requested}; final={final_priority}; "
            "caller request is non-authoritative"
        )
        decision = PriorityDecision(
            authority_type=self.authority_type,
            authority_id=authority_id,
            final_priority_class=final_priority,
            reason=reason,
        )
        decision.validate()
        return decision

    def resolve(self, envelope: JobEnvelope) -> JobEnvelope:
        decision = self.decide(envelope)
        return envelope.resolve_priority(
            authority_type=decision.authority_type,
            authority_id=decision.authority_id,
            final_priority_class=decision.final_priority_class,
            reason=decision.reason,
        )


class RootPriorityAuthority:
    """Explicit Programmer Root override. This is never inferred from caller input."""

    authority_type = "ROOT"
    authority_id = "ROOT_CONSTITUTION__V1"

    def resolve(self, envelope: JobEnvelope, *, final_priority_class: str, reason: str) -> JobEnvelope:
        if envelope.priority_resolution is not None:
            raise ValueError("Envelope priority is already resolved")
        if final_priority_class not in PRIORITY:
            raise ValueError(f"Unknown priority class: {final_priority_class}")
        if not reason.strip():
            raise ValueError("Root override reason is required")
        return envelope.resolve_priority(
            authority_type=self.authority_type,
            authority_id=self.authority_id,
            final_priority_class=final_priority_class,
            reason=f"ROOT_OVERRIDE: {reason}",
        )


def priority_authority_status() -> Dict[str, Any]:
    return {
        "status": "READY",
        "active_runtime_authority": "WORLD_POLICY",
        "root_override": "AVAILABLE_EXPLICIT_ONLY",
        "director_authority": "RESERVED_NOT_ACTIVE",
        "caller_priority_is_authoritative": False,
        "self_escalation_allowed": False,
        "priority_classes": list(PRIORITY),
        "job_type_mapping": dict(JOB_TYPE_PRIORITY),
        "source_fallback_mapping": dict(SOURCE_FALLBACK_PRIORITY),
    }
