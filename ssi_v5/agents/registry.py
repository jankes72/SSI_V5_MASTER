
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
import json
import sqlite3
import time
import uuid


ALLOWED_AGENT_STATES = {"PLANNED", "ACTIVE", "SUSPENDED", "RETIRED", "QUARANTINED"}

ALLOWED_CAPABILITIES = {
    "OBSERVE",
    "ANALYZE",
    "PROPOSE_JOB",
    "PROPOSE_RETRAIN",
    "PROPOSE_FEATURE",
    "PROPOSE_EXPERIMENT",
    "REQUEST_PRIORITY_REVIEW",
    "REQUEST_PROMOTION_REVIEW",
}

FORBIDDEN_AUTHORITIES = {
    "ROOT_OVERRIDE",
    "DIRECT_LIFECYCLE_MUTATION",
    "DIRECT_PROMOTION",
    "DIRECT_SUSPENSION",
    "DIRECT_QUARANTINE",
    "DIRECT_COMPUTE_DISPATCH",
    "FINAL_PRIORITY_AUTHORITY",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    agent_type: str
    world_id: Optional[str]
    domain_id: Optional[str]
    capabilities: List[str]
    state: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "AgentRecord":
        body = asdict(self)
        body["content_hash"] = ""
        return AgentRecord(**{**body, "content_hash": stable_hash(body)})


class AgentRegistry:
    """
    Immutable agent identity/capability registry.

    Agent trust and capability do not imply authority.
    Agents can propose/request; they cannot execute or mutate lifecycle directly.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agents(
                agent_id TEXT PRIMARY KEY,
                agent_type TEXT NOT NULL,
                world_id TEXT,
                domain_id TEXT,
                capabilities_json TEXT NOT NULL,
                state TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def register(
        self,
        *,
        agent_type: str,
        capabilities: Iterable[str],
        world_id: Optional[str] = None,
        domain_id: Optional[str] = None,
        state: str = "PLANNED",
        agent_id: Optional[str] = None,
    ) -> AgentRecord:
        state = str(state).upper()
        caps = sorted({str(c).upper() for c in capabilities})

        if state not in ALLOWED_AGENT_STATES:
            raise ValueError(f"unsupported agent state: {state}")
        unknown = [c for c in caps if c not in ALLOWED_CAPABILITIES]
        if unknown:
            raise ValueError(f"unsupported agent capabilities: {unknown}")
        if not agent_type:
            raise ValueError("agent_type is required")

        rec = AgentRecord(
            agent_id=agent_id or f"AGENT__{uuid.uuid4().hex.upper()}",
            agent_type=str(agent_type),
            world_id=world_id,
            domain_id=domain_id,
            capabilities=caps,
            state=state,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO agents(
                    agent_id,agent_type,world_id,domain_id,capabilities_json,
                    state,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    rec.agent_id, rec.agent_type, rec.world_id, rec.domain_id,
                    canonical_json(rec.capabilities), rec.state,
                    rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("agent identity is immutable") from exc

        return rec

    def get(self, agent_id: str) -> AgentRecord:
        row = self.conn.execute(
            "SELECT * FROM agents WHERE agent_id=?",
            (agent_id,),
        ).fetchone()
        if row is None:
            raise KeyError(agent_id)
        return AgentRecord(
            agent_id=row["agent_id"],
            agent_type=row["agent_type"],
            world_id=row["world_id"],
            domain_id=row["domain_id"],
            capabilities=json.loads(row["capabilities_json"]),
            state=row["state"],
            created_unix=int(row["created_unix"]),
            content_hash=row["content_hash"],
        )

    def verify(self, agent_id: str) -> Dict[str, Any]:
        rec = self.get(agent_id)
        body = asdict(rec)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        expected = stable_hash(body)
        return {
            "agent_id": agent_id,
            "status": "VALID" if actual == expected else "INVALID",
            "stored_hash": actual,
            "expected_hash": expected,
        }

    def can(self, agent_id: str, capability: str) -> bool:
        rec = self.get(agent_id)
        return (
            rec.state == "ACTIVE"
            and str(capability).upper() in set(rec.capabilities)
        )

    @staticmethod
    def authority_contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "trust_is_authority": False,
            "capability_is_authority": False,
            "agent_can_request": True,
            "agent_can_propose": True,
            "agent_can_execute_directly": False,
            "agent_can_mutate_lifecycle_directly": False,
            "agent_can_promote_directly": False,
            "agent_can_root_override": False,
            "agent_can_be_final_priority_authority": False,
            "forbidden_authorities": sorted(FORBIDDEN_AUTHORITIES),
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        return {
            **self.authority_contract(),
            "agent_count": int(count),
            "allowed_capabilities": sorted(ALLOWED_CAPABILITIES),
            "allowed_states": sorted(ALLOWED_AGENT_STATES),
            "identity_immutable": True,
        }
