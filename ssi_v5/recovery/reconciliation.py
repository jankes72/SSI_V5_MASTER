
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3
import time
import uuid


RECONCILIATION_STATES = {
    "CONSISTENT",
    "REBUILD_REQUIRED",
    "DEGRADED",
    "QUARANTINED",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReconciliationRecord:
    reconciliation_id: str
    journal_sequence: int
    checkpoint_sequence: int
    state: str
    discrepancies: List[str]
    replay_required: bool
    historical_reexecution_required: bool
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "ReconciliationRecord":
        body = asdict(self)
        body["content_hash"] = ""
        return ReconciliationRecord(**{**body, "content_hash": stable_hash(body)})


class StateReconciler:
    """
    Restart and disaster-recovery reconciliation.

    Reconciliation never re-executes historical jobs. It compares durable
    checkpoints against the event journal and returns a safe recovery decision.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.db_path = self.root / "recovery" / "reconciliation.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reconciliation_records(
                reconciliation_id TEXT PRIMARY KEY,
                journal_sequence INTEGER NOT NULL,
                checkpoint_sequence INTEGER NOT NULL,
                state TEXT NOT NULL,
                discrepancies_json TEXT NOT NULL,
                replay_required INTEGER NOT NULL,
                historical_reexecution_required INTEGER NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    @staticmethod
    def evaluate(
        *,
        journal_sequence: int,
        checkpoint_sequence: int,
        registry_health: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        if journal_sequence < 0 or checkpoint_sequence < 0:
            raise ValueError("sequences must be >= 0")

        registry_health = dict(registry_health or {})
        discrepancies: List[str] = []

        if checkpoint_sequence > journal_sequence:
            discrepancies.append("CHECKPOINT_AHEAD_OF_JOURNAL")

        if checkpoint_sequence < journal_sequence:
            discrepancies.append("CHECKPOINT_BEHIND_JOURNAL")

        for name, healthy in sorted(registry_health.items()):
            if not bool(healthy):
                discrepancies.append(f"REGISTRY_UNHEALTHY:{name}")

        if "CHECKPOINT_AHEAD_OF_JOURNAL" in discrepancies:
            state = "QUARANTINED"
            replay_required = False
        elif any(x.startswith("REGISTRY_UNHEALTHY:") for x in discrepancies):
            state = "DEGRADED"
            replay_required = checkpoint_sequence < journal_sequence
        elif checkpoint_sequence < journal_sequence:
            state = "REBUILD_REQUIRED"
            replay_required = True
        else:
            state = "CONSISTENT"
            replay_required = False

        return {
            "state": state,
            "discrepancies": discrepancies,
            "replay_required": replay_required,
            "historical_reexecution_required": False,
            "safe_to_resume": state == "CONSISTENT",
        }

    def record(
        self,
        *,
        journal_sequence: int,
        checkpoint_sequence: int,
        registry_health: Optional[Dict[str, bool]] = None,
        reconciliation_id: Optional[str] = None,
    ) -> ReconciliationRecord:
        result = self.evaluate(
            journal_sequence=journal_sequence,
            checkpoint_sequence=checkpoint_sequence,
            registry_health=registry_health,
        )
        rec = ReconciliationRecord(
            reconciliation_id=reconciliation_id or f"RECON__{uuid.uuid4().hex.upper()}",
            journal_sequence=int(journal_sequence),
            checkpoint_sequence=int(checkpoint_sequence),
            state=result["state"],
            discrepancies=list(result["discrepancies"]),
            replay_required=bool(result["replay_required"]),
            historical_reexecution_required=False,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO reconciliation_records(
                    reconciliation_id,journal_sequence,checkpoint_sequence,state,
                    discrepancies_json,replay_required,historical_reexecution_required,
                    created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.reconciliation_id, rec.journal_sequence,
                    rec.checkpoint_sequence, rec.state,
                    canonical_json(rec.discrepancies),
                    1 if rec.replay_required else 0,
                    1 if rec.historical_reexecution_required else 0,
                    rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("reconciliation_id is immutable") from exc

        return rec

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "restart_reexecutes_historical_jobs": False,
            "restart_recreates_predictions": False,
            "reconciliation_uses_durable_state": True,
            "checkpoint_ahead_of_journal_is_safe": False,
            "checkpoint_behind_journal_requires_replay": True,
            "unhealthy_registry_may_degrade_runtime": True,
            "automatic_destructive_repair": False,
            "resume_requires_consistent_state": True,
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute(
            "SELECT COUNT(*) FROM reconciliation_records"
        ).fetchone()[0]
        return {
            **self.contract(),
            "reconciliation_count": int(count),
            "immutable_reconciliation_records": True,
            "states": sorted(RECONCILIATION_STATES),
        }
