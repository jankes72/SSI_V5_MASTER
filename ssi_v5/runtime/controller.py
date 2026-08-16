from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from ssi_v5.freeze.acceptance import CoreAcceptanceRegistry
from ssi_v5.scaling.routing import WorkerRoutingRegistry
from ssi_v5.recovery.reconciliation import StateReconciler
from ssi_v5.recovery.registry import RecoveryRegistry
from ssi_v5.continuum.development import ControlledDevelopmentRegistry
from ssi_v5.continuum.contract import ContinuumEngineeringContract
from ssi_v5.iskra.proposal_boundary import IskraProposalBoundary
from ssi_v5.iskra.discovery import IskraDiscoveryEnvironment
from ssi_v5.agents.reasoning import AgentReasoningRegistry
from ssi_v5.agents.observations import AgentObservationRegistry
from ssi_v5.agents.workspace import AgentWorkspace
from ssi_v5.agents.proposals import AgentProposalRegistry
from ssi_v5.agents.registry import AgentRegistry
from ssi_v5.director.priority_authority import DirectorPriorityAuthority
from ssi_v5.director.catchup import DirectorCatchup
from ssi_v5.director.contracts import DirectorContract
from ssi_v5.replay.engine import ReplayRebuildEngine
from ssi_v5.champion.registry import ChampionChallengerRegistry
from ssi_v5.behavior.registry import BehaviorHealthFeatureRegistry
from typing import Any, Dict

from ssi_v5.compute.job_envelope import job_envelope_status
from ssi_v5.compute.priority_authority import priority_authority_status
from ssi_v5.compute.resource_router import resource_router_status
from ssi_v5.compute.result_verifier import result_verifier_status
from ssi_v5.lifecycle import generation_lifecycle_status
from ssi_v5.predictors import predictor_artifact_status
from ssi_v5.predictions import prediction_registry_status
from ssi_v5.outcomes import outcome_evaluation_status
from ssi_v5.compute.remote_gateway import (
    build_unified_remote_gateway,
    node01_healthcheck_envelope,
    remote_gateway_status,
)
from ssi_v5.compute.fabric import (
    NODE01_DEFAULT_CONNECTION,
    SshNodeTransport,
    bootstrap_default_store,
    collect_remote_results,
    dispatch_pending,
    sync_node01_worker_status,
)
from ssi_v5.worlds.legacy_factory import (
    DEFAULT_OBSERVATION_FRACTION,
    DEFAULT_RETRAIN_GROWTH,
    DEFAULT_TRAIN_FRACTION,
    DOMAIN_REGISTRY as LEGACY_EXECUTABLE_DOMAIN_REGISTRY,
    SCHEMA_VERSION,
    StateStore,
    run_current_pipeline,
    write_canonical_manifests,
)
from ssi_v5.governance import governance_status
from ssi_v5.events import event_journal_status
from ssi_v5.artifacts import artifact_registry_status
from ssi_v5.teacher import teacher_status
from ssi_v5.runtime.manual_cycle import ManualCycleDependencies, ManualWorldCycle
from ssi_v5.worlds.registry import (
    CANONICAL_WORLD_REGISTRY,
    LEGACY_DOMAIN_ALIASES,
    canonical_namespace,
    validate_registry,
)

WORLD_RUNNER_NAME = "SSI_V5_RUN.py"
DIRECTOR_RUNTIME_REQUIRED = False


def canonical_root(value: str) -> Path:
    return Path(value).expanduser().resolve()


def namespace_status() -> Dict[str, Any]:
    validate_registry()
    return {
        "canonical_worlds": {k: list(v) for k, v in CANONICAL_WORLD_REGISTRY.items()},
        "legacy_aliases": {
            key: {
                "world_id": value.world_id,
                "domain_id": value.domain_id,
                "status": value.status,
                "notes": value.notes,
            }
            for key, value in sorted(LEGACY_DOMAIN_ALIASES.items())
        },
    }


