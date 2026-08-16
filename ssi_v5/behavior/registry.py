
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3
import time
import uuid


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(payload: Dict[str, Any]) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


ALLOWED_RECORD_KINDS = {"BEHAVIOR", "HEALTH", "FEATURE"}
ALLOWED_HEALTH_STATES = {"HEALTHY", "DEGRADED", "UNHEALTHY", "UNKNOWN"}
ALLOWED_FEATURE_STATES = {"STABLE", "DRIFT", "MISSING", "UNKNOWN"}


@dataclass(frozen=True)
class BehaviorHealthFeatureRecord:
    record_id: str
    record_kind: str
    predictor_artifact_id: str
    generation_id: str
    world_id: str
    domain_id: str
    network_id: str
    generation: int
    observation_unix: int
    payload: Dict[str, Any]
    source_prediction_id: Optional[str] = None
    source_evaluation_id: Optional[str] = None
    governance_id: Optional[str] = None
    governance_hash: Optional[str] = None
    record_hash: str = ""

    def with_hash(self) -> "BehaviorHealthFeatureRecord":
        body = asdict(self)
        body["record_hash"] = ""
        return BehaviorHealthFeatureRecord(**{**body, "record_hash": content_hash(body)})


class BehaviorHealthFeatureRegistry:
    """
    Immutable observation registry for model behavior, model health, and feature health.

    It has NO authority to:
      * modify a prediction/outcome/evaluation
      * alter generation lifecycle
      * promote/suspend a generation
      * enqueue or execute work
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bhf_records(
                record_id TEXT PRIMARY KEY,
                record_kind TEXT NOT NULL,
                predictor_artifact_id TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                world_id TEXT NOT NULL,
                domain_id TEXT NOT NULL,
                network_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                observation_unix INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                source_prediction_id TEXT,
                source_evaluation_id TEXT,
                governance_id TEXT,
                governance_hash TEXT,
                record_hash TEXT NOT NULL,
                created_unix INTEGER NOT NULL
            )
            """
        )
        self.conn.commit()

    def register(
        self,
        *,
        record_kind: str,
        predictor_artifact_id: str,
        generation_id: str,
        world_id: str,
        domain_id: str,
        network_id: str,
        generation: int,
        payload: Dict[str, Any],
        source_prediction_id: Optional[str] = None,
        source_evaluation_id: Optional[str] = None,
        governance_id: Optional[str] = None,
        governance_hash: Optional[str] = None,
        observation_unix: Optional[int] = None,
        record_id: Optional[str] = None,
    ) -> BehaviorHealthFeatureRecord:
        kind = str(record_kind).upper()
        if kind not in ALLOWED_RECORD_KINDS:
            raise ValueError(f"Unsupported record_kind: {kind}")

        if not predictor_artifact_id or not generation_id or not world_id or not domain_id or not network_id:
            raise ValueError("Predictor/generation/world/domain/network lineage is required")

        if kind == "HEALTH":
            state = str(payload.get("health_state", "UNKNOWN")).upper()
            if state not in ALLOWED_HEALTH_STATES:
                raise ValueError(f"Unsupported health_state: {state}")
        if kind == "FEATURE":
            state = str(payload.get("feature_state", "UNKNOWN")).upper()
            if state not in ALLOWED_FEATURE_STATES:
                raise ValueError(f"Unsupported feature_state: {state}")

        record = BehaviorHealthFeatureRecord(
            record_id=record_id or f"BHF__{uuid.uuid4().hex.upper()}",
            record_kind=kind,
            predictor_artifact_id=str(predictor_artifact_id),
            generation_id=str(generation_id),
            world_id=str(world_id),
            domain_id=str(domain_id),
            network_id=str(network_id),
            generation=int(generation),
            observation_unix=int(observation_unix or time.time()),
            payload=dict(payload),
            source_prediction_id=source_prediction_id,
            source_evaluation_id=source_evaluation_id,
            governance_id=governance_id,
            governance_hash=governance_hash,
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO bhf_records(
                    record_id,record_kind,predictor_artifact_id,generation_id,world_id,domain_id,
                    network_id,generation,observation_unix,payload_json,source_prediction_id,
                    source_evaluation_id,governance_id,governance_hash,record_hash,created_unix
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.record_id, record.record_kind, record.predictor_artifact_id,
                    record.generation_id, record.world_id, record.domain_id, record.network_id,
                    record.generation, record.observation_unix, canonical_json(record.payload),
                    record.source_prediction_id, record.source_evaluation_id,
                    record.governance_id, record.governance_hash,
                    record.record_hash, int(time.time()),
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Immutable record_id already exists: {record.record_id}") from exc

        return record

    def get(self, record_id: str) -> Dict[str, Any]:
        row = self.conn.execute("SELECT * FROM bhf_records WHERE record_id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        out = dict(row)
        out["payload"] = json.loads(out.pop("payload_json"))
        return out

    def verify(self, record_id: str) -> Dict[str, Any]:
        row = self.get(record_id)
        body = {
            "record_id": row["record_id"],
            "record_kind": row["record_kind"],
            "predictor_artifact_id": row["predictor_artifact_id"],
            "generation_id": row["generation_id"],
            "world_id": row["world_id"],
            "domain_id": row["domain_id"],
            "network_id": row["network_id"],
            "generation": row["generation"],
            "observation_unix": row["observation_unix"],
            "payload": row["payload"],
            "source_prediction_id": row["source_prediction_id"],
            "source_evaluation_id": row["source_evaluation_id"],
            "governance_id": row["governance_id"],
            "governance_hash": row["governance_hash"],
            "record_hash": "",
        }
        expected = content_hash(body)
        return {
            "record_id": record_id,
            "status": "VALID" if expected == row["record_hash"] else "INVALID",
            "expected_hash": expected,
            "stored_hash": row["record_hash"],
        }

    def summary(self) -> Dict[str, Any]:
        rows = self.conn.execute(
            "SELECT record_kind,COUNT(*) AS n FROM bhf_records GROUP BY record_kind ORDER BY record_kind"
        ).fetchall()
        return {
            "status": "READY",
            "schema_version": self.SCHEMA_VERSION,
            "record_count": sum(int(r["n"]) for r in rows),
            "by_kind": [dict(r) for r in rows],
            "immutable_records": True,
            "prediction_mutation_allowed": False,
            "outcome_mutation_allowed": False,
            "evaluation_mutation_allowed": False,
            "lifecycle_authority": False,
            "execution_authority": False,
            "health_signal_is_not_lifecycle_decision": True,
            "feature_drift_is_evidence_not_automatic_retrain": True,
        }
