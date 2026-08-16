
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional
import json
import sqlite3
import time
import uuid


ALLOWED_PROPOSAL_INTENTS = {
    "PROPOSE_EXPERIMENT",
    "PROPOSE_FEATURE",
    "PROPOSE_RETRAIN",
    "PROPOSE_JOB",
}

ALLOWED_TARGETS = {"DIRECTOR", "WORLD_POLICY", "CONTINUUM"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IskraProposalLink:
    link_id: str
    discovery_id: str
    agent_id: str
    intent: str
    target_authority: str
    proposal_payload: Dict[str, Any]
    evidence_refs: list[str]
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "IskraProposalLink":
        body = asdict(self)
        body["content_hash"] = ""
        return IskraProposalLink(**{**body, "content_hash": stable_hash(body)})


class IskraProposalBoundary:
    """
    Controlled bridge from discovery evidence to a governed proposal.

    Discovery may support a proposal, but:
      * does not create ComputeJob
      * does not resolve priority
      * does not mutate lifecycle
      * does not promote a generation
      * does not bypass Director / WorldPolicy / Continuum
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS iskra_proposal_links(
                link_id TEXT PRIMARY KEY,
                discovery_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                intent TEXT NOT NULL,
                target_authority TEXT NOT NULL,
                proposal_payload_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def create_link(
        self,
        *,
        discovery_id: str,
        agent_id: str,
        intent: str,
        target_authority: str,
        proposal_payload: Dict[str, Any],
        evidence_refs: list[str],
        link_id: Optional[str] = None,
    ) -> IskraProposalLink:
        intent = str(intent).upper()
        target_authority = str(target_authority).upper()

        if not discovery_id:
            raise ValueError("discovery_id is required")
        if not agent_id:
            raise ValueError("agent_id is required")
        if intent not in ALLOWED_PROPOSAL_INTENTS:
            raise ValueError(f"unsupported proposal intent: {intent}")
        if target_authority not in ALLOWED_TARGETS:
            raise ValueError(f"unsupported target authority: {target_authority}")
        if not isinstance(proposal_payload, dict):
            raise ValueError("proposal_payload must be a dict")
        if not isinstance(evidence_refs, list):
            raise ValueError("evidence_refs must be a list")
        if not evidence_refs:
            raise ValueError("at least one evidence_ref is required")

        rec = IskraProposalLink(
            link_id=link_id or f"ISKRALINK__{uuid.uuid4().hex.upper()}",
            discovery_id=discovery_id,
            agent_id=agent_id,
            intent=intent,
            target_authority=target_authority,
            proposal_payload=dict(proposal_payload),
            evidence_refs=sorted(set(str(x) for x in evidence_refs)),
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO iskra_proposal_links(
                    link_id,discovery_id,agent_id,intent,target_authority,
                    proposal_payload_json,evidence_refs_json,created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.link_id, rec.discovery_id, rec.agent_id, rec.intent,
                    rec.target_authority, canonical_json(rec.proposal_payload),
                    canonical_json(rec.evidence_refs), rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("link_id is immutable") from exc

        return rec

    def get(self, link_id: str) -> IskraProposalLink:
        row = self.conn.execute(
            "SELECT * FROM iskra_proposal_links WHERE link_id=?",
            (link_id,),
        ).fetchone()
        if row is None:
            raise KeyError(link_id)
        return IskraProposalLink(
            link_id=row["link_id"],
            discovery_id=row["discovery_id"],
            agent_id=row["agent_id"],
            intent=row["intent"],
            target_authority=row["target_authority"],
            proposal_payload=json.loads(row["proposal_payload_json"]),
            evidence_refs=json.loads(row["evidence_refs_json"]),
            created_unix=int(row["created_unix"]),
            content_hash=row["content_hash"],
        )

    def verify(self, link_id: str) -> Dict[str, Any]:
        rec = self.get(link_id)
        body = asdict(rec)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        expected = stable_hash(body)
        return {
            "link_id": link_id,
            "status": "VALID" if actual == expected else "INVALID",
            "stored_hash": actual,
            "expected_hash": expected,
        }

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "discovery_directly_creates_job": False,
            "discovery_directly_mutates_lifecycle": False,
            "discovery_directly_promotes_generation": False,
            "discovery_resolves_final_priority": False,
            "proposal_review_required": True,
            "evidence_lineage_required": True,
            "director_or_governance_boundary_required": True,
            "node01_is_not_target_authority": True,
            "proposal_is_execution": False,
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM iskra_proposal_links").fetchone()[0]
        return {
            **self.contract(),
            "link_count": int(count),
            "immutable_links": True,
            "allowed_intents": sorted(ALLOWED_PROPOSAL_INTENTS),
            "allowed_targets": sorted(ALLOWED_TARGETS),
        }
