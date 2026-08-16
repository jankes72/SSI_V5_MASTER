from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ssi_v5.predictions.registry import PredictionRegistry, PredictionRegistryError

OUTCOME_STATES = {"UNKNOWN", "WAITING_RESULT", "VERIFIED", "CONFLICT", "QUARANTINED"}


class OutcomeEvaluationError(RuntimeError):
    pass


def _now_unix() -> int:
    return int(time.time())


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OutcomeRecord:
    outcome_id: str
    prediction_id: str
    status: str
    outcome_json: str
    evidence_ref: str
    evidence_source: str
    observed_time: Optional[str]
    created_unix: int
    content_hash: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["outcome"] = json.loads(data.pop("outcome_json"))
        return data


@dataclass(frozen=True)
class EvaluationRecord:
    evaluation_id: str
    prediction_id: str
    outcome_id: str
    evaluator_id: str
    evaluation_json: str
    created_unix: int
    content_hash: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["evaluation"] = json.loads(data.pop("evaluation_json"))
        return data


class OutcomeEvaluationRegistry:
    """Immutable outcomes and evaluations referencing immutable predictions.

    Predictions are never updated here. Conflicting verified evidence is preserved:
    the later conflicting record is stored as CONFLICT instead of replacing history.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.database = self.root / "outcomes" / "outcome_evaluation.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.database)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outcomes(
                outcome_id TEXT PRIMARY KEY,
                prediction_id TEXT NOT NULL,
                status TEXT NOT NULL,
                outcome_json TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                evidence_source TEXT NOT NULL,
                observed_time TEXT,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL UNIQUE
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluations(
                evaluation_id TEXT PRIMARY KEY,
                prediction_id TEXT NOT NULL,
                outcome_id TEXT NOT NULL,
                evaluator_id TEXT NOT NULL,
                evaluation_json TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL UNIQUE
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _verified_prediction(self, prediction_id: str):
        predictions = PredictionRegistry(self.root)
        try:
            verification = predictions.verify(prediction_id)
            if verification.get("status") != "VALID":
                raise OutcomeEvaluationError("prediction is not valid")
            return predictions.get(prediction_id)
        except PredictionRegistryError as exc:
            raise OutcomeEvaluationError(str(exc)) from exc
        finally:
            predictions.close()

    def register_outcome(
        self,
        *,
        prediction_id: str,
        status: str,
        outcome: Optional[Dict[str, Any]],
        evidence_ref: str,
        evidence_source: str,
        observed_time: Optional[str] = None,
        outcome_id: Optional[str] = None,
    ) -> OutcomeRecord:
        self._verified_prediction(prediction_id)
        status = str(status).upper().strip()
        if status not in OUTCOME_STATES:
            raise OutcomeEvaluationError(f"unsupported outcome status: {status}")
        if not str(evidence_ref).strip() or not str(evidence_source).strip():
            raise OutcomeEvaluationError("evidence_ref and evidence_source are required")
        if outcome is None:
            outcome = {}
        if not isinstance(outcome, dict):
            raise OutcomeEvaluationError("outcome must be an object")
        if status == "VERIFIED" and not outcome:
            raise OutcomeEvaluationError("VERIFIED outcome requires non-empty outcome")

        effective_status = status
        if status == "VERIFIED":
            rows = self.conn.execute(
                "SELECT outcome_json FROM outcomes WHERE prediction_id=? AND status='VERIFIED'",
                (prediction_id,),
            ).fetchall()
            incoming = _canonical_json(outcome)
            if any(str(r["outcome_json"]) != incoming for r in rows):
                effective_status = "CONFLICT"

        outcome_id = outcome_id or f"OUT__{uuid.uuid4().hex.upper()}"
        created_unix = _now_unix()
        payload = {
            "outcome_id": outcome_id,
            "prediction_id": prediction_id,
            "status": effective_status,
            "outcome": outcome,
            "evidence_ref": str(evidence_ref),
            "evidence_source": str(evidence_source),
            "observed_time": observed_time,
            "created_unix": created_unix,
        }
        content_hash = _sha256_json(payload)
        try:
            self.conn.execute(
                """
                INSERT INTO outcomes(
                    outcome_id,prediction_id,status,outcome_json,evidence_ref,evidence_source,
                    observed_time,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    outcome_id, prediction_id, effective_status, _canonical_json(outcome),
                    str(evidence_ref), str(evidence_source), observed_time, created_unix, content_hash,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise OutcomeEvaluationError("outcome is immutable/duplicate") from exc
        return self.get_outcome(outcome_id)

    def get_outcome(self, outcome_id: str) -> OutcomeRecord:
        row = self.conn.execute("SELECT * FROM outcomes WHERE outcome_id=?", (outcome_id,)).fetchone()
        if row is None:
            raise OutcomeEvaluationError(f"unknown outcome_id: {outcome_id}")
        return OutcomeRecord(**dict(row))

    def verify_outcome(self, outcome_id: str) -> Dict[str, Any]:
        record = self.get_outcome(outcome_id)
        payload = {
            "outcome_id": record.outcome_id,
            "prediction_id": record.prediction_id,
            "status": record.status,
            "outcome": json.loads(record.outcome_json),
            "evidence_ref": record.evidence_ref,
            "evidence_source": record.evidence_source,
            "observed_time": record.observed_time,
            "created_unix": record.created_unix,
        }
        actual = _sha256_json(payload)
        errors = [] if actual == record.content_hash else ["outcome content hash mismatch"]
        return {"status": "VALID" if not errors else "INVALID", "outcome_id": outcome_id, "errors": errors}

    def evaluate(
        self,
        *,
        outcome_id: str,
        evaluator_id: str,
        evaluation: Dict[str, Any],
        evaluation_id: Optional[str] = None,
    ) -> EvaluationRecord:
        outcome = self.get_outcome(outcome_id)
        if self.verify_outcome(outcome_id).get("status") != "VALID":
            raise OutcomeEvaluationError("outcome is not valid")
        if outcome.status != "VERIFIED":
            raise OutcomeEvaluationError("only VERIFIED outcome may be evaluated")
        self._verified_prediction(outcome.prediction_id)
        if not str(evaluator_id).strip():
            raise OutcomeEvaluationError("evaluator_id is required")
        if not isinstance(evaluation, dict) or not evaluation:
            raise OutcomeEvaluationError("evaluation must be a non-empty object")

        evaluation_id = evaluation_id or f"EVAL__{uuid.uuid4().hex.upper()}"
        created_unix = _now_unix()
        payload = {
            "evaluation_id": evaluation_id,
            "prediction_id": outcome.prediction_id,
            "outcome_id": outcome_id,
            "evaluator_id": str(evaluator_id),
            "evaluation": evaluation,
            "created_unix": created_unix,
        }
        content_hash = _sha256_json(payload)
        try:
            self.conn.execute(
                """
                INSERT INTO evaluations(
                    evaluation_id,prediction_id,outcome_id,evaluator_id,evaluation_json,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    evaluation_id, outcome.prediction_id, outcome_id, str(evaluator_id),
                    _canonical_json(evaluation), created_unix, content_hash,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise OutcomeEvaluationError("evaluation is immutable/duplicate") from exc
        return self.get_evaluation(evaluation_id)

    def get_evaluation(self, evaluation_id: str) -> EvaluationRecord:
        row = self.conn.execute("SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
        if row is None:
            raise OutcomeEvaluationError(f"unknown evaluation_id: {evaluation_id}")
        return EvaluationRecord(**dict(row))

    def verify_evaluation(self, evaluation_id: str) -> Dict[str, Any]:
        record = self.get_evaluation(evaluation_id)
        payload = {
            "evaluation_id": record.evaluation_id,
            "prediction_id": record.prediction_id,
            "outcome_id": record.outcome_id,
            "evaluator_id": record.evaluator_id,
            "evaluation": json.loads(record.evaluation_json),
            "created_unix": record.created_unix,
        }
        actual = _sha256_json(payload)
        errors = [] if actual == record.content_hash else ["evaluation content hash mismatch"]
        return {"status": "VALID" if not errors else "INVALID", "evaluation_id": evaluation_id, "errors": errors}

    def summary(self) -> Dict[str, Any]:
        outcome_count = int(self.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0])
        evaluation_count = int(self.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0])
        by_status = {
            str(r["status"]): int(r["n"])
            for r in self.conn.execute("SELECT status,COUNT(*) AS n FROM outcomes GROUP BY status ORDER BY status")
        }
        return {
            "status": "READY",
            "schema_version": 1,
            "database": str(self.database),
            "outcome_count": outcome_count,
            "evaluation_count": evaluation_count,
            "outcomes_by_status": by_status,
        }


def outcome_evaluation_status(root: Path | None = None) -> Dict[str, Any]:
    base = Path(root or "./dane/ssi_canonical").expanduser().resolve()
    registry = OutcomeEvaluationRegistry(base)
    try:
        summary = registry.summary()
    finally:
        registry.close()
    return {
        **summary,
        "prediction_is_immutable": True,
        "outcome_is_separate_record": True,
        "evaluation_is_separate_record": True,
        "conflicting_evidence_preserved": True,
        "conflicting_verified_outcome_becomes": "CONFLICT",
        "verified_outcome_required_for_evaluation": True,
        "unknown_is_valid_state": True,
        "waiting_result_is_valid_state": True,
        "automatic_prediction_overwrite": False,
    }
