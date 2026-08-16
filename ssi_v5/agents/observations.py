
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional
import json
import sqlite3
import time
import uuid


OBSERVATION_STATES = {"OBSERVED", "UNKNOWN", "CONFLICT", "QUARANTINED"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentObservation:
    observation_id: str
    agent_id: str
    world_id: Optional[str]
    domain_id: Optional[str]
    source_ref: str
    observed_at_unix: int
    payload: Dict[str, Any]
    state: str
    generation_id: Optional[str]
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "AgentObservation":
        body = asdict(self)
        body["content_hash"] = ""
        return AgentObservation(**{**body, "content_hash": stable_hash(body)})


class AgentObservationRegistry:
    """
    Agent-local observation intake.

    Observation != Experience.
    Observation != training material for the same generation.
    Observation != canonical world state mutation.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_observations(
                observation_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                world_id TEXT,
                domain_id TEXT,
                source_ref TEXT NOT NULL,
                observed_at_unix INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL,
                generation_id TEXT,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def record(
        self,
        *,
        agent_id: str,
        source_ref: str,
        payload: Dict[str, Any],
        observed_at_unix: int,
        state: str = "OBSERVED",
        world_id: Optional[str] = None,
        domain_id: Optional[str] = None,
        generation_id: Optional[str] = None,
        observation_id: Optional[str] = None,
    ) -> AgentObservation:
        state = str(state).upper()
        if state not in OBSERVATION_STATES:
            raise ValueError(f"unsupported observation state: {state}")
        if not agent_id:
            raise ValueError("agent_id is required")
        if not source_ref:
            raise ValueError("source_ref is required")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        if int(observed_at_unix) < 0:
            raise ValueError("observed_at_unix must be >= 0")

        rec = AgentObservation(
            observation_id=observation_id or f"AOBS__{uuid.uuid4().hex.upper()}",
            agent_id=agent_id,
            world_id=world_id,
            domain_id=domain_id,
            source_ref=source_ref,
            observed_at_unix=int(observed_at_unix),
            payload=dict(payload),
            state=state,
            generation_id=generation_id,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO agent_observations(
                    observation_id,agent_id,world_id,domain_id,source_ref,
                    observed_at_unix,payload_json,state,generation_id,
                    created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.observation_id, rec.agent_id, rec.world_id, rec.domain_id,
                    rec.source_ref, rec.observed_at_unix, canonical_json(rec.payload),
                    rec.state, rec.generation_id, rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("observation_id is immutable") from exc

        return rec

    def get(self, observation_id: str) -> AgentObservation:
        row = self.conn.execute(
            "SELECT * FROM agent_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(observation_id)
        return AgentObservation(
            observation_id=row["observation_id"],
            agent_id=row["agent_id"],
            world_id=row["world_id"],
            domain_id=row["domain_id"],
            source_ref=row["source_ref"],
            observed_at_unix=int(row["observed_at_unix"]),
            payload=json.loads(row["payload_json"]),
            state=row["state"],
            generation_id=row["generation_id"],
            created_unix=int(row["created_unix"]),
            content_hash=row["content_hash"],
        )

    def verify(self, observation_id: str) -> Dict[str, Any]:
        rec = self.get(observation_id)
        body = asdict(rec)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        expected = stable_hash(body)
        return {
            "observation_id": observation_id,
            "status": "VALID" if actual == expected else "INVALID",
            "stored_hash": actual,
            "expected_hash": expected,
        }

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "observation_is_experience": False,
            "observation_is_training_material_same_generation": False,
            "observation_mutates_canonical_world_state": False,
            "observation_mutates_lifecycle": False,
            "observation_creates_prediction": False,
            "observation_may_reference_generation": True,
            "conflicting_observation_preserved": True,
            "unknown_observation_preserved": True,
            "promotion_requires_separate_pipeline": True,
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM agent_observations").fetchone()[0]
        return {
            **self.contract(),
            "observation_count": int(count),
            "immutable_observations": True,
            "states": sorted(OBSERVATION_STATES),
        }
