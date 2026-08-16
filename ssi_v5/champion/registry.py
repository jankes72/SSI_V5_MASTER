
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional
import json
import sqlite3
import time
import uuid


DECISIONS = {"PROMOTE", "KEEP_AS_SHADOW", "REJECT", "SUSPEND", "QUARANTINE"}
AUTHORITIES = {"ROOT", "WORLD_POLICY", "DIRECTOR"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(payload: Dict[str, Any]) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LineageDecision:
    decision_id: str
    network_id: str
    champion_generation_id: Optional[str]
    challenger_generation_id: str
    decision: str
    authority_type: str
    authority_id: str
    evidence_scope: str
    evidence_ref: str
    reason: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "LineageDecision":
        body = asdict(self)
        body["content_hash"] = ""
        return LineageDecision(**{**body, "content_hash": sha(body)})


class ChampionChallengerRegistry:
    """
    Immutable decision and parent-child lineage registry.

    No decision here mutates generation rows. The lifecycle registry remains
    authoritative for actual state transitions.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS generation_lineage(
                child_generation_id TEXT PRIMARY KEY,
                parent_generation_id TEXT,
                network_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_unix INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS champion_decisions(
                decision_id TEXT PRIMARY KEY,
                network_id TEXT NOT NULL,
                champion_generation_id TEXT,
                challenger_generation_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                authority_type TEXT NOT NULL,
                authority_id TEXT NOT NULL,
                evidence_scope TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def register_child(
        self,
        *,
        network_id: str,
        child_generation_id: str,
        parent_generation_id: Optional[str],
        relation_type: str = "RETRAIN_CHILD",
    ) -> Dict[str, Any]:
        if not network_id or not child_generation_id:
            raise ValueError("network_id and child_generation_id are required")
        if parent_generation_id == child_generation_id:
            raise ValueError("generation cannot be its own parent")
        try:
            self.conn.execute(
                """
                INSERT INTO generation_lineage(
                    child_generation_id,parent_generation_id,network_id,relation_type,created_unix
                ) VALUES(?,?,?,?,?)
                """,
                (
                    child_generation_id, parent_generation_id, network_id,
                    relation_type, int(time.time())
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("child generation lineage is immutable") from exc
        return self.lineage(child_generation_id)

    def lineage(self, child_generation_id: str) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM generation_lineage WHERE child_generation_id=?",
            (child_generation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(child_generation_id)
        return dict(row)

    def decide(
        self,
        *,
        network_id: str,
        challenger_generation_id: str,
        decision: str,
        authority_type: str,
        authority_id: str,
        evidence_scope: str,
        evidence_ref: str,
        reason: str,
        champion_generation_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> LineageDecision:
        decision = str(decision).upper()
        authority_type = str(authority_type).upper()
        evidence_scope = str(evidence_scope).upper()

        if decision not in DECISIONS:
            raise ValueError(f"unsupported decision: {decision}")
        if authority_type not in AUTHORITIES:
            raise ValueError("caller is not champion/challenger authority")
        if not evidence_ref:
            raise ValueError("evidence_ref is required")
        if decision == "PROMOTE" and evidence_scope != "PROSPECTIVE":
            raise ValueError("PROMOTE requires PROSPECTIVE evidence")
        if champion_generation_id and champion_generation_id == challenger_generation_id:
            raise ValueError("champion and challenger must be different generations")

        rec = LineageDecision(
            decision_id=decision_id or f"CCD__{uuid.uuid4().hex.upper()}",
            network_id=network_id,
            champion_generation_id=champion_generation_id,
            challenger_generation_id=challenger_generation_id,
            decision=decision,
            authority_type=authority_type,
            authority_id=authority_id,
            evidence_scope=evidence_scope,
            evidence_ref=evidence_ref,
            reason=reason,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO champion_decisions(
                    decision_id,network_id,champion_generation_id,challenger_generation_id,
                    decision,authority_type,authority_id,evidence_scope,evidence_ref,reason,
                    created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.decision_id, rec.network_id, rec.champion_generation_id,
                    rec.challenger_generation_id, rec.decision, rec.authority_type,
                    rec.authority_id, rec.evidence_scope, rec.evidence_ref,
                    rec.reason, rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("decision_id is immutable") from exc

        return rec

    def verify_decision(self, decision_id: str) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM champion_decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        d = dict(row)
        body = {
            "decision_id": d["decision_id"],
            "network_id": d["network_id"],
            "champion_generation_id": d["champion_generation_id"],
            "challenger_generation_id": d["challenger_generation_id"],
            "decision": d["decision"],
            "authority_type": d["authority_type"],
            "authority_id": d["authority_id"],
            "evidence_scope": d["evidence_scope"],
            "evidence_ref": d["evidence_ref"],
            "reason": d["reason"],
            "created_unix": d["created_unix"],
            "content_hash": "",
        }
        expected = sha(body)
        return {
            "decision_id": decision_id,
            "status": "VALID" if expected == d["content_hash"] else "INVALID",
            "stored_hash": d["content_hash"],
            "expected_hash": expected,
        }

    def summary(self) -> Dict[str, Any]:
        decision_count = self.conn.execute("SELECT COUNT(*) FROM champion_decisions").fetchone()[0]
        lineage_count = self.conn.execute("SELECT COUNT(*) FROM generation_lineage").fetchone()[0]
        return {
            "status": "READY",
            "decision_count": int(decision_count),
            "lineage_count": int(lineage_count),
            "decisions": sorted(DECISIONS),
            "automatic_promotion": False,
            "promotion_requires_prospective_evidence": True,
            "retraining_mutates_parent_generation": False,
            "child_generation_required_for_retraining": True,
            "parent_child_lineage_immutable": True,
            "worker_has_champion_authority": False,
            "lifecycle_state_changed_here": False,
        }
