from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Optional

from ssi_v5.compute.fabric import ComputeJob, PRIORITY, ResourceRequest

JOB_SOURCES = {"SYSTEM", "WORLD", "DIRECTOR", "AGENT", "CONTINUUM"}
PRIORITY_AUTHORITIES = {"ROOT", "WORLD_POLICY", "DIRECTOR"}
SCHEMA_VERSION = 1


def _now() -> int:
    return int(time.time())


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class PriorityResolution:
    authority_type: str
    authority_id: str
    final_priority_class: str
    reason: str
    resolved_unix: int = field(default_factory=_now)

    def validate(self) -> None:
        if self.authority_type not in PRIORITY_AUTHORITIES:
            raise ValueError(f"Unsupported priority authority: {self.authority_type}")
        if self.final_priority_class not in PRIORITY:
            raise ValueError(f"Unknown final priority class: {self.final_priority_class}")
        if not self.authority_id.strip():
            raise ValueError("authority_id is required")
        if not self.reason.strip():
            raise ValueError("priority resolution reason is required")


@dataclass(frozen=True)
class JobEnvelope:
    envelope_id: str
    source: str
    source_id: str
    job_type: str
    world_id: str
    domain_id: str
    discipline: str
    owner_type: str
    owner_id: str
    route_key: str
    payload: Dict[str, Any]
    resources: ResourceRequest
    requested_priority_class: Optional[str] = None
    priority_resolution: Optional[PriorityResolution] = None
    network_id: Optional[str] = None
    generation: Optional[int] = None
    artifact_inputs: List[str] = field(default_factory=list)
    artifact_outputs: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    governance_refs: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    idempotency_key: Optional[str] = None
    deadline_unix: Optional[int] = None
    created_unix: int = field(default_factory=_now)
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def create(cls, **kwargs: Any) -> "JobEnvelope":
        kwargs.setdefault("envelope_id", "JOBENV__" + uuid.uuid4().hex.upper())
        env = cls(**kwargs)
        env.validate()
        return env

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported job envelope schema: {self.schema_version}")
        if self.source not in JOB_SOURCES:
            raise ValueError(f"Unsupported job source: {self.source}")
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        if not self.job_type.strip():
            raise ValueError("job_type is required")
        for name, value in (
            ("world_id", self.world_id),
            ("domain_id", self.domain_id),
            ("discipline", self.discipline),
            ("owner_type", self.owner_type),
            ("owner_id", self.owner_id),
            ("route_key", self.route_key),
        ):
            if not str(value).strip():
                raise ValueError(f"{name} is required")
        if self.requested_priority_class is not None and self.requested_priority_class not in PRIORITY:
            raise ValueError(f"Unknown requested priority class: {self.requested_priority_class}")
        self.resources.validate()
        if self.priority_resolution is not None:
            self.priority_resolution.validate()

    @property
    def is_priority_resolved(self) -> bool:
        return self.priority_resolution is not None

    @property
    def final_priority_class(self) -> Optional[str]:
        return None if self.priority_resolution is None else self.priority_resolution.final_priority_class

    @property
    def content_hash(self) -> str:
        payload = self.to_dict(include_hash=False)
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> Dict[str, Any]:
        result = asdict(self)
        if include_hash:
            result["content_hash"] = hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest()
        return result

    def resolve_priority(
        self,
        *,
        authority_type: str,
        authority_id: str,
        final_priority_class: str,
        reason: str,
    ) -> "JobEnvelope":
        resolution = PriorityResolution(
            authority_type=authority_type,
            authority_id=authority_id,
            final_priority_class=final_priority_class,
            reason=reason,
        )
        resolution.validate()
        resolved = replace(self, priority_resolution=resolution)
        resolved.validate()
        return resolved

    def to_compute_job(self) -> ComputeJob:
        if self.priority_resolution is None:
            raise ValueError("Priority must be resolved by an authority before queue submission")
        return ComputeJob.create(
            job_type=self.job_type,
            priority_class=self.priority_resolution.final_priority_class,
            world_key=self.world_id,
            domain_key=self.domain_id,
            discipline=self.discipline,
            owner_type=self.owner_type,
            owner_id=self.owner_id,
            route_key=self.route_key,
            network_id=self.network_id,
            generation=self.generation,
            payload={
                **self.payload,
                "job_envelope": {
                    "envelope_id": self.envelope_id,
                    "source": self.source,
                    "source_id": self.source_id,
                    "requested_priority_class": self.requested_priority_class,
                    "priority_resolution": asdict(self.priority_resolution),
                    "governance_refs": list(self.governance_refs),
                    "evidence_refs": list(self.evidence_refs),
                    "content_hash": self.content_hash,
                },
            },
            resources=self.resources,
            artifact_inputs=list(self.artifact_inputs),
            artifact_outputs=list(self.artifact_outputs),
            dependencies=list(self.dependencies),
            deadline_unix=self.deadline_unix,
            idempotency_key=self.idempotency_key,
        )


def job_envelope_status() -> Dict[str, Any]:
    return {
        "status": "READY",
        "schema_version": SCHEMA_VERSION,
        "sources": sorted(JOB_SOURCES),
        "priority_authorities": sorted(PRIORITY_AUTHORITIES),
        "priority_classes": list(PRIORITY),
        "caller_priority_is_authoritative": False,
        "queue_submission_requires_priority_resolution": True,
        "director_output_is_execution": False,
        "node_worker_has_authority": False,
    }