def project_status(root: Path, *, check_node: bool = True) -> Dict[str, Any]:
    queue = bootstrap_default_store(root)
    canonical_db = root / "canonical_worlds.sqlite3"
    persisted = None
    if canonical_db.is_file():
        persisted = StateStore(canonical_db).ingestion_summary()

    if check_node:
        transport = SshNodeTransport(NODE01_DEFAULT_CONNECTION)
        node01 = transport.check_connection()
        if node01.get("ok"):
            node01["worker_sync"] = sync_node01_worker_status(root)
    else:
        node01 = {"status": "NOT_CHECKED", "reason": "OFFLINE_STATUS_REQUESTED"}

    return {
        "status": "READY",
        "entrypoint": WORLD_RUNNER_NAME,
        "director_runtime_required": DIRECTOR_RUNTIME_REQUIRED,
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "namespace": namespace_status(),
        "legacy_executable_domains": sorted(LEGACY_EXECUTABLE_DOMAIN_REGISTRY),
        "policies": {
            "train_fraction": DEFAULT_TRAIN_FRACTION,
            "observation_fraction": DEFAULT_OBSERVATION_FRACTION,
            "retrain_growth": DEFAULT_RETRAIN_GROWTH,
        },
        "canonical_state": persisted,
        "compute": queue.summary(),
        "node01": node01,
    }


