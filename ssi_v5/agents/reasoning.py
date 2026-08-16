
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3
import time
import uuid


ALLOWED_REASONING_STATES = {"DRAFT", "READY", "ABSTAIN", "CONFLICT", "QUARANTINED"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentReasoningRecord:
    reasoning_id: str
    agent_id: str
    world_id: Optional[str]
    domain_id: Optional[str]
    observation_refs: List[str]
    memory_refs: List[str]
    hypothesis: str
    analysis: Dict[str, Any]
    local_strategy: Optional[str]
    confidence: Optional[float]
    state: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "AgentReasoningRecord":
        body = asdict(self)
        body["content_hash"] = ""
        return AgentReasoningRecord(**{**body, "content_hash": stable_hash(body)})


class AgentReasoningRegistry:
    """
    Immutable local reasoning records.

    Local reasoning and local strategy are not authority.
    They may support an AgentProposal through a separate boundary.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_reasoning(
                reasoning_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                world_id TEXT,
                domain_id TEXT,
                observation_refs_json TEXT NOT NULL,
                memory_refs_json TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                local_strategy TEXT,
                confidence REAL,
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
        hypothesis: str,
        analysis: Dict[str, Any],
        observation_refs: Optional[List[str]] = None,
        memory_refs: Optional[List[str]] = None,
        local_strategy: Optional[str] = None,
        confidence: Optional[float] = None,
        state: str = "DRAFT",
        world_id: Optional[str] = None,
        domain_id: Optional[str] = None,
        reasoning_id: Optional[str] = None,
    ) -> AgentReasoningRecord:
        state = str(state).upper()

        if state not in ALLOWED_REASONING_STATES:
            raise ValueError(f"unsupported reasoning state: {state}")
        if not agent_id:
            raise ValueError("agent_id is required")
        if not hypothesis:
            raise ValueError("hypothesis is required")
        if not isinstance(analysis, dict):
            raise ValueError("analysis must be a dict")
        if confidence is not None and not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("confidence must be within [0,1]")

        rec = AgentReasoningRecord(
            reasoning_id=reasoning_id or f"AREASON__{uuid.uuid4().hex.upper()}",
            agent_id=agent_id,
            world_id=world_id,
            domain_id=domain_id,
            observation_refs=sorted(set(observation_refs or [])),
            memory_refs=sorted(set(memory_refs or [])),
            hypothesis=str(hypothesis),
            analysis=dict(analysis),
            local_strategy=local_strategy,
            confidence=None if confidence is None else float(confidence),
            state=state,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO agent_reasoning(
                    reasoning_id,agent_id,world_id,domain_id,
                    observation_refs_json,memory_refs_json,hypothesis,
                    analysis_json,local_strategy,confidence,state,
                    created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.reasoning_id, rec.agent_id, rec.world_id, rec.domain_id,
                    canonical_json(rec.observation_refs),
                    canonical_json(rec.memory_refs),
                    rec.hypothesis, canonical_json(rec.analysis),
                    rec.local_strategy, rec.confidence, rec.state,
                    rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("reasoning_id is immutable") from exc

        return rec

    def get(self, reasoning_id: str) -> AgentReasoningRecord:
        row = self.conn.execute(
            "SELECT * FROM agent_reasoning WHERE reasoning_id=?",
            (reasoning_id,),
        ).fetchone()
        if row is None:
            raise KeyError(reasoning_id)
        return AgentReasoningRecord(
            reasoning_id=row["reasoning_id"],
            agent_id=row["agent_id"],
            world_id=row["world_id"],
            domain_id=row["domain_id"],
            observation_refs=json.loads(row["observation_refs_json"]),
            memory_refs=json.loads(row["memory_refs_json"]),
            hypothesis=row["hypothesis"],
            analysis=json.loads(row["analysis_json"]),
            local_strategy=row["local_strategy"],
            confidence=row["confidence"],
            state=row["state"],
            created_unix=int(row["created_unix"]),
            content_hash=row["content_hash"],
        )

    def verify(self, reasoning_id: str) -> Dict[str, Any]:
        rec = self.get(reasoning_id)
        body = asdict(rec)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        expected = stable_hash(body)
        return {
            "reasoning_id": reasoning_id,
            "status": "VALID" if actual == expected else "INVALID",
            "stored_hash": actual,
            "expected_hash": expected,
        }

    @staticmethod
    def boundary_contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "local_reasoning_is_authority": False,
            "local_strategy_is_execution": False,
            "local_strategy_mutates_world_state": False,
            "local_strategy_mutates_lifecycle": False,
            "local_strategy_resolves_final_priority": False,
            "local_reasoning_may_create_proposal": True,
            "proposal_boundary_required": True,
            "abstain_is_valid": True,
            "conflict_is_preserved": True,
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM agent_reasoning").fetchone()[0]
        return {
            **self.boundary_contract(),
            "reasoning_count": int(count),
            "immutable_reasoning": True,
            "states": sorted(ALLOWED_REASONING_STATES),
        }
