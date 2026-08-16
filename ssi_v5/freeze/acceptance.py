
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3
import time
import uuid


REQUIRED_CAPABILITIES = [
    "governance",
    "event_journal",
    "artifact_registry",
    "experience",
    "teacher",
    "job_envelope",
    "priority_authority",
    "remote_worker",
    "resource_router",
    "result_verification",
    "generation_lifecycle",
    "prediction_outcome",
    "behavior_health_features",
    "champion_challenger",
    "replay_rebuild",
    "director",
    "agents",
    "iskra",
    "continuum",
    "recovery",
    "state_reconciliation",
    "multiworker_routing",
]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CoreFreezeRecord:
    freeze_id: str
    core_version: str
    passed_test_count: int
    required_capabilities: List[str]
    capability_status: Dict[str, bool]
    acceptance_status: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "CoreFreezeRecord":
        body = asdict(self)
        body["content_hash"] = ""
        return CoreFreezeRecord(**{**body, "content_hash": stable_hash(body)})


class CoreAcceptanceRegistry:
    """
    Final SSI V5 core acceptance and freeze manifest.

    Freeze is a versioned acceptance record, not a claim that future extension
    is forbidden. Core changes after freeze require a new version and evidence.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS core_freezes(
                freeze_id TEXT PRIMARY KEY,
                core_version TEXT NOT NULL,
                passed_test_count INTEGER NOT NULL,
                required_capabilities_json TEXT NOT NULL,
                capability_status_json TEXT NOT NULL,
                acceptance_status TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def evaluate(
        self,
        *,
        capability_status: Dict[str, bool],
        passed_test_count: int,
        minimum_test_count: int,
    ) -> Dict[str, Any]:
        missing = [c for c in REQUIRED_CAPABILITIES if not bool(capability_status.get(c, False))]
        test_ok = int(passed_test_count) >= int(minimum_test_count)
        accepted = not missing and test_ok
        return {
            "accepted": accepted,
            "missing_capabilities": missing,
            "test_count_ok": test_ok,
            "acceptance_status": "PASSED" if accepted else "FAILED",
        }

    def freeze(
        self,
        *,
        core_version: str,
        capability_status: Dict[str, bool],
        passed_test_count: int,
        minimum_test_count: int,
        freeze_id: Optional[str] = None,
    ) -> CoreFreezeRecord:
        result = self.evaluate(
            capability_status=capability_status,
            passed_test_count=passed_test_count,
            minimum_test_count=minimum_test_count,
        )
        if not result["accepted"]:
            raise ValueError(
                "core acceptance failed: "
                + canonical_json({
                    "missing_capabilities": result["missing_capabilities"],
                    "test_count_ok": result["test_count_ok"],
                })
            )

        rec = CoreFreezeRecord(
            freeze_id=freeze_id or f"FREEZE__{uuid.uuid4().hex.upper()}",
            core_version=core_version,
            passed_test_count=int(passed_test_count),
            required_capabilities=list(REQUIRED_CAPABILITIES),
            capability_status={k: bool(v) for k, v in sorted(capability_status.items())},
            acceptance_status="PASSED",
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO core_freezes(
                    freeze_id,core_version,passed_test_count,
                    required_capabilities_json,capability_status_json,
                    acceptance_status,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    rec.freeze_id, rec.core_version, rec.passed_test_count,
                    canonical_json(rec.required_capabilities),
                    canonical_json(rec.capability_status),
                    rec.acceptance_status, rec.created_unix, rec.content_hash,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("freeze_id is immutable") from exc

        return rec

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "freeze_requires_all_capabilities": True,
            "freeze_requires_full_regression": True,
            "freeze_is_immutable": True,
            "freeze_is_runtime_execution": False,
            "freeze_grants_new_authority": False,
            "post_freeze_changes_require_new_version": True,
            "replay_remains_non_execution": True,
            "director_remains_internal": True,
            "worker_remains_compute_only": True,
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM core_freezes").fetchone()[0]
        return {
            **self.contract(),
            "freeze_count": int(count),
            "required_capability_count": len(REQUIRED_CAPABILITIES),
            "required_capabilities": list(REQUIRED_CAPABILITIES),
        }
