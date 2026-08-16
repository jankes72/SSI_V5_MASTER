from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ssi_v5.lifecycle.generation_registry import GenerationLifecycleRegistry, LifecycleError
from ssi_v5.predictors.artifact import PredictorArtifactRegistry, PredictorArtifactError

ALLOWED_PREDICTION_STATES = {"SHADOW", "ACTIVE", "DEGRADED"}


class PredictionRegistryError(RuntimeError):
    pass


def _now_unix() -> int:
    return int(time.time())


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PredictionRecord:
    prediction_id: str
    predictor_artifact_id: str
    generation_id: str
    world_id: str
    domain_id: str
    network_id: str
    generation: int
    target_time: str
    prediction_kind: str
    prediction_json: str
    context_json: str
    governance_id: str
    governance_hash: str
    source_envelope_id: Optional[str]
    created_unix: int
    content_hash: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["prediction"] = json.loads(data.pop("prediction_json"))
        data["context"] = json.loads(data.pop("context_json"))
        return data


class PredictionRegistry:
    """Immutable predictions bound to a verified predictor artifact and generation.

    Outcome data is deliberately absent from this registry. A later outcome/evaluation
    layer may reference prediction_id but must never update the prediction record.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.database = self.root / "predictions" / "prediction_registry.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.database)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions(
                prediction_id TEXT PRIMARY KEY,
                predictor_artifact_id TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                world_id TEXT NOT NULL,
                domain_id TEXT NOT NULL,
                network_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                target_time TEXT NOT NULL,
                prediction_kind TEXT NOT NULL,
                prediction_json TEXT NOT NULL,
                context_json TEXT NOT NULL,
                governance_id TEXT NOT NULL,
                governance_hash TEXT NOT NULL,
                source_envelope_id TEXT,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL UNIQUE
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def register(
        self,
        *,
        predictor_artifact_id: str,
        target_time: str,
        prediction_kind: str,
        prediction: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        source_envelope_id: Optional[str] = None,
        prediction_id: Optional[str] = None,
    ) -> PredictionRecord:
        target_time = str(target_time).strip()
        prediction_kind = str(prediction_kind).strip()
        if not target_time:
            raise PredictionRegistryError("target_time is required")
        if not prediction_kind:
            raise PredictionRegistryError("prediction_kind is required")
        if not isinstance(prediction, dict) or not prediction:
            raise PredictionRegistryError("prediction must be a non-empty object")
        if context is not None and not isinstance(context, dict):
            raise PredictionRegistryError("context must be an object")

        artifacts = PredictorArtifactRegistry(self.root)
        try:
            verification = artifacts.verify(predictor_artifact_id)
            if verification.get("status") != "VALID":
                raise PredictionRegistryError("predictor artifact is not valid")
            artifact = artifacts.get(predictor_artifact_id)
        except PredictorArtifactError as exc:
            raise PredictionRegistryError(str(exc)) from exc
        finally:
            artifacts.close()

        lifecycle = GenerationLifecycleRegistry(self.root / "lifecycle" / "generation_lifecycle.sqlite3")
        try:
            generation = lifecycle.get(artifact.generation_id)
        except LifecycleError as exc:
            raise PredictionRegistryError(str(exc)) from exc
        finally:
            lifecycle.close()

        if generation.state not in ALLOWED_PREDICTION_STATES:
            raise PredictionRegistryError(
                f"generation state does not allow prediction: {generation.state}"
            )
        if (
            artifact.world_id != generation.world_id
            or artifact.domain_id != generation.domain_id
            or artifact.network_id != generation.network_id
            or artifact.generation != generation.generation
        ):
            raise PredictionRegistryError("predictor/generation lineage mismatch")

        prediction_id = prediction_id or f"PRED__{uuid.uuid4().hex.upper()}"
        created_unix = _now_unix()
        payload = {
            "prediction_id": prediction_id,
            "predictor_artifact_id": artifact.predictor_artifact_id,
            "generation_id": artifact.generation_id,
            "world_id": artifact.world_id,
            "domain_id": artifact.domain_id,
            "network_id": artifact.network_id,
            "generation": artifact.generation,
            "target_time": target_time,
            "prediction_kind": prediction_kind,
            "prediction": prediction,
            "context": context or {},
            "governance_id": artifact.governance_id,
            "governance_hash": artifact.governance_hash,
            "source_envelope_id": source_envelope_id or artifact.source_envelope_id,
            "created_unix": created_unix,
        }
        content_hash = _sha256_json(payload)
        try:
            self.conn.execute(
                """
                INSERT INTO predictions(
                    prediction_id,predictor_artifact_id,generation_id,world_id,domain_id,
                    network_id,generation,target_time,prediction_kind,prediction_json,context_json,
                    governance_id,governance_hash,source_envelope_id,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    prediction_id, artifact.predictor_artifact_id, artifact.generation_id,
                    artifact.world_id, artifact.domain_id, artifact.network_id, artifact.generation,
                    target_time, prediction_kind, _canonical_json(prediction), _canonical_json(context or {}),
                    artifact.governance_id, artifact.governance_hash,
                    source_envelope_id or artifact.source_envelope_id, created_unix, content_hash,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise PredictionRegistryError("prediction is immutable/duplicate") from exc
        return self.get(prediction_id)

    def get(self, prediction_id: str) -> PredictionRecord:
        row = self.conn.execute(
            "SELECT * FROM predictions WHERE prediction_id=?", (prediction_id,)
        ).fetchone()
        if row is None:
            raise PredictionRegistryError(f"unknown prediction_id: {prediction_id}")
        return PredictionRecord(**dict(row))

    def verify(self, prediction_id: str) -> Dict[str, Any]:
        record = self.get(prediction_id)
        payload = {
            "prediction_id": record.prediction_id,
            "predictor_artifact_id": record.predictor_artifact_id,
            "generation_id": record.generation_id,
            "world_id": record.world_id,
            "domain_id": record.domain_id,
            "network_id": record.network_id,
            "generation": record.generation,
            "target_time": record.target_time,
            "prediction_kind": record.prediction_kind,
            "prediction": json.loads(record.prediction_json),
            "context": json.loads(record.context_json),
            "governance_id": record.governance_id,
            "governance_hash": record.governance_hash,
            "source_envelope_id": record.source_envelope_id,
            "created_unix": record.created_unix,
        }
        actual = _sha256_json(payload)
        errors = [] if actual == record.content_hash else ["prediction content hash mismatch"]
        return {
            "status": "VALID" if not errors else "INVALID",
            "prediction_id": prediction_id,
            "errors": errors,
        }

    def summary(self) -> Dict[str, Any]:
        count = int(self.conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0])
        return {
            "status": "READY",
            "schema_version": 1,
            "database": str(self.database),
            "prediction_count": count,
        }


def prediction_registry_status(root: Path | None = None) -> Dict[str, Any]:
    base = Path(root or "./dane/ssi_canonical").expanduser().resolve()
    registry = PredictionRegistry(base)
    try:
        summary = registry.summary()
    finally:
        registry.close()
    return {
        **summary,
        "immutable_predictions": True,
        "outcome_stored_in_prediction_record": False,
        "predictor_artifact_required": True,
        "predictor_artifact_must_verify": True,
        "generation_states_allowed": sorted(ALLOWED_PREDICTION_STATES),
        "target_time_required": True,
        "generation_lineage_required": True,
        "governance_lineage_required": True,
    }