def run_world_cycle(root: Path, sports_root: Path, data_root: Path) -> Dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    validate_registry()
    write_canonical_manifests(root)
    db = StateStore(root / "canonical_worlds.sqlite3")
    pipeline = run_current_pipeline(root, sports_root, data_root, db)
    return {
        "status": "RUN_COMPLETED",
        "entrypoint": WORLD_RUNNER_NAME,
        "director_runtime_required": DIRECTOR_RUNTIME_REQUIRED,
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "namespace": namespace_status(),
        "pipeline": pipeline,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SSI V5 manual world runtime (Director-independent)")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=("run", "status", "namespace", "governance", "events", "artifacts", "teacher", "job-envelope", "priority-authority", "remote-gateway", "resource-router", "result-verifier", "behavior-health-features", "champion-challenger", "replay-rebuild", "director-contract", "director-catchup", "director-priority-authority", "agent-authority", "agent-proposal", "agent-workspace", "agent-observation", "agent-reasoning", "iskra-discovery", "iskra-proposal-boundary", "continuum-contract", "controlled-development", "recovery-status", "state-reconciliation", "scaling-routing", "core-acceptance", "generation-lifecycle", "predictor-artifact", "prediction-registry", "outcome-evaluation", "envelope-healthcheck", "node-check", "dispatch", "collect", "cycle"),
    )
    parser.add_argument("--root", default=os.environ.get("SSI_CANONICAL_ROOT", "./dane/ssi_canonical"))
    parser.add_argument("--sports-root", default=os.environ.get("SSI_SPORTS_ROOT", "./dane/sport_2way"))
    parser.add_argument("--data-root", default=os.environ.get("SSI_DATA_ROOT", "./dane"))
    parser.add_argument("--offline-status", action="store_true")
    parser.add_argument("--world", default="ALL", choices=("ALL", "SPORT", "FOREX", "CAPITAL"))
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = canonical_root(args.root)
    sports_root = Path(args.sports_root).expanduser().resolve()
    data_root = Path(args.data_root).expanduser().resolve()
    queue = bootstrap_default_store(root)
    transport = SshNodeTransport(NODE01_DEFAULT_CONNECTION)

    if args.command == "status":
        result = project_status(root, check_node=not args.offline_status)
    elif args.command == "namespace":
        result = namespace_status()
    elif args.command == "governance":
        result = governance_status()
    elif args.command == "events":
        result = event_journal_status(root)
    elif args.command == "artifacts":
        result = artifact_registry_status(root)
    elif args.command == "teacher":
        result = teacher_status()
    elif args.command == "job-envelope":
        result = job_envelope_status()
    elif args.command == "priority-authority":
        result = priority_authority_status()
    elif args.command == "remote-gateway":
        result = remote_gateway_status()
    elif args.command == "resource-router":
        result = resource_router_status()
    elif args.command == "result-verifier":
        result = result_verifier_status()
    elif args.command == "generation-lifecycle":
        result = generation_lifecycle_status()
    elif args.command == "predictor-artifact":
        result = predictor_artifact_status(root)
    elif args.command == "prediction-registry":
        result = prediction_registry_status(root)
    elif args.command == "outcome-evaluation":
        result = outcome_evaluation_status(root)
    elif args.command == "envelope-healthcheck":
        node_state = transport.check_connection()
        if not node_state.get("ok"):
            result = {"status": "NODE01_UNAVAILABLE", "node01": node_state}
        else:
            node_state["worker_sync"] = sync_node01_worker_status(root)
            gateway = build_unified_remote_gateway(root, strict_dispatch=True)
            receipt = gateway.submit(node01_healthcheck_envelope())
            result = {
                "status": "SUBMITTED",
                "receipt": receipt.to_dict(),
                "node01": node_state,
            }
    elif args.command == "behavior-health-features":
        registry = BehaviorHealthFeatureRegistry(
            Path(args.root) / "behavior" / "behavior_health_features.sqlite3"
        )
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "champion-challenger":
        registry = ChampionChallengerRegistry(
            Path(args.root) / "lifecycle" / "champion_challenger.sqlite3"
        )
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "replay-rebuild":
        engine = ReplayRebuildEngine(Path(args.root))
        print(json.dumps(engine.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "director-contract":
        print(json.dumps(DirectorContract.status(), indent=2, sort_keys=True))
        return 0
    elif args.command == "director-catchup":
        catchup = DirectorCatchup(Path(args.root))
        print(json.dumps(catchup.catch_up_once(advance=False), indent=2, sort_keys=True))
        return 0
    elif args.command == "director-priority-authority":
        authority = DirectorPriorityAuthority()
        print(json.dumps(authority.status(), indent=2, sort_keys=True))
        return 0
    elif args.command == "agent-authority":
        registry = AgentRegistry(Path(args.root) / "agents" / "agents.sqlite3")
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "agent-proposal":
        registry = AgentProposalRegistry(Path(args.root) / "agents" / "agent_proposals.sqlite3")
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "agent-workspace":
        workspace = AgentWorkspace(Path(args.root), "AGENT__DIAGNOSTIC")
        print(json.dumps(workspace.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "agent-observation":
        registry = AgentObservationRegistry(Path(args.root) / "agents" / "agent_observations.sqlite3")
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "agent-reasoning":
        registry = AgentReasoningRegistry(Path(args.root) / "agents" / "agent_reasoning.sqlite3")
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "iskra-discovery":
        registry = IskraDiscoveryEnvironment(Path(args.root) / "iskra" / "discovery.sqlite3")
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "iskra-proposal-boundary":
        boundary = IskraProposalBoundary(Path(args.root) / "iskra" / "proposal_links.sqlite3")
        print(json.dumps(boundary.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "continuum-contract":
        contract = ContinuumEngineeringContract(Path(args.root) / "continuum" / "requests.sqlite3")
        print(json.dumps(contract.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "controlled-development":
        registry = ControlledDevelopmentRegistry(Path(args.root) / "continuum" / "development.sqlite3")
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "recovery-status":
        registry = RecoveryRegistry(Path(args.root) / "recovery" / "recovery.sqlite3")
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "state-reconciliation":
        reconciler = StateReconciler(Path(args.root))
        print(json.dumps(reconciler.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "scaling-routing":
        registry = WorkerRoutingRegistry(Path(args.root) / "scaling" / "routing.sqlite3")
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "core-acceptance":
        registry = CoreAcceptanceRegistry(Path(args.root) / "freeze" / "core_acceptance.sqlite3")
        print(json.dumps(registry.summary(), indent=2, sort_keys=True))
        return 0
    elif args.command == "node-check":
        result = transport.check_connection()
        if result.get("ok"):
            result["worker_sync"] = sync_node01_worker_status(root)
    elif args.command == "dispatch":
        result = dispatch_pending(queue, transport)
    elif args.command == "collect":
        result = collect_remote_results(queue, transport, root / "compute" / "results_collected")
    elif args.command == "cycle":
        deps = ManualCycleDependencies(
            node_check=transport.check_connection,
            collect=lambda: collect_remote_results(queue, transport, root / "compute" / "results_collected"),
            dispatch=lambda: dispatch_pending(queue, transport),
            queue_summary=queue.summary,
        )
        result = ManualWorldCycle(root, sports_root, data_root, deps).run(args.world)
    else:
        result = run_world_cycle(root, sports_root, data_root)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
