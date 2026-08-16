
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional
import json
import sqlite3
import time
import uuid


COMPONENT_STATES = {
    "HEALTHY",
    "DEGRADED",
    "SUSPENDED",
    "QUARANTINED",
    "FAILED",
    "RECOVERING",
    "RECOVERED",
}

FAILURE_CLASSES = {
    "NETWORK_FAILURE",
    "AGENT_FAILURE",
    "ISKRA_FAILURE",
    "CONTINUUM_FAILURE",
    "WORKER_FAILURE",
    "ARTIFACT_FAILURE",
    "UNKNOWN_FAILURE",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FailureRecord:
    failure_id: str
    component_type: str
    component_id: str
    failure_class: str
    state: str
    reason: str
    evidence_ref: str
    director_runtime_affected: bool
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "FailureRecord":
        body = asdict(self)
        body["content_hash"] = ""
        return FailureRecord(**{**body, "content_hash": stable_hash(body)})


class RecoveryRegistry:
    """
    Failure isolation and recovery evidence.

    Component failure must not imply Director Runtime failure.
    Recovery records are immutable evidence, not authority to restart or promote.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS failures(
                failure_id TEXT PRIMARY KEY,
                component_type TEXT NOT NULL,
                component_id TEXT NOT NULL,
                failure_class TEXT NOT NULL,
                state TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                director_runtime_affected INTEGER NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def record_failure(
        self,
        *,
        component_type: str,
        component_id: str,
        failure_class: str,
        state: str,
        reason: str,
        evidence_ref: str,
        director_runtime_affected: bool = False,
        failure_id: Optional[str] = None,
    ) -> FailureRecord:
        failure_class = str(failure_class).upper()
        state = str(state).upper()

        if failure_class not in FAILURE_CLASSES:
            raise ValueError(f"unsupported failure class: {failure_class}")
        if state not in COMPONENT_STATES:
            raise ValueError(f"unsupported component state: {state}")
        if not component_type or not component_id:
            raise ValueError("component_type and component_id are required")
        if not reason:
            raise ValueError("reason is required")
        if not evidence_ref:
            raise ValueError("evidence_ref is required")

        rec = FailureRecord(
            failure_id=failure_id or f"FAIL__{uuid.uuid4().hex.upper()}",
            component_type=str(component_type),
            component_id=str(component_id),
            failure_class=failure_class,
            state=state,
            reason=str(reason),
            evidence_ref=str(evidence_ref),
            director_runtime_affected=bool(director_runtime_affected),
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO failures(
                    failure_id,component_type,component_id,failure_class,state,
                    reason,evidence_ref,director_runtime_affected,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.failure_id, rec.component_type, rec.component_id,
                    rec.failure_class, rec.state, rec.reason, rec.evidence_ref,
                    1 if rec.director_runtime_affected else 0,
                    rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("failure_id is immutable") from exc

        return rec

    def get(self, failure_id: str) -> FailureRecord:
        row = self.conn.execute(
            "SELECT * FROM failures WHERE failure_id=?",
            (failure_id,),
        ).fetchone()
        if row is None:
            raise KeyError(failure_id)
        return FailureRecord(
            failure_id=row["failure_id"],
            component_type=row["component_type"],
            component_id=row["component_id"],
            failure_class=row["failure_class"],
            state=row["state"],
            reason=row["reason"],
            evidence_ref=row["evidence_ref"],
            director_runtime_affected=bool(row["director_runtime_affected"]),
            created_unix=int(row["created_unix"]),
            content_hash=row["content_hash"],
        )

    def verify(self, failure_id: str) -> Dict[str, Any]:
        rec = self.get(failure_id)
        body = asdict(rec)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        expected = stable_hash(body)
        return {
            "failure_id": failure_id,
            "status": "VALID" if actual == expected else "INVALID",
            "stored_hash": actual,
            "expected_hash": expected,
        }

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "component_failure_implies_director_failure": False,
            "director_runtime_isolation_required": True,
            "failed_component_may_be_degraded": True,
            "failed_component_may_be_quarantined": True,
            "automatic_promotion_on_recovery": False,
            "automatic_lifecycle_mutation_on_recovery": False,
            "automatic_restart_authority": False,
            "failure_evidence_preserved": True,
            "recovery_requires_separate_verification": True,
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
        return {
            **self.contract(),
            "failure_count": int(count),
            "immutable_failure_records": True,
            "component_states": sorted(COMPONENT_STATES),
            "failure_classes": sorted(FAILURE_CLASSES),
        }
