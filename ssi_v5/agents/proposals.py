
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional
import json
import sqlite3
import time
import uuid


ALLOWED_INTENTS = {
    "PROPOSE_JOB",
    "PROPOSE_RETRAIN",
    "PROPOSE_FEATURE",
    "PROPOSE_EXPERIMENT",
    "REQUEST_PRIORITY_REVIEW",
    "REQUEST_PROMOTION_REVIEW",
}

ALLOWED_TARGETS = {
    "DIRECTOR",
    "WORLD_POLICY",
    "CONTINUUM",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentProposal:
    proposal_id: str
    agent_id: str
    intent: str
    target_authority: str
    world_id: Optional[str]
    domain_id: Optional[str]
    requested_priority: Optional[str]
    payload: Dict[str, Any]
    rationale: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "AgentProposal":
        body = asdict(self)
        body["content_hash"] = ""
        return AgentProposal(**{**body, "content_hash": stable_hash(body)})


class AgentProposalRegistry:
    """
    Immutable intent/proposal registry.

    Proposal is not authority and not execution:
      * it does not create ComputeJob
      * it does not resolve final priority
      * it does not mutate lifecycle
      * it does not promote/suspend/quarantine generations
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_proposals(
                proposal_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                intent TEXT NOT NULL,
                target_authority TEXT NOT NULL,
                world_id TEXT,
                domain_id TEXT,
                requested_priority TEXT,
                payload_json TEXT NOT NULL,
                rationale TEXT NOT NULL,
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
        intent: str,
        target_authority: str,
        payload: Dict[str, Any],
        rationale: str,
        world_id: Optional[str] = None,
        domain_id: Optional[str] = None,
        requested_priority: Optional[str] = None,
        proposal_id: Optional[str] = None,
    ) -> AgentProposal:
        intent = str(intent).upper()
        target_authority = str(target_authority).upper()

        if intent not in ALLOWED_INTENTS:
            raise ValueError(f"unsupported intent: {intent}")
        if target_authority not in ALLOWED_TARGETS:
            raise ValueError(f"unsupported target authority: {target_authority}")
        if not agent_id:
            raise ValueError("agent_id is required")
        if not rationale:
            raise ValueError("rationale is required")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")

        rec = AgentProposal(
            proposal_id=proposal_id or f"APROP__{uuid.uuid4().hex.upper()}",
            agent_id=agent_id,
            intent=intent,
            target_authority=target_authority,
            world_id=world_id,
            domain_id=domain_id,
            requested_priority=requested_priority,
            payload=dict(payload),
            rationale=str(rationale),
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO agent_proposals(
                    proposal_id,agent_id,intent,target_authority,world_id,domain_id,
                    requested_priority,payload_json,rationale,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.proposal_id, rec.agent_id, rec.intent, rec.target_authority,
                    rec.world_id, rec.domain_id, rec.requested_priority,
                    canonical_json(rec.payload), rec.rationale,
                    rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("proposal_id is immutable") from exc

        return rec

    def get(self, proposal_id: str) -> AgentProposal:
        row = self.conn.execute(
            "SELECT * FROM agent_proposals WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return AgentProposal(
            proposal_id=row["proposal_id"],
            agent_id=row["agent_id"],
            intent=row["intent"],
            target_authority=row["target_authority"],
            world_id=row["world_id"],
            domain_id=row["domain_id"],
            requested_priority=row["requested_priority"],
            payload=json.loads(row["payload_json"]),
            rationale=row["rationale"],
            created_unix=int(row["created_unix"]),
            content_hash=row["content_hash"],
        )

    def verify(self, proposal_id: str) -> Dict[str, Any]:
        rec = self.get(proposal_id)
        body = asdict(rec)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        expected = stable_hash(body)
        return {
            "proposal_id": proposal_id,
            "status": "VALID" if actual == expected else "INVALID",
            "stored_hash": actual,
            "expected_hash": expected,
        }

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "proposal_is_execution": False,
            "proposal_creates_compute_job": False,
            "proposal_resolves_final_priority": False,
            "proposal_mutates_lifecycle": False,
            "proposal_promotes_generation": False,
            "requested_priority_is_authoritative": False,
            "authority_review_required": True,
            "allowed_intents": sorted(ALLOWED_INTENTS),
            "allowed_targets": sorted(ALLOWED_TARGETS),
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM agent_proposals").fetchone()[0]
        return {
            **self.contract(),
            "proposal_count": int(count),
            "immutable_proposals": True,
        }
