from __future__ import annotations

from dataclasses import dataclass
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

from ssi_v5.artifacts.registry import ArtifactRegistry
from ssi_v5.events.journal import WorldEventJournal
from ssi_v5.governance import build_default_registry
from ssi_v5.runtime.incremental import SourceCheckpoint
from ssi_v5.worlds.legacy_factory import StateStore, run_current_pipeline, write_canonical_manifests
from ssi_v5.worlds.registry import LEGACY_DOMAIN_ALIASES, validate_registry

WORLD_SELECTOR = {
    "SPORT": ("WORLD__SPORT",),
    "FOREX": ("WORLD__FOREX",),
    "CAPITAL": ("WORLD__CAPITAL",),
    "ALL": ("WORLD__SPORT", "WORLD__FOREX", "WORLD__CAPITAL"),
}


def _canonical_world_for_legacy(domain_key: str) -> Optional[Tuple[str, str]]:
    alias = LEGACY_DOMAIN_ALIASES.get(domain_key)
    if alias is None:
        return None
    return alias.world_id, alias.domain_id


def _governance_refs(registry: Any, world_id: str) -> Tuple[str, ...]:
    return tuple(doc.governance_id for doc in registry.chain_for(world_id))


def _queue_job_total(summary: Mapping[str, Any]) -> int:
    return sum(int(row.get("n", 0)) for row in summary.get("jobs", []))


@dataclass(frozen=True)
class ManualCycleDependencies:
    node_check: Callable[[], Dict[str, Any]]
    collect: Callable[[], Dict[str, Any]]
    dispatch: Callable[[], Dict[str, Any]]
    queue_summary: Callable[[], Dict[str, Any]]
    pipeline: Callable[[Path, Path, Path, StateStore], Dict[str, Any]] = run_current_pipeline


