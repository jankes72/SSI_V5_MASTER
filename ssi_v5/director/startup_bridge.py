
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ssi_v5.director.contracts import DirectorContract, DirectorContext, DirectorOutput


@dataclass(frozen=True)
class DirectorStartupBridgeStatus:
    status: str
    public_runtime: bool
    starter_owned: bool
    director_output_is_execution: bool
    automatic_execution: bool
    worlds_can_run_without_director: bool


class DirectorStartupBridge:
    """
    Internal bridge used by the real SSI starter.

    It does not replace start_ssi.py and it does not create a second public
    Director runtime. The starter owns invocation order.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def build_context(
        self,
        *,
        checkpoint_event_sequence: int,
        governance_snapshot: Dict[str, Any],
        queue_snapshot: Dict[str, Any],
        lifecycle_snapshot: Dict[str, Any],
        evidence_snapshot: Dict[str, Any],
        world_id: Optional[str] = None,
        domain_id: Optional[str] = None,
    ) -> DirectorContext:
        return DirectorContract.create_context(
            checkpoint_event_sequence=checkpoint_event_sequence,
            governance_snapshot=governance_snapshot,
            queue_snapshot=queue_snapshot,
            lifecycle_snapshot=lifecycle_snapshot,
            evidence_snapshot=evidence_snapshot,
            world_id=world_id,
            domain_id=domain_id,
        )

    def accept_output(self, output: DirectorOutput) -> Dict[str, Any]:
        if not DirectorContract.verify_output(output):
            raise ValueError("DirectorOutput integrity verification failed")
        return {
            "status": "ACCEPTED_AS_RECOMMENDATION",
            "output_id": output.output_id,
            "context_id": output.context_id,
            "decision": output.decision,
            "execution_started": False,
            "priority_resolved": False,
            "lifecycle_changed": False,
        }

    @staticmethod
    def status() -> Dict[str, Any]:
        return {
            "status": "READY",
            "public_runtime": False,
            "starter_owned": True,
            "director_output_is_execution": False,
            "automatic_execution": False,
            "worlds_can_run_without_director": True,
            "start_ssi_remains_primary_entrypoint": True,
            "ssi_v5_run_remains_manual_world_runner": True,
            "director_priority_authority": "RESERVED_FOR_GATE24",
        }
