
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional
import json
import sqlite3
import time
import uuid


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentMemoryRecord:
    memory_id: str
    agent_id: str
    category: str
    payload: Dict[str, Any]
    evidence_ref: Optional[str]
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "AgentMemoryRecord":
        body = asdict(self)
        body["content_hash"] = ""
        return AgentMemoryRecord(**{**body, "content_hash": stable_hash(body)})


class AgentWorkspace:
    """
    Per-agent local workspace and memory.

    Local memory is not canonical world state.
    It cannot mutate:
      * event journal
      * global artifact registry
      * lifecycle registry
      * canonical world state
    """

    def __init__(self, root: Path, agent_id: str):
        if not agent_id:
            raise ValueError("agent_id is required")
        self.root = Path(root)
        self.agent_id = agent_id
        self.workspace_dir = self.root / "agents" / agent_id / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "agents" / agent_id / "local_memory.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_memory(
                memory_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                category TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                evidence_ref TEXT,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def remember(
        self,
        *,
        category: str,
        payload: Dict[str, Any],
        evidence_ref: Optional[str] = None,
        memory_id: Optional[str] = None,
    ) -> AgentMemoryRecord:
        if not category:
            raise ValueError("category is required")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")

        rec = AgentMemoryRecord(
            memory_id=memory_id or f"AMEM__{uuid.uuid4().hex.upper()}",
            agent_id=self.agent_id,
            category=str(category),
            payload=dict(payload),
            evidence_ref=evidence_ref,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO local_memory(
                    memory_id,agent_id,category,payload_json,evidence_ref,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    rec.memory_id, rec.agent_id, rec.category,
                    canonical_json(rec.payload), rec.evidence_ref,
                    rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("memory_id is immutable") from exc
        return rec

    def get(self, memory_id: str) -> AgentMemoryRecord:
        row = self.conn.execute(
            "SELECT * FROM local_memory WHERE memory_id=?",
            (memory_id,),
        ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return AgentMemoryRecord(
            memory_id=row["memory_id"],
            agent_id=row["agent_id"],
            category=row["category"],
            payload=json.loads(row["payload_json"]),
            evidence_ref=row["evidence_ref"],
            created_unix=int(row["created_unix"]),
            content_hash=row["content_hash"],
        )

    def verify(self, memory_id: str) -> Dict[str, Any]:
        rec = self.get(memory_id)
        body = asdict(rec)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        expected = stable_hash(body)
        return {
            "memory_id": memory_id,
            "status": "VALID" if actual == expected else "INVALID",
            "stored_hash": actual,
            "expected_hash": expected,
        }

    @staticmethod
    def evidence_contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "local_memory_is_canonical_world_state": False,
            "local_memory_is_global_artifact": False,
            "agent_can_mutate_event_journal": False,
            "agent_can_mutate_global_artifact_registry": False,
            "agent_can_mutate_lifecycle": False,
            "agent_can_overwrite_canonical_world_state": False,
            "local_memory_may_reference_evidence": True,
            "evidence_reference_does_not_grant_authority": True,
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM local_memory").fetchone()[0]
        return {
            **self.evidence_contract(),
            "agent_id": self.agent_id,
            "memory_count": int(count),
            "workspace_dir": str(self.workspace_dir),
            "memory_immutable": True,
        }