class ManualWorldCycle:
    """Gate 8 orchestration layer. Director-independent, policy-bound and auditable."""

    def __init__(self, root: Path, sports_root: Path, data_root: Path, deps: ManualCycleDependencies):
        self.root = Path(root)
        self.sports_root = Path(sports_root)
        self.data_root = Path(data_root)
        self.deps = deps
        self.governance = build_default_registry()
        self.journal = WorldEventJournal(self.root / "events" / "world_event_journal.sqlite3")
        self.artifacts = ArtifactRegistry(self.root)

    def _append_all(self, worlds: Iterable[str], cycle_id: str, event_type: str, **kwargs: Any) -> None:
        for world_id in worlds:
            self.journal.append(world_id=world_id, cycle_id=cycle_id, event_type=event_type, **kwargs)

    def run(self, selector: str = "ALL") -> Dict[str, Any]:
        selector = str(selector).upper()
        if selector not in WORLD_SELECTOR:
            raise ValueError(f"Unsupported world selector: {selector}")
        worlds = WORLD_SELECTOR[selector]
        if selector != "ALL":
            raise ValueError(
                "Gate 8 executable legacy pipeline is still all-worlds. "
                "Use --world ALL until world-isolated ingestion is introduced."
            )

        self.root.mkdir(parents=True, exist_ok=True)
        validate_registry()
        governance_status = self.governance.summary()
        if governance_status["status"] != "VALID":
            raise RuntimeError("Governance registry is not valid")

        cycle_id = f"CYCLE__{int(time.time())}__{uuid4().hex[:12].upper()}"
        created_unix = int(time.time())

        invocation_refs: Dict[str, str] = {}
        for world_id in worlds:
            inv = self.artifacts.register_json(
                {
                    "cycle_id": cycle_id,
                    "selector": selector,
                    "world_id": world_id,
                    "director_required": False,
                    "created_unix": created_unix,
                },
                artifact_kind="RUNTIME_INVOCATION_ARTIFACT",
                world_id=world_id,
                governance_refs=_governance_refs(self.governance, world_id),
                metadata={"gate": 8, "entrypoint": "SSI_V5_RUN.py", "manual": True},
            )
            invocation_refs[world_id] = inv.artifact_id
            self.journal.append(
                world_id=world_id,
                cycle_id=cycle_id,
                event_type="WORLD_CYCLE_STARTED",
                payload={"selector": selector, "director_required": False},
                artifact_refs=[inv.artifact_id],
            )
            self.journal.append(
                world_id=world_id,
                cycle_id=cycle_id,
                event_type="GOVERNANCE_VALIDATED",
                payload={"governance_refs": list(_governance_refs(self.governance, world_id))},
            )

        node = self.deps.node_check()
        collection = self.deps.collect()
        before = self.deps.queue_summary()
        before_count = _queue_job_total(before)

        write_canonical_manifests(self.root)
        canonical_db = self.root / "canonical_worlds.sqlite3"
        canonical_state_exists = canonical_db.is_file() and canonical_db.stat().st_size > 0
        checkpoint = SourceCheckpoint(self.root)
        incremental = checkpoint.decide(
            self.sports_root, self.data_root, canonical_state_exists=canonical_state_exists
        )
        db = StateStore(canonical_db)
        try:
            if incremental.should_ingest:
                pipeline = self.deps.pipeline(self.root, self.sports_root, self.data_root, db)
                checkpoint.save(incremental.current, cycle_id=cycle_id, mode="AFTER_INGESTION")
            else:
                pipeline = {
                    "status": incremental.action,
                    "reason": incremental.reason,
                    "ingestion_skipped": True,
                    "canonical_state": db.ingestion_summary(),
                    "compute_queue": self.deps.queue_summary(),
                }
                if incremental.action == "BOOTSTRAP_CHECKPOINT":
                    checkpoint.save(incremental.current, cycle_id=cycle_id, mode="BOOTSTRAP_EXISTING_STATE")
        finally:
            conn = getattr(db, "conn", None)
            if conn is not None:
                conn.close()

        sports = pipeline.get("sports_ingestion", {}) if not pipeline.get("ingestion_skipped") else {}
        for legacy_domain, info in sports.get("details", {}).items():
            mapped = _canonical_world_for_legacy(legacy_domain)
            if not mapped:
                continue
            world_id, domain_id = mapped
            self.journal.append(
                world_id=world_id, domain_id=domain_id, cycle_id=cycle_id,
                event_type="RAW_EVIDENCE_INGESTED",
                payload={"legacy_domain": legacy_domain, "summary": info},
            )

        market = pipeline.get("market_capital_ingestion", {}) if not pipeline.get("ingestion_skipped") else {}
        for legacy_domain, info in market.items():
            mapped = _canonical_world_for_legacy(legacy_domain)
            if not mapped:
                continue
            world_id, domain_id = mapped
            self.journal.append(
                world_id=world_id, domain_id=domain_id, cycle_id=cycle_id,
                event_type="RAW_EVIDENCE_INGESTED",
                payload={"legacy_domain": legacy_domain, "summary": info},
            )
            inserted = int(info.get("experiences_inserted", 0) or 0)
            if inserted:
                self.journal.append(
                    world_id=world_id, domain_id=domain_id, cycle_id=cycle_id,
                    event_type="EXPERIENCE_CREATED",
                    payload={"legacy_domain": legacy_domain, "count": inserted},
                )

        level1 = pipeline.get("level1_gate_all_worlds", {}) if not pipeline.get("ingestion_skipped") else {}
        for legacy_domain, result in level1.items():
            mapped = _canonical_world_for_legacy(legacy_domain)
            if not mapped:
                continue
            world_id, domain_id = mapped
            self.journal.append(
                world_id=world_id, domain_id=domain_id, cycle_id=cycle_id,
                event_type="TRAINING_NEED_CREATED",
                payload={"legacy_domain": legacy_domain, "gate_result": result},
            )

        after_pipeline = self.deps.queue_summary()
        after_pipeline_count = _queue_job_total(after_pipeline)
        new_jobs = max(0, after_pipeline_count - before_count)
        if new_jobs:
            self._append_all(
                worlds, cycle_id, "COMPUTE_JOB_CREATED",
                payload={"new_job_count": new_jobs, "queue_before": before_count, "queue_after": after_pipeline_count},
            )

        dispatch = self.deps.dispatch()
        dispatched = int(dispatch.get("dispatched", 0) or 0)
        if dispatched:
            self._append_all(worlds, cycle_id, "COMPUTE_JOB_DISPATCHED", payload={"dispatched": dispatched})

        collected_count = int(collection.get("collected", 0) or collection.get("results_collected", 0) or 0)
        if collected_count:
            self._append_all(worlds, cycle_id, "COMPUTE_JOB_RESULT_RECEIVED", payload={"collected": collected_count})

        artifact_integrity = self.artifacts.verify_integrity()
        event_integrity_before_complete = self.journal.verify_integrity()
        final_queue = self.deps.queue_summary()

        for world_id in worlds:
            self.journal.append(
                world_id=world_id,
                cycle_id=cycle_id,
                event_type="WORLD_CYCLE_COMPLETED",
                payload={
                    "node_ok": bool(node.get("ok", False)),
                    "artifact_integrity": artifact_integrity["status"],
                    "event_integrity_before_complete": event_integrity_before_complete["status"],
                },
                artifact_refs=[invocation_refs[world_id]],
            )

        event_integrity = self.journal.verify_integrity()
        status = "RUN_COMPLETED" if artifact_integrity["status"] == "VALID" and event_integrity["status"] == "VALID" else "RUN_COMPLETED_WITH_INTEGRITY_ERROR"
        return {
            "status": status,
            "cycle_id": cycle_id,
            "world_selector": selector,
            "worlds": list(worlds),
            "director_runtime_required": False,
            "invocation_artifacts": invocation_refs,
            "governance": governance_status,
            "node01": node,
            "collection_before": collection,
            "incremental": {
                "action": incremental.action,
                "reason": incremental.reason,
                "fingerprint": incremental.current.get("fingerprint"),
                "file_count": incremental.current.get("file_count"),
            },
            "pipeline": pipeline,
            "dispatch": dispatch,
            "queue_after": final_queue,
            "artifact_integrity": artifact_integrity,
            "event_integrity": event_integrity,
        }
