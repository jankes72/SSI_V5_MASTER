
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3
import time
import uuid


WORK_STATES = {
    "PLANNED",
    "IMPLEMENTED",
    "TESTED",
    "INTEGRATION_CANDIDATE",
    "REJECTED",
    "QUARANTINED",
}

INTEGRATION_DECISIONS = {
    "APPROVE_CANDIDATE",
    "REJECT",
    "QUARANTINE",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DevelopmentRecord:
    development_id: str
    request_id: str
    implementation_ref: str
    changed_paths: List[str]
    test_refs: List[str]
    evidence_refs: List[str]
    governance_ref: str
    state: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "DevelopmentRecord":
        body = asdict(self)
        body["content_hash"] = ""
        return DevelopmentRecord(**{**body, "content_hash": stable_hash(body)})


@dataclass(frozen=True)
class IntegrationDecision:
    integration_id: str
    development_id: str
    decision: str
    authority_type: str
    authority_id: str
    test_evidence_ref: str
    rationale: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "IntegrationDecision":
        body = asdict(self)
        body["content_hash"] = ""
        return IntegrationDecision(**{**body, "content_hash": stable_hash(body)})


class ControlledDevelopmentRegistry:
    """
    Controlled development pipeline:
      request -> implementation -> tests -> evidence -> integration candidate

    Integration candidate != runtime deployment.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS developments(
                development_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                implementation_ref TEXT NOT NULL,
                changed_paths_json TEXT NOT NULL,
                test_refs_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                governance_ref TEXT NOT NULL,
                state TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS integration_decisions(
                integration_id TEXT PRIMARY KEY,
                development_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                authority_type TEXT NOT NULL,
                authority_id TEXT NOT NULL,
                test_evidence_ref TEXT NOT NULL,
                rationale TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def register_development(
        self,
        *,
        request_id: str,
        implementation_ref: str,
        changed_paths: List[str],
        test_refs: List[str],
        evidence_refs: List[str],
        governance_ref: str,
        state: str = "IMPLEMENTED",
        development_id: Optional[str] = None,
    ) -> DevelopmentRecord:
        state = str(state).upper()

        if state not in WORK_STATES:
            raise ValueError(f"unsupported development state: {state}")
        if not request_id or not implementation_ref or not governance_ref:
            raise ValueError("request_id, implementation_ref and governance_ref are required")
        if not isinstance(changed_paths, list):
            raise ValueError("changed_paths must be a list")
        if not isinstance(test_refs, list):
            raise ValueError("test_refs must be a list")
        if not isinstance(evidence_refs, list):
            raise ValueError("evidence_refs must be a list")

        rec = DevelopmentRecord(
            development_id=development_id or f"DEV__{uuid.uuid4().hex.upper()}",
            request_id=request_id,
            implementation_ref=implementation_ref,
            changed_paths=sorted(set(str(x) for x in changed_paths)),
            test_refs=sorted(set(str(x) for x in test_refs)),
            evidence_refs=sorted(set(str(x) for x in evidence_refs)),
            governance_ref=governance_ref,
            state=state,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO developments(
                    development_id,request_id,implementation_ref,changed_paths_json,
                    test_refs_json,evidence_refs_json,governance_ref,state,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.development_id, rec.request_id, rec.implementation_ref,
                    canonical_json(rec.changed_paths), canonical_json(rec.test_refs),
                    canonical_json(rec.evidence_refs), rec.governance_ref,
                    rec.state, rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("development_id is immutable") from exc

        return rec

    def decide_integration(
        self,
        *,
        development_id: str,
        decision: str,
        authority_type: str,
        authority_id: str,
        test_evidence_ref: str,
        rationale: str,
        integration_id: Optional[str] = None,
    ) -> IntegrationDecision:
        decision = str(decision).upper()
        authority_type = str(authority_type).upper()

        if decision not in INTEGRATION_DECISIONS:
            raise ValueError(f"unsupported integration decision: {decision}")
        if authority_type not in {"ROOT", "WORLD_POLICY", "DIRECTOR"}:
            raise ValueError("unsupported integration authority")
        if not authority_id:
            raise ValueError("authority_id is required")
        if not test_evidence_ref:
            raise ValueError("test_evidence_ref is required")
        if not rationale:
            raise ValueError("rationale is required")

        dev = self.conn.execute(
            "SELECT * FROM developments WHERE development_id=?",
            (development_id,),
        ).fetchone()
        if dev is None:
            raise KeyError(development_id)

        if decision == "APPROVE_CANDIDATE":
            test_refs = json.loads(dev["test_refs_json"])
            evidence_refs = json.loads(dev["evidence_refs_json"])
            if not test_refs or not evidence_refs:
                raise ValueError("tests and evidence are required before integration candidate approval")

        rec = IntegrationDecision(
            integration_id=integration_id or f"INTDEC__{uuid.uuid4().hex.upper()}",
            development_id=development_id,
            decision=decision,
            authority_type=authority_type,
            authority_id=authority_id,
            test_evidence_ref=test_evidence_ref,
            rationale=rationale,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO integration_decisions(
                    integration_id,development_id,decision,authority_type,authority_id,
                    test_evidence_ref,rationale,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.integration_id, rec.development_id, rec.decision,
                    rec.authority_type, rec.authority_id, rec.test_evidence_ref,
                    rec.rationale, rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("integration_id is immutable") from exc

        return rec

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "implementation_is_runtime_deployment": False,
            "tests_required_before_candidate": True,
            "evidence_required_before_candidate": True,
            "integration_candidate_is_runtime_deployment": False,
            "runtime_deployment_automatic": False,
            "continuum_self_authorizes_integration": False,
            "authority_review_required": True,
            "governance_ref_required": True,
            "rollback_required": True,
        }

    def summary(self) -> Dict[str, Any]:
        dev_count = self.conn.execute("SELECT COUNT(*) FROM developments").fetchone()[0]
        dec_count = self.conn.execute("SELECT COUNT(*) FROM integration_decisions").fetchone()[0]
        return {
            **self.contract(),
            "development_count": int(dev_count),
            "integration_decision_count": int(dec_count),
            "immutable_development_records": True,
            "immutable_integration_decisions": True,
            "work_states": sorted(WORK_STATES),
            "integration_decisions": sorted(INTEGRATION_DECISIONS),
        }
