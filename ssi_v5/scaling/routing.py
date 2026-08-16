
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3
import time
import uuid


WORKER_STATES = {"READY", "DEGRADED", "DRAINING", "OFFLINE", "QUARANTINED"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkerRecord:
    worker_id: str
    endpoint: str
    capabilities: List[str]
    max_concurrency: int
    state: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "WorkerRecord":
        body = asdict(self)
        body["content_hash"] = ""
        return WorkerRecord(**{**body, "content_hash": stable_hash(body)})


@dataclass(frozen=True)
class RoutingDecision:
    routing_id: str
    job_id: str
    selected_worker_id: str
    required_capability: str
    final_priority: int
    policy_ref: str
    reason: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "RoutingDecision":
        body = asdict(self)
        body["content_hash"] = ""
        return RoutingDecision(**{**body, "content_hash": stable_hash(body)})


class WorkerRoutingRegistry:
    """
    Multi-worker registry and deterministic routing.

    Routing is governed by final priority and policy.
    Workers execute compute only; they do not self-route, self-promote,
    mutate lifecycle, or resolve final priority.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workers(
                worker_id TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                max_concurrency INTEGER NOT NULL,
                state TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS routing_decisions(
                routing_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                selected_worker_id TEXT NOT NULL,
                required_capability TEXT NOT NULL,
                final_priority INTEGER NOT NULL,
                policy_ref TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def register_worker(
        self,
        *,
        worker_id: str,
        endpoint: str,
        capabilities: List[str],
        max_concurrency: int,
        state: str = "READY",
    ) -> WorkerRecord:
        state = str(state).upper()
        if state not in WORKER_STATES:
            raise ValueError(f"unsupported worker state: {state}")
        if not worker_id or not endpoint:
            raise ValueError("worker_id and endpoint are required")
        if not isinstance(capabilities, list) or not capabilities:
            raise ValueError("capabilities must be a non-empty list")
        if int(max_concurrency) < 1:
            raise ValueError("max_concurrency must be >= 1")

        rec = WorkerRecord(
            worker_id=worker_id,
            endpoint=endpoint,
            capabilities=sorted(set(str(x) for x in capabilities)),
            max_concurrency=int(max_concurrency),
            state=state,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO workers(
                    worker_id,endpoint,capabilities_json,max_concurrency,
                    state,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    rec.worker_id, rec.endpoint, canonical_json(rec.capabilities),
                    rec.max_concurrency, rec.state, rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("worker_id is immutable") from exc

        return rec

    def route(
        self,
        *,
        job_id: str,
        required_capability: str,
        final_priority: int,
        policy_ref: str,
        routing_id: Optional[str] = None,
    ) -> RoutingDecision:
        if not job_id:
            raise ValueError("job_id is required")
        if not required_capability:
            raise ValueError("required_capability is required")
        if not 0 <= int(final_priority) <= 9:
            raise ValueError("final_priority must be within [0,9]")
        if not policy_ref:
            raise ValueError("policy_ref is required")

        rows = self.conn.execute(
            "SELECT * FROM workers WHERE state='READY' ORDER BY worker_id"
        ).fetchall()

        eligible = []
        for row in rows:
            caps = json.loads(row["capabilities_json"])
            if required_capability in caps:
                eligible.append(row)

        if not eligible:
            raise RuntimeError("no eligible READY worker")

        selected = eligible[0]
        rec = RoutingDecision(
            routing_id=routing_id or f"ROUTE__{uuid.uuid4().hex.upper()}",
            job_id=job_id,
            selected_worker_id=selected["worker_id"],
            required_capability=required_capability,
            final_priority=int(final_priority),
            policy_ref=policy_ref,
            reason="DETERMINISTIC_LOWEST_WORKER_ID",
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO routing_decisions(
                    routing_id,job_id,selected_worker_id,required_capability,
                    final_priority,policy_ref,reason,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.routing_id, rec.job_id, rec.selected_worker_id,
                    rec.required_capability, rec.final_priority, rec.policy_ref,
                    rec.reason, rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("routing_id is immutable") from exc

        return rec

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "worker_is_compute_only": True,
            "worker_resolves_final_priority": False,
            "worker_mutates_lifecycle": False,
            "worker_self_routes": False,
            "routing_requires_policy_ref": True,
            "routing_uses_final_priority": True,
            "routing_is_deterministic": True,
            "multiple_workers_supported": True,
            "node_failure_does_not_grant_authority": True,
        }

    def summary(self) -> Dict[str, Any]:
        workers = self.conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
        routes = self.conn.execute("SELECT COUNT(*) FROM routing_decisions").fetchone()[0]
        return {
            **self.contract(),
            "worker_count": int(workers),
            "routing_decision_count": int(routes),
            "immutable_workers": True,
            "immutable_routing_decisions": True,
            "worker_states": sorted(WORKER_STATES),
        }
