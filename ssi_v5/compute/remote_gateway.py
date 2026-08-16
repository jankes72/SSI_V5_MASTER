from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ssi_v5.compute.fabric import (
    ComputeBackend,
    ResourceRequest,
    build_remote_backend,
)
from ssi_v5.compute.job_envelope import JobEnvelope
from ssi_v5.compute.resource_router import Node01ResourceRouter
from ssi_v5.compute.priority_authority import (
    RootPriorityAuthority,
    WorldPolicyPriorityAuthority,
)


@dataclass(frozen=True)
class SubmissionReceipt:
    envelope_id: str
    job_id: str
    authority_type: str
    authority_id: str
    final_priority_class: str
    transport: str = "SSH_LAN_NODE01"
    resource_route: str = "NODE01"
    execution_mode: str = "CPU"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "job_id": self.job_id,
            "authority_type": self.authority_type,
            "authority_id": self.authority_id,
            "final_priority_class": self.final_priority_class,
            "transport": self.transport,
            "resource_route": self.resource_route,
            "execution_mode": self.execution_mode,
        }


class UnifiedRemoteJobGateway:
    """Authorized submission path from JobEnvelope to the compute backend.

    The gateway owns priority resolution for normal world/system submissions.
    Callers cannot submit a pre-resolved envelope through the normal path and
    cannot bypass authority by supplying a ComputeJob here.
    """

    def __init__(
        self,
        backend: ComputeBackend,
        *,
        world_authority: Optional[WorldPolicyPriorityAuthority] = None,
        root_authority: Optional[RootPriorityAuthority] = None,
        resource_router: Optional[Node01ResourceRouter] = None,
    ) -> None:
        self.backend = backend
        self.world_authority = world_authority or WorldPolicyPriorityAuthority()
        self.root_authority = root_authority or RootPriorityAuthority()
        self.resource_router = resource_router or Node01ResourceRouter()

    @staticmethod
    def _require_unresolved(envelope: JobEnvelope) -> None:
        envelope.validate()
        if envelope.priority_resolution is not None:
            raise ValueError(
                "Normal gateway submission requires an unresolved JobEnvelope; "
                "priority authority is selected inside the gateway"
            )

    def submit(self, envelope: JobEnvelope) -> SubmissionReceipt:
        self._require_unresolved(envelope)
        resolved = self.world_authority.resolve(envelope)
        job = resolved.to_compute_job()
        route = self.resource_router.route(job)
        if route.status != "ROUTABLE":
            raise RuntimeError(f"RESOURCE_ROUTE_BLOCKED:{route.status}:{route.reason}")
        job_id = self.backend.submit(job)
        resolution = resolved.priority_resolution
        assert resolution is not None
        return SubmissionReceipt(
            envelope_id=resolved.envelope_id,
            job_id=job_id,
            authority_type=resolution.authority_type,
            authority_id=resolution.authority_id,
            final_priority_class=resolution.final_priority_class,
            resource_route=route.route,
            execution_mode=str(route.execution_mode),
        )

    def submit_root_override(
        self,
        envelope: JobEnvelope,
        *,
        final_priority_class: str,
        reason: str,
    ) -> SubmissionReceipt:
        self._require_unresolved(envelope)
        resolved = self.root_authority.resolve(
            envelope,
            final_priority_class=final_priority_class,
            reason=reason,
        )
        job = resolved.to_compute_job()
        route = self.resource_router.route(job)
        if route.status != "ROUTABLE":
            raise RuntimeError(f"RESOURCE_ROUTE_BLOCKED:{route.status}:{route.reason}")
        job_id = self.backend.submit(job)
        resolution = resolved.priority_resolution
        assert resolution is not None
        return SubmissionReceipt(
            envelope_id=resolved.envelope_id,
            job_id=job_id,
            authority_type=resolution.authority_type,
            authority_id=resolution.authority_id,
            final_priority_class=resolution.final_priority_class,
            resource_route=route.route,
            execution_mode=str(route.execution_mode),
        )


def build_unified_remote_gateway(
    root: Path,
    *,
    strict_dispatch: bool = False,
) -> UnifiedRemoteJobGateway:
    backend = build_remote_backend(Path(root), strict_dispatch=strict_dispatch)
    return UnifiedRemoteJobGateway(backend)


def node01_healthcheck_envelope() -> JobEnvelope:
    return JobEnvelope.create(
        source="SYSTEM",
        source_id="SSI_V5_GATE11",
        job_type="HEALTHCHECK",
        world_id="WORLD__SYSTEM",
        domain_id="DOMAIN__NODE01_HEALTHCHECK",
        discipline="SYSTEM",
        owner_type="SYSTEM",
        owner_id="SSI_V5_GATE11",
        route_key="NODE01_HEALTHCHECK",
        payload={"message": "SSI_GATE11_UNIFIED_ENVELOPE_HEALTHCHECK"},
        resources=ResourceRequest(
            cpu_cores=1,
            ram_mb=128,
            gpu_mode="NONE",
            vram_mb=0,
            expected_seconds=10,
        ),
        requested_priority_class="P0_SYSTEM_CRITICAL",
    )


def remote_gateway_status() -> Dict[str, Any]:
    return {
        "status": "READY",
        "input_contract": "JobEnvelope",
        "normal_priority_authority": "WORLD_POLICY",
        "root_override": "EXPLICIT_METHOD_ONLY",
        "caller_pre_resolved_submission_allowed": False,
        "direct_compute_job_input_allowed": False,
        "backend": "RemoteQueueComputeBackend",
        "resource_router": "Node01ResourceRouter",
        "transport": "SSH_LAN_NODE01",
        "node_worker_has_authority": False,
        "node_worker_can_promote": False,
        "director_authority": "RESERVED_NOT_ACTIVE",
    }
