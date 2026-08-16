from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


LIFECYCLE_STATES = (
    "PLANNED",
    "QUEUED",
    "TRAINING",
    "HISTORICALLY_EVALUATED",
    "SHADOW",
    "ACTIVE",
    "DEGRADED",
    "SUSPENDED",
    "RETIRED",
    "FAILED",
    "QUARANTINED",
)

TERMINAL_STATES = {"RETIRED", "FAILED", "QUARANTINED"}
PROMOTION_AUTHORITIES = {"ROOT", "WORLD_POLICY", "DIRECTOR"}

ALLOWED_TRANSITIONS = {
    "PLANNED": {"QUEUED", "RETIRED", "QUARANTINED"},
    "QUEUED": {"TRAINING", "FAILED", "QUARANTINED", "RETIRED"},
    "TRAINING": {"HISTORICALLY_EVALUATED", "FAILED", "QUARANTINED"},
    "HISTORICALLY_EVALUATED": {"SHADOW", "FAILED", "QUARANTINED", "RETIRED"},
    "SHADOW": {"ACTIVE", "SUSPENDED", "QUARANTINED", "RETIRED"},
    "ACTIVE": {"DEGRADED", "SUSPENDED", "QUARANTINED", "RETIRED"},
    "DEGRADED": {"ACTIVE", "SUSPENDED", "QUARANTINED", "RETIRED"},
    "SUSPENDED": {"SHADOW", "ACTIVE", "QUARANTINED", "RETIRED"},
    "RETIRED": set(),
    "FAILED": {"RETIRED"},
    "QUARANTINED": {"RETIRED"},
}


class LifecycleError(RuntimeError):
    pass


