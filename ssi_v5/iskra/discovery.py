
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3
import time
import uuid


DISCOVERY_STATES = {"DRAFT", "RUNNING", "COMPLETE", "ABSTAIN", "CONFLICT", "QUARANTINED"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DiscoveryRecord:
    discovery_id: str
    agent_id: str
    world_id: Optional[str]
    domain_id: Optional[str]
    question: str
    hypothesis: str
    method: Dict[str, Any]
    evidence_refs: List[str]
    findings: Dict[str, Any]
    state: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "DiscoveryRecord":
        body = asdict(self)
        body["content_hash"] = ""
        return DiscoveryRecord(**{**body, "content_hash": stable_hash(body)})


class IskraDiscoveryEnvironment:
    """
    Local governed discovery environment.

    Discovery evidence may support future proposals.
    It is never canonical truth, execution, lifecycle mutation or promotion.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discoveries(
                discovery_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                world_id TEXT,
                domain_id TEXT,
                question TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                method_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                findings_json TEXT NOT NULL,
                state TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def create(
        self,
        *,
        agent_id: str,
        question: str,
        hypothesis: str,
        method: Dict[str, Any],
        evidence_refs: Optional[List[str]] = None,
        findings: Optional[Dict[str, Any]] = None,
        state: str = "DRAFT",
        world_id: Optional[str] = None,
        domain_id: Optional[str] = None,
        discovery_id: Optional[str] = None,
    ) -> DiscoveryRecord:
        state = str(state).upper()

        if state not in DISCOVERY_STATES:
            raise ValueError(f"unsupported discovery state: {state}")
        if not agent_id:
            raise ValueError("agent_id is required")
        if not question:
            raise ValueError("question is required")
        if not hypothesis:
            raise ValueError("hypothesis is required")
        if not isinstance(method, dict):
            raise ValueError("method must be a dict")
        if findings is not None and not isinstance(findings, dict):
            raise ValueError("findings must be a dict")

        rec = DiscoveryRecord(
            discovery_id=discovery_id or f"ISKRA__{uuid.uuid4().hex.upper()}",
            agent_id=agent_id,
            world_id=world_id,
            domain_id=domain_id,
            question=str(question),
            hypothesis=str(hypothesis),
            method=dict(method),
            evidence_refs=sorted(set(evidence_refs or [])),
            findings=dict(findings or {}),
            state=state,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO discoveries(
                    discovery_id,agent_id,world_id,domain_id,question,hypothesis,
                    method_json,evidence_refs_json,findings_json,state,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.discovery_id, rec.agent_id, rec.world_id, rec.domain_id,
                    rec.question, rec.hypothesis, canonical_json(rec.method),
                    canonical_json(rec.evidence_refs), canonical_json(rec.findings),
                    rec.state, rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("discovery_id is immutable") from exc

        return rec

    def get(self, discovery_id: str) -> DiscoveryRecord:
        row = self.conn.execute(
            "SELECT * FROM discoveries WHERE discovery_id=?",
            (discovery_id,),
        ).fetchone()
        if row is None:
            raise KeyError(discovery_id)
        return DiscoveryRecord(
            discovery_id=row["discovery_id"],
            agent_id=row["agent_id"],
            world_id=row["world_id"],
            domain_id=row["domain_id"],
            question=row["question"],
            hypothesis=row["hypothesis"],
            method=json.loads(row["method_json"]),
            evidence_refs=json.loads(row["evidence_refs_json"]),
            findings=json.loads(row["findings_json"]),
            state=row["state"],
            created_unix=int(row["created_unix"]),
            content_hash=row["content_hash"],
        )

    def verify(self, discovery_id: str) -> Dict[str, Any]:
        rec = self.get(discovery_id)
        body = asdict(rec)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        expected = stable_hash(body)
        return {
            "discovery_id": discovery_id,
            "status": "VALID" if actual == expected else "INVALID",
            "stored_hash": actual,
            "expected_hash": expected,
        }

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "principle": "DESIGN_WORLD_NOT_ANSWER",
            "discovery_is_canonical_truth": False,
            "discovery_is_execution": False,
            "discovery_creates_compute_job": False,
            "discovery_mutates_lifecycle": False,
            "discovery_promotes_generation": False,
            "discovery_may_support_proposal": True,
            "proposal_boundary_required": True,
            "abstain_is_valid": True,
            "conflict_is_preserved": True,
            "evidence_required_for_claims": True,
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0]
        return {
            **self.contract(),
            "discovery_count": int(count),
            "immutable_discoveries": True,
            "states": sorted(DISCOVERY_STATES),
        }
