from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import uuid4

SCHEMA_VERSION = 1
ALLOWED_EVENT_TYPES = {
    "WORLD_CYCLE_STARTED",
    "WORLD_CYCLE_COMPLETED",
    "GOVERNANCE_VALIDATED",
    "RAW_EVIDENCE_INGESTED",
    "EXPERIENCE_CREATED",
    "TRAINING_NEED_CREATED",
    "COMPUTE_JOB_CREATED",
    "COMPUTE_JOB_DISPATCHED",
    "COMPUTE_JOB_RESULT_RECEIVED",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_body(body: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    sequence_number: int
    world_id: str
    domain_id: Optional[str]
    cycle_id: str
    event_type: str
    payload: Mapping[str, Any]
    artifact_refs: Sequence[str]
    created_unix: int
    previous_hash: Optional[str]
    content_hash: str

    def body(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence_number": self.sequence_number,
            "world_id": self.world_id,
            "domain_id": self.domain_id,
            "cycle_id": self.cycle_id,
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "artifact_refs": list(self.artifact_refs),
            "created_unix": self.created_unix,
            "previous_hash": self.previous_hash,
        }

    def verify_hash(self) -> bool:
        return self.content_hash == _hash_body(self.body())

    def as_dict(self) -> Dict[str, Any]:
        value = self.body()
        value["content_hash"] = self.content_hash
        return value


class WorldEventJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS world_events (
                    sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    world_id TEXT NOT NULL,
                    domain_id TEXT,
                    cycle_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    created_unix INTEGER NOT NULL,
                    previous_hash TEXT,
                    content_hash TEXT NOT NULL UNIQUE
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_events_world_seq ON world_events(world_id, sequence_number)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_events_cycle_seq ON world_events(cycle_id, sequence_number)")

    def append(
        self,
        *,
        world_id: str,
        cycle_id: str,
        event_type: str,
        domain_id: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
        artifact_refs: Optional[Iterable[str]] = None,
        created_unix: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> EventRecord:
        if not world_id.startswith("WORLD__"):
            raise ValueError(f"Invalid world_id: {world_id}")
        if domain_id is not None and not domain_id.startswith("DOMAIN__"):
            raise ValueError(f"Invalid domain_id: {domain_id}")
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"Unsupported event_type: {event_type}")
        if not cycle_id:
            raise ValueError("cycle_id is required")
        payload_obj = dict(payload or {})
        refs = list(artifact_refs or [])
        created = int(time.time()) if created_unix is None else int(created_unix)
        eid = event_id or f"EVT__{uuid4().hex.upper()}"

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            last = con.execute(
                "SELECT sequence_number, content_hash FROM world_events ORDER BY sequence_number DESC LIMIT 1"
            ).fetchone()
            next_seq = 1 if last is None else int(last["sequence_number"]) + 1
            previous_hash = None if last is None else str(last["content_hash"])
            body = {
                "event_id": eid,
                "sequence_number": next_seq,
                "world_id": world_id,
                "domain_id": domain_id,
                "cycle_id": cycle_id,
                "event_type": event_type,
                "payload": payload_obj,
                "artifact_refs": refs,
                "created_unix": created,
                "previous_hash": previous_hash,
            }
            content_hash = _hash_body(body)
            con.execute(
                """
                INSERT INTO world_events(
                    sequence_number,event_id,world_id,domain_id,cycle_id,event_type,
                    payload_json,artifact_refs_json,created_unix,previous_hash,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    next_seq, eid, world_id, domain_id, cycle_id, event_type,
                    _canonical_json(payload_obj), _canonical_json(refs), created,
                    previous_hash, content_hash,
                ),
            )
        return EventRecord(**body, content_hash=content_hash)

    def list_events(self, *, after_sequence: int = 0, limit: int = 1000) -> List[EventRecord]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM world_events WHERE sequence_number > ? ORDER BY sequence_number ASC LIMIT ?",
                (int(after_sequence), int(limit)),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            event_id=row["event_id"],
            sequence_number=int(row["sequence_number"]),
            world_id=row["world_id"],
            domain_id=row["domain_id"],
            cycle_id=row["cycle_id"],
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"]),
            artifact_refs=json.loads(row["artifact_refs_json"]),
            created_unix=int(row["created_unix"]),
            previous_hash=row["previous_hash"],
            content_hash=row["content_hash"],
        )

    def verify_integrity(self) -> Dict[str, Any]:
        events = self.list_events(limit=10_000_000)
        errors: List[str] = []
        expected_seq = 1
        previous_hash: Optional[str] = None
        for event in events:
            if event.sequence_number != expected_seq:
                errors.append(f"sequence gap: expected {expected_seq}, got {event.sequence_number}")
            if event.previous_hash != previous_hash:
                errors.append(f"previous_hash mismatch at sequence {event.sequence_number}")
            if not event.verify_hash():
                errors.append(f"content_hash mismatch at sequence {event.sequence_number}")
            previous_hash = event.content_hash
            expected_seq += 1
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "VALID" if not errors else "INVALID",
            "event_count": len(events),
            "last_sequence_number": 0 if not events else events[-1].sequence_number,
            "last_content_hash": None if not events else events[-1].content_hash,
            "errors": errors,
            "database": str(self.path),
        }


def event_journal_status(root: Path) -> Dict[str, Any]:
    return WorldEventJournal(Path(root) / "events" / "world_event_journal.sqlite3").verify_integrity()
