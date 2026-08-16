from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class DirectorOutput:
    """Reserved boundary used later by the real SSI starter.

    Director is intentionally not required by the world runtime.
    """

    cycle_id: str
    director_status: str
    priority_decisions: List[Dict[str, Any]] = field(default_factory=list)
    compute_job_requests: List[Dict[str, Any]] = field(default_factory=list)
    world_recommendations: List[Dict[str, Any]] = field(default_factory=list)
    schedule_recommendations: List[Dict[str, Any]] = field(default_factory=list)
    agent_instructions: List[Dict[str, Any]] = field(default_factory=list)
    artifact_requests: List[Dict[str, Any]] = field(default_factory=list)
    continuum_requests: List[Dict[str, Any]] = field(default_factory=list)
    observations_created: List[str] = field(default_factory=list)
    experiences_created: List[str] = field(default_factory=list)
    memory_updates: List[Dict[str, Any]] = field(default_factory=list)
    abstentions: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    event_checkpoint: Optional[int] = None
    created_unix: Optional[int] = None
