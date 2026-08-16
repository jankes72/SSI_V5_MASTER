from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ssi_v5.compute.fabric import NODE01_DEFAULT_PROFILE, PRIORITY, ComputeJob, WorkerProfile


CPU_ONLY_JOB_TYPES = {
    "HEALTHCHECK",
    "MODEL_TRAIN_AND_OBSERVE",
    "MODEL_RETRAIN",
    "MAINTENANCE",
    "OUTCOME_OBSERVATION",
    "FEATURE_PREDICTION",
    "LIVE_PREDICTION",
}


@dataclass(frozen=True)
class ResourceRouteDecision:
    status: str
    route: str
    worker_id: Optional[str]
    execution_mode: Optional[str]
    reason: str
    checkpoint_required: bool
    preemption_mode: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "route": self.route,
            "worker_id": self.worker_id,
            "execution_mode": self.execution_mode,
            "reason": self.reason,
            "checkpoint_required": self.checkpoint_required,
            "preemption_mode": self.preemption_mode,
        }


class Node01ResourceRouter:
    """Static resource authority for the current single Node-01 topology.

    This router decides whether a resolved ComputeJob is admissible on Node-01
    and whether it should use the CPU or reserve a future GPU path. It does not
    execute, promote, reprioritize, or mutate governance.
    """

    def __init__(self, profile: WorkerProfile = NODE01_DEFAULT_PROFILE) -> None:
        self.profile = profile
        self.reserved_cpu_cores = int(profile.labels.get("reserved_cpu_cores", 1))
        self.soft_training_ram_mb = int(profile.labels.get("soft_training_ram_mb", profile.ram_mb))

    @property
    def schedulable_cpu_cores(self) -> int:
        return max(1, int(self.profile.cpu_cores) - self.reserved_cpu_cores)

    def route(self, job: ComputeJob) -> ResourceRouteDecision:
        job.validate()
        r = job.resources

        if r.cpu_cores > self.schedulable_cpu_cores:
            return ResourceRouteDecision(
                status="WAITING_RESOURCES",
                route="NONE",
                worker_id=self.profile.worker_id,
                execution_mode=None,
                reason="CPU_REQUEST_EXCEEDS_NODE01_SCHEDULABLE_CAPACITY",
                checkpoint_required=False,
                preemption_mode="NONE",
            )

        if r.ram_mb > self.soft_training_ram_mb:
            return ResourceRouteDecision(
                status="WAITING_RESOURCES",
                route="NONE",
                worker_id=self.profile.worker_id,
                execution_mode=None,
                reason="RAM_REQUEST_EXCEEDS_NODE01_SOFT_LIMIT",
                checkpoint_required=False,
                preemption_mode="NONE",
            )

        long_running = bool(r.expected_seconds is not None and r.expected_seconds >= 300)

        if job.job_type in CPU_ONLY_JOB_TYPES or r.gpu_mode in {"CPU", "NONE"}:
            return ResourceRouteDecision(
                status="ROUTABLE",
                route="NODE01",
                worker_id=self.profile.worker_id,
                execution_mode="CPU",
                reason="CURRENT_JOB_TYPE_ROUTED_TO_CPU",
                checkpoint_required=long_running,
                preemption_mode="COOPERATIVE_CHECKPOINT_ONLY" if long_running else "NONE",
            )

        if r.gpu_mode == "GPU":
            if not self.profile.gpu_name:
                reason = "GPU_REQUIRED_BUT_NODE01_HAS_NO_GPU"
            elif r.vram_mb > int(self.profile.vram_mb):
                reason = "VRAM_REQUEST_EXCEEDS_NODE01_CAPACITY"
            else:
                reason = "GPU_EXECUTION_NOT_ENABLED_FOR_GATE12"
            return ResourceRouteDecision(
                status="WAITING_IMPLEMENTATION" if reason == "GPU_EXECUTION_NOT_ENABLED_FOR_GATE12" else "WAITING_RESOURCES",
                route="NONE",
                worker_id=self.profile.worker_id,
                execution_mode=None,
                reason=reason,
                checkpoint_required=long_running,
                preemption_mode="COOPERATIVE_CHECKPOINT_ONLY" if long_running else "NONE",
            )

        # AUTO is intentionally conservative on GTX 970/Maxwell: current worker
        # jobs are CPU/sklearn, so AUTO falls back to CPU rather than pretending
        # GPU acceleration is available for unsupported runners.
        return ResourceRouteDecision(
            status="ROUTABLE",
            route="NODE01",
            worker_id=self.profile.worker_id,
            execution_mode="CPU",
            reason="AUTO_CPU_FALLBACK_CURRENT_WORKER",
            checkpoint_required=long_running,
            preemption_mode="COOPERATIVE_CHECKPOINT_ONLY" if long_running else "NONE",
        )

    def preemption_decision(self, running: ComputeJob, incoming: ComputeJob) -> Dict[str, Any]:
        running.validate()
        incoming.validate()
        if PRIORITY[incoming.priority_class] <= PRIORITY[running.priority_class]:
            return {
                "action": "KEEP_RUNNING",
                "reason": "INCOMING_PRIORITY_NOT_HIGHER",
                "destructive_preemption_allowed": False,
            }

        route = self.route(running)
        if route.checkpoint_required:
            return {
                "action": "REQUEST_COOPERATIVE_CHECKPOINT",
                "reason": "HIGHER_PRIORITY_JOB_WAITING",
                "destructive_preemption_allowed": False,
            }

        return {
            "action": "DEFER_PREEMPTION",
            "reason": "RUNNING_JOB_HAS_NO_SAFE_CHECKPOINT_CONTRACT",
            "destructive_preemption_allowed": False,
        }


def resource_router_status() -> Dict[str, Any]:
    router = Node01ResourceRouter()
    return {
        "status": "READY",
        "topology": "I7_CONTROLLER__NODE01_SINGLE_WORKER",
        "worker_id": router.profile.worker_id,
        "schedulable_cpu_cores": router.schedulable_cpu_cores,
        "soft_training_ram_mb": router.soft_training_ram_mb,
        "gpu_name": router.profile.gpu_name,
        "gpu_execution": "RESERVED_NOT_ENABLED",
        "auto_gpu_policy": "CPU_FALLBACK_CURRENT_WORKER",
        "checkpoint_policy": "REQUIRED_FOR_EXPECTED_SECONDS_GTE_300",
        "preemption": "COOPERATIVE_CHECKPOINT_ONLY",
        "destructive_preemption_allowed": False,
        "worker_has_authority": False,
    }
