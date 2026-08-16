
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from typing import Any, Dict, List, Optional
import json
import time
import uuid


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


ALLOWED_DECISIONS = {
    "NO_ACTION",
    "REQUEST_JOB",
    "REQUEST_RETRAIN",
    "REQUEST_PROMOTION_REVIEW",
    "REQUEST_SUSPENSION_REVIEW",
    "REQUEST_QUARANTINE_REVIEW",
    "REQUEST_PRIORITY_REVIEW",
}


@dataclass(frozen=True)
class DirectorContext:
    context_id: str
    checkpoint_event_sequence: int
    world_id: Optional[str]
    domain_id: Optional[str]
    governance_snapshot: Dict[str, Any]
    queue_snapshot: Dict[str, Any]
    lifecycle_snapshot: Dict[str, Any]
    evidence_snapshot: Dict[str, Any]
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "DirectorContext":
        body = asdict(self)
        body["content_hash"] = ""
        return DirectorContext(**{**body, "content_hash": stable_hash(body)})


@dataclass(frozen=True)
class DirectorOutput:
    output_id: str
    context_id: str
    decision: str
    rationale: str
    requested_action: Dict[str, Any]
    confidence: Optional[float]
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "DirectorOutput":
        body = asdict(self)
        body["content_hash"] = ""
        return DirectorOutput(**{**body, "content_hash": stable_hash(body)})


class DirectorContract:
    """
    Pure data contract between SSI runtime and Director.

    DirectorOutput is NOT execution and cannot directly:
      * enqueue a ComputeJob
      * alter lifecycle
      * promote/suspend/quarantine a generation
      * become final PriorityAuthority
    """

    @staticmethod
    def create_context(
        *,
        checkpoint_event_sequence: int,
        governance_snapshot: Dict[str, Any],
        queue_snapshot: Dict[str, Any],
        lifecycle_snapshot: Dict[str, Any],
        evidence_snapshot: Dict[str, Any],
        world_id: Optional[str] = None,
        domain_id: Optional[str] = None,
        context_id: Optional[str] = None,
    ) -> DirectorContext:
        if checkpoint_event_sequence < 0:
            raise ValueError("checkpoint_event_sequence must be >= 0")
        if not isinstance(governance_snapshot, dict):
            raise ValueError("governance_snapshot must be a dict")
        if not isinstance(queue_snapshot, dict):
            raise ValueError("queue_snapshot must be a dict")
        if not isinstance(lifecycle_snapshot, dict):
            raise ValueError("lifecycle_snapshot must be a dict")
        if not isinstance(evidence_snapshot, dict):
            raise ValueError("evidence_snapshot must be a dict")

        return DirectorContext(
            context_id=context_id or f"DCTX__{uuid.uuid4().hex.upper()}",
            checkpoint_event_sequence=int(checkpoint_event_sequence),
            world_id=world_id,
            domain_id=domain_id,
            governance_snapshot=dict(governance_snapshot),
            queue_snapshot=dict(queue_snapshot),
            lifecycle_snapshot=dict(lifecycle_snapshot),
            evidence_snapshot=dict(evidence_snapshot),
            created_unix=int(time.time()),
        ).with_hash()

    @staticmethod
    def create_output(
        *,
        context: DirectorContext,
        decision: str,
        rationale: str,
        requested_action: Optional[Dict[str, Any]] = None,
        confidence: Optional[float] = None,
        output_id: Optional[str] = None,
    ) -> DirectorOutput:
        decision = str(decision).upper()
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"Unsupported director decision: {decision}")
        if not rationale:
            raise ValueError("rationale is required")
        if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
            raise ValueError("confidence must be within [0, 1]")

        return DirectorOutput(
            output_id=output_id or f"DOUT__{uuid.uuid4().hex.upper()}",
            context_id=context.context_id,
            decision=decision,
            rationale=str(rationale),
            requested_action=dict(requested_action or {}),
            confidence=None if confidence is None else float(confidence),
            created_unix=int(time.time()),
        ).with_hash()

    @staticmethod
    def verify_context(context: DirectorContext) -> bool:
        body = asdict(context)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        return stable_hash(body) == actual

    @staticmethod
    def verify_output(output: DirectorOutput) -> bool:
        body = asdict(output)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        return stable_hash(body) == actual

    @staticmethod
    def status() -> Dict[str, Any]:
        return {
            "status": "READY",
            "director_output_is_execution": False,
            "director_can_enqueue_compute_job_directly": False,
            "director_can_mutate_lifecycle_directly": False,
            "director_can_promote_directly": False,
            "director_priority_authority": "RESERVED_FOR_GATE24",
            "context_is_immutable": True,
            "output_is_immutable": True,
            "checkpoint_event_sequence_required": True,
            "allowed_decisions": sorted(ALLOWED_DECISIONS),
        }