def _now_unix() -> int:
    return int(time.time())


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GenerationRecord:
    generation_id: str
    world_id: str
    domain_id: str
    network_id: str
    generation: int
    state: str
    governance_id: str
    governance_hash: str
    parent_generation_id: Optional[str]
    created_unix: int
    updated_unix: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GenerationLifecycleRegistry:
    """Durable generation lifecycle with immutable transition history.

    The registry records state; it never trains, promotes, executes, or selects a
    champion by itself. Promotion to ACTIVE requires prospective evidence and a
    recognized authority decision.
    """

    def __init__(self, database: Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.database)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS generations(
                generation_id TEXT PRIMARY KEY,
                world_id TEXT NOT NULL,
                domain_id TEXT NOT NULL,
                network_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                state TEXT NOT NULL,
                governance_id TEXT NOT NULL,
                governance_hash TEXT NOT NULL,
                parent_generation_id TEXT,
                created_unix INTEGER NOT NULL,
                updated_unix INTEGER NOT NULL,
                UNIQUE(network_id, generation)
            );

            CREATE TABLE IF NOT EXISTS generation_transitions(
                transition_id TEXT PRIMARY KEY,
                sequence_number INTEGER NOT NULL UNIQUE,
                generation_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                authority_type TEXT NOT NULL,
                authority_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence_kind TEXT NOT NULL,
                evidence_ref TEXT,
                governance_id TEXT NOT NULL,
                governance_hash TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                previous_content_hash TEXT,
                content_hash TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def create_generation(
        self,
        *,
        world_id: str,
        domain_id: str,
        network_id: str,
        generation: int,
        governance_id: str,
        governance_hash: str,
        parent_generation_id: Optional[str] = None,
        authority_type: str = "WORLD_POLICY",
        authority_id: Optional[str] = None,
        reason: str = "GENERATION_PLANNED",
    ) -> GenerationRecord:
        if generation < 1:
            raise LifecycleError("generation must be >= 1")
        if not governance_id or not governance_hash:
            raise LifecycleError("governance lineage is required")
        if parent_generation_id is not None:
            parent = self.get(parent_generation_id)
            if parent.network_id != network_id:
                raise LifecycleError("parent generation must belong to the same network")
            if parent.generation >= generation:
                raise LifecycleError("parent generation must be older")

        generation_id = f"GEN__{uuid.uuid4().hex.upper()}"
        ts = _now_unix()
        try:
            self.conn.execute(
                """
                INSERT INTO generations(
                    generation_id,world_id,domain_id,network_id,generation,state,
                    governance_id,governance_hash,parent_generation_id,created_unix,updated_unix
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    generation_id, world_id, domain_id, network_id, int(generation), "PLANNED",
                    governance_id, governance_hash, parent_generation_id, ts, ts,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LifecycleError("network generation already exists") from exc

        self._append_transition(
            generation_id=generation_id,
            from_state=None,
            to_state="PLANNED",
            authority_type=authority_type,
            authority_id=authority_id or governance_id,
            reason=reason,
            evidence_kind="GOVERNANCE",
            evidence_ref=governance_id,
            governance_id=governance_id,
            governance_hash=governance_hash,
            commit=False,
        )
        self.conn.commit()
        return self.get(generation_id)

    def get(self, generation_id: str) -> GenerationRecord:
        row = self.conn.execute(
            "SELECT * FROM generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
        if row is None:
            raise LifecycleError(f"unknown generation_id: {generation_id}")
        return GenerationRecord(**dict(row))

    def transition(
        self,
        generation_id: str,
        to_state: str,
        *,
        authority_type: str,
        authority_id: str,
        reason: str,
        evidence_kind: str,
        evidence_ref: Optional[str],
        governance_id: Optional[str] = None,
        governance_hash: Optional[str] = None,
    ) -> GenerationRecord:
        current = self.get(generation_id)
        to_state = str(to_state).upper()
        if to_state not in LIFECYCLE_STATES:
            raise LifecycleError(f"unknown lifecycle state: {to_state}")
        if to_state not in ALLOWED_TRANSITIONS[current.state]:
            raise LifecycleError(f"transition not allowed: {current.state} -> {to_state}")

        effective_governance_id = governance_id or current.governance_id
        effective_governance_hash = governance_hash or current.governance_hash
        if not effective_governance_id or not effective_governance_hash:
            raise LifecycleError("governance lineage is required")

        evidence_kind = str(evidence_kind).upper()
        authority_type = str(authority_type).upper()
        if to_state == "SHADOW" and current.state == "HISTORICALLY_EVALUATED":
            if evidence_kind != "HISTORICAL":
                raise LifecycleError("SHADOW qualification requires historical evidence")
            if not evidence_ref:
                raise LifecycleError("SHADOW qualification requires evidence_ref")

        if to_state == "ACTIVE":
            if authority_type not in PROMOTION_AUTHORITIES:
                raise LifecycleError("ACTIVE promotion requires recognized authority")
            if evidence_kind != "PROSPECTIVE":
                raise LifecycleError("ACTIVE promotion requires prospective evidence")
            if not evidence_ref:
                raise LifecycleError("ACTIVE promotion requires evidence_ref")

        ts = _now_unix()
        self.conn.execute(
            """
            UPDATE generations
            SET state=?, governance_id=?, governance_hash=?, updated_unix=?
            WHERE generation_id=?
            """,
            (to_state, effective_governance_id, effective_governance_hash, ts, generation_id),
        )
        self._append_transition(
            generation_id=generation_id,
            from_state=current.state,
            to_state=to_state,
            authority_type=authority_type,
            authority_id=authority_id,
            reason=reason,
            evidence_kind=evidence_kind,
            evidence_ref=evidence_ref,
            governance_id=effective_governance_id,
            governance_hash=effective_governance_hash,
            commit=False,
        )
        self.conn.commit()
        return self.get(generation_id)

    def history(self, generation_id: str) -> Iterable[Dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM generation_transitions WHERE generation_id=? ORDER BY sequence_number",
                (generation_id,),
            ).fetchall()
        ]

    def _append_transition(
        self,
        *,
        generation_id: str,
        from_state: Optional[str],
        to_state: str,
        authority_type: str,
        authority_id: str,
        reason: str,
        evidence_kind: str,
        evidence_ref: Optional[str],
        governance_id: str,
        governance_hash: str,
        commit: bool,
    ) -> None:
        last = self.conn.execute(
            "SELECT sequence_number,content_hash FROM generation_transitions ORDER BY sequence_number DESC LIMIT 1"
        ).fetchone()
        sequence_number = 1 if last is None else int(last["sequence_number"]) + 1
        previous_content_hash = None if last is None else str(last["content_hash"])
        created_unix = _now_unix()
        transition_id = f"GENTR__{uuid.uuid4().hex.upper()}"
        payload = {
            "transition_id": transition_id,
            "sequence_number": sequence_number,
            "generation_id": generation_id,
            "from_state": from_state,
            "to_state": to_state,
            "authority_type": authority_type,
            "authority_id": authority_id,
            "reason": reason,
            "evidence_kind": evidence_kind,
            "evidence_ref": evidence_ref,
            "governance_id": governance_id,
            "governance_hash": governance_hash,
            "created_unix": created_unix,
            "previous_content_hash": previous_content_hash,
        }
        content_hash = _sha256_text(_canonical_json(payload))
        self.conn.execute(
            """
            INSERT INTO generation_transitions(
                transition_id,sequence_number,generation_id,from_state,to_state,
                authority_type,authority_id,reason,evidence_kind,evidence_ref,
                governance_id,governance_hash,created_unix,previous_content_hash,content_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                transition_id, sequence_number, generation_id, from_state, to_state,
                authority_type, authority_id, reason, evidence_kind, evidence_ref,
                governance_id, governance_hash, created_unix, previous_content_hash, content_hash,
            ),
        )
        if commit:
            self.conn.commit()

    def verify_integrity(self) -> Dict[str, Any]:
        errors = []
        previous = None
        expected_sequence = 1
        rows = self.conn.execute(
            "SELECT * FROM generation_transitions ORDER BY sequence_number"
        ).fetchall()
        for row in rows:
            item = dict(row)
            if int(item["sequence_number"]) != expected_sequence:
                errors.append(f"sequence mismatch at {item['transition_id']}")
            if item["previous_content_hash"] != previous:
                errors.append(f"previous hash mismatch at {item['transition_id']}")
            payload = {k: item[k] for k in (
                "transition_id", "sequence_number", "generation_id", "from_state", "to_state",
                "authority_type", "authority_id", "reason", "evidence_kind", "evidence_ref",
                "governance_id", "governance_hash", "created_unix", "previous_content_hash",
            )}
            calculated = _sha256_text(_canonical_json(payload))
            if calculated != item["content_hash"]:
                errors.append(f"content hash mismatch at {item['transition_id']}")
            previous = item["content_hash"]
            expected_sequence += 1
        return {
            "status": "VALID" if not errors else "INVALID",
            "database": str(self.database),
            "generation_count": int(self.conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]),
            "transition_count": len(rows),
            "errors": errors,
        }


def generation_lifecycle_status() -> Dict[str, Any]:
    return {
        "status": "READY",
        "states": list(LIFECYCLE_STATES),
        "terminal_states": sorted(TERMINAL_STATES),
        "automatic_promotion": False,
        "historical_evidence_can_activate": False,
        "shadow_requires_historical_evidence": True,
        "active_requires_prospective_evidence": True,
        "promotion_authorities": sorted(PROMOTION_AUTHORITIES),
        "node_worker_has_lifecycle_authority": False,
        "transition_history": "APPEND_ONLY_HASH_CHAIN",
    }
