
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


ALLOWED_AUTHORITIES = {"ROOT", "WORLD_POLICY", "DIRECTOR"}
PRIORITY_LEVELS = {
    "P0_CRITICAL": 0,
    "P1_LIVE": 1,
    "P2_OUTCOME": 2,
    "P3_FEATURE": 3,
    "P4_RETRAIN": 4,
    "P5_AGENT": 5,
    "P6_TRAIN": 6,
    "P7_FEATURE_LEARNING": 7,
    "P8_DISCOVERY": 8,
    "P9_MAINTENANCE": 9,
}


@dataclass(frozen=True)
class PriorityResolution:
    requested_priority: str
    final_priority: str
    authority_type: str
    authority_id: str
    root_override_applied: bool
    caller_priority_authoritative: bool
    execution_started: bool


class DirectorPriorityAuthority:
    """
    Gate 24 Director priority authority.

    Director may resolve final priority only through this authority boundary.
    Priority resolution is not execution and never dispatches a job.
    ROOT override remains highest.
    """

    def __init__(self, *, director_authority_id: str = "DIRECTOR_MAIN"):
        self.director_authority_id = director_authority_id

    @staticmethod
    def _validate(priority: str) -> str:
        p = str(priority).upper()
        if p not in PRIORITY_LEVELS:
            raise ValueError(f"unsupported priority: {priority}")
        return p

    def resolve(
        self,
        *,
        requested_priority: str,
        director_priority: Optional[str] = None,
        caller_source: str = "WORLD",
        root_override_priority: Optional[str] = None,
        root_authority_id: Optional[str] = None,
    ) -> PriorityResolution:
        requested = self._validate(requested_priority)

        # Explicit ROOT override is always highest authority.
        if root_override_priority is not None:
            if not root_authority_id:
                raise ValueError("root_authority_id required for ROOT override")
            final_priority = self._validate(root_override_priority)
            return PriorityResolution(
                requested_priority=requested,
                final_priority=final_priority,
                authority_type="ROOT",
                authority_id=root_authority_id,
                root_override_applied=True,
                caller_priority_authoritative=False,
                execution_started=False,
            )

        if director_priority is None:
            raise ValueError("director_priority required when ROOT override is absent")

        final_priority = self._validate(director_priority)
        return PriorityResolution(
            requested_priority=requested,
            final_priority=final_priority,
            authority_type="DIRECTOR",
            authority_id=self.director_authority_id,
            root_override_applied=False,
            caller_priority_authoritative=False,
            execution_started=False,
        )

    @staticmethod
    def verify_resolution(resolution: PriorityResolution) -> Dict[str, Any]:
        valid = (
            resolution.authority_type in {"ROOT", "DIRECTOR"}
            and resolution.final_priority in PRIORITY_LEVELS
            and resolution.requested_priority in PRIORITY_LEVELS
            and resolution.execution_started is False
            and resolution.caller_priority_authoritative is False
        )
        if resolution.root_override_applied and resolution.authority_type != "ROOT":
            valid = False
        return {
            "status": "VALID" if valid else "INVALID",
            "authority_type": resolution.authority_type,
            "final_priority": resolution.final_priority,
            "execution_started": resolution.execution_started,
        }

    @staticmethod
    def status() -> Dict[str, Any]:
        return {
            "status": "READY",
            "active_runtime_authority": "DIRECTOR",
            "director_priority_authority": True,
            "caller_priority_is_authoritative": False,
            "root_override": "AVAILABLE_EXPLICIT_ONLY",
            "root_override_highest": True,
            "self_escalation": False,
            "priority_resolution_is_execution": False,
            "direct_compute_dispatch": False,
            "world_policy_fallback": True,
        }
