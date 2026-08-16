
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3
import time
import uuid


ALLOWED_REQUEST_TYPES = {
    "IMPLEMENT_FEATURE",
    "FIX_DEFECT",
    "BUILD_EXPERIMENT",
    "ADD_TESTS",
    "REFACTOR_INTERNAL",
    "PREPARE_INTEGRATION",
}

ALLOWED_REQUEST_STATES = {
    "REQUESTED",
    "ACCEPTED",
    "PLANNED",
    "IMPLEMENTED",
    "TESTED",
    "REJECTED",
    "QUARANTINED",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContinuumRequest:
    request_id: str
    source_type: str
    source_id: str
    request_type: str
    world_id: Optional[str]
    domain_id: Optional[str]
    requirements: Dict[str, Any]
    evidence_refs: List[str]
    governance_ref: str
    state: str
    created_unix: int
    content_hash: str = ""

    def with_hash(self) -> "ContinuumRequest":
        body = asdict(self)
        body["content_hash"] = ""
        return ContinuumRequest(**{**body, "content_hash": stable_hash(body)})


class ContinuumEngineeringContract:
    """
    Governed engineering intake.

    Continuum may prepare engineering work, but cannot:
      * deploy into runtime automatically
      * mutate lifecycle
      * bypass tests
      * bypass governance
      * self-authorize integration
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS continuum_requests(
                request_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                request_type TEXT NOT NULL,
                world_id TEXT,
                domain_id TEXT,
                requirements_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                governance_ref TEXT NOT NULL,
                state TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def create_request(
        self,
        *,
        source_type: str,
        source_id: str,
        request_type: str,
        requirements: Dict[str, Any],
        evidence_refs: List[str],
        governance_ref: str,
        state: str = "REQUESTED",
        world_id: Optional[str] = None,
        domain_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> ContinuumRequest:
        request_type = str(request_type).upper()
        state = str(state).upper()

        if request_type not in ALLOWED_REQUEST_TYPES:
            raise ValueError(f"unsupported request type: {request_type}")
        if state not in ALLOWED_REQUEST_STATES:
            raise ValueError(f"unsupported request state: {state}")
        if not source_type or not source_id:
            raise ValueError("source_type and source_id are required")
        if not isinstance(requirements, dict):
            raise ValueError("requirements must be a dict")
        if not isinstance(evidence_refs, list):
            raise ValueError("evidence_refs must be a list")
        if not governance_ref:
            raise ValueError("governance_ref is required")

        rec = ContinuumRequest(
            request_id=request_id or f"CONTREQ__{uuid.uuid4().hex.upper()}",
            source_type=str(source_type),
            source_id=str(source_id),
            request_type=request_type,
            world_id=world_id,
            domain_id=domain_id,
            requirements=dict(requirements),
            evidence_refs=sorted(set(str(x) for x in evidence_refs)),
            governance_ref=str(governance_ref),
            state=state,
            created_unix=int(time.time()),
        ).with_hash()

        try:
            self.conn.execute(
                """
                INSERT INTO continuum_requests(
                    request_id,source_type,source_id,request_type,world_id,domain_id,
                    requirements_json,evidence_refs_json,governance_ref,state,
                    created_unix,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.request_id, rec.source_type, rec.source_id, rec.request_type,
                    rec.world_id, rec.domain_id, canonical_json(rec.requirements),
                    canonical_json(rec.evidence_refs), rec.governance_ref, rec.state,
                    rec.created_unix, rec.content_hash
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("request_id is immutable") from exc

        return rec

    def get(self, request_id: str) -> ContinuumRequest:
        row = self.conn.execute(
            "SELECT * FROM continuum_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return ContinuumRequest(
            request_id=row["request_id"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            request_type=row["request_type"],
            world_id=row["world_id"],
            domain_id=row["domain_id"],
            requirements=json.loads(row["requirements_json"]),
            evidence_refs=json.loads(row["evidence_refs_json"]),
            governance_ref=row["governance_ref"],
            state=row["state"],
            created_unix=int(row["created_unix"]),
            content_hash=row["content_hash"],
        )

    def verify(self, request_id: str) -> Dict[str, Any]:
        rec = self.get(request_id)
        body = asdict(rec)
        actual = body.pop("content_hash")
        body["content_hash"] = ""
        expected = stable_hash(body)
        return {
            "request_id": request_id,
            "status": "VALID" if actual == expected else "INVALID",
            "stored_hash": actual,
            "expected_hash": expected,
        }

    @staticmethod
    def contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "continuum_request_is_execution": False,
            "continuum_can_deploy_directly": False,
            "continuum_can_mutate_lifecycle": False,
            "continuum_can_bypass_tests": False,
            "continuum_can_bypass_governance": False,
            "continuum_can_self_authorize_integration": False,
            "evidence_lineage_supported": True,
            "governance_ref_required": True,
            "integration_requires_separate_gate": True,
        }

    def summary(self) -> Dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM continuum_requests").fetchone()[0]
        return {
            **self.contract(),
            "request_count": int(count),
            "immutable_requests": True,
            "allowed_request_types": sorted(ALLOWED_REQUEST_TYPES),
            "allowed_states": sorted(ALLOWED_REQUEST_STATES),
        }
