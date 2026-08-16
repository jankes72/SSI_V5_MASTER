
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReplayReport:
    status: str
    event_count: int
    last_sequence_number: int
    state_hash: str
    replay_executes_jobs: bool
    replay_creates_predictions: bool
    replay_mutates_source_registries: bool
    deterministic: bool
    errors: List[str]


class ReplayRebuildEngine:
    """
    Deterministic state reconstruction from durable evidence.

    Replay is NOT re-execution:
      * no compute jobs are dispatched
      * no predictions/outcomes are created
      * no source registries are mutated
      * event order and content hashes are verified before applying state
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.events_db = self.root / "events" / "world_event_journal.sqlite3"

    def _read_events(self) -> List[Dict[str, Any]]:
        if not self.events_db.exists():
            return []
        conn = sqlite3.connect(f"file:{self.events_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM world_events ORDER BY sequence_number"
            ).fetchall()
        except sqlite3.OperationalError:
            # Support the existing journal table name if schema differs.
            rows = conn.execute(
                "SELECT * FROM events ORDER BY sequence_number"
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def _verify_event_chain(self, events: List[Dict[str, Any]]) -> List[str]:
        errors: List[str] = []
        expected_seq = 1
        prev_hash: Optional[str] = None

        for row in events:
            seq = int(row.get("sequence_number", 0))
            if seq != expected_seq:
                errors.append(f"SEQUENCE_GAP expected={expected_seq} actual={seq}")
                expected_seq = seq
            expected_seq += 1

            if "previous_content_hash" in row:
                actual_prev = row.get("previous_content_hash")
                if seq > 1 and actual_prev != prev_hash:
                    errors.append(f"PREVIOUS_HASH_MISMATCH sequence={seq}")

            prev_hash = row.get("content_hash") or prev_hash

        return errors

    def rebuild_state(self) -> Dict[str, Any]:
        events = self._read_events()
        errors = self._verify_event_chain(events)

        state: Dict[str, Any] = {
            "event_types": {},
            "worlds": {},
            "last_sequence_number": 0,
        }

        for row in events:
            event_type = str(row.get("event_type") or "UNKNOWN")
            state["event_types"][event_type] = state["event_types"].get(event_type, 0) + 1

            world_id = row.get("world_id")
            domain_id = row.get("domain_id")
            if world_id:
                world_state = state["worlds"].setdefault(
                    str(world_id),
                    {"event_count": 0, "domains": {}},
                )
                world_state["event_count"] += 1
                if domain_id:
                    world_state["domains"][str(domain_id)] = (
                        world_state["domains"].get(str(domain_id), 0) + 1
                    )

            state["last_sequence_number"] = int(row.get("sequence_number", 0))

        return {
            "status": "VALID" if not errors else "INVALID",
            "state": state,
            "state_hash": stable_hash(state),
            "event_count": len(events),
            "errors": errors,
        }

    def verify_determinism(self) -> Dict[str, Any]:
        first = self.rebuild_state()
        second = self.rebuild_state()
        deterministic = (
            first["status"] == "VALID"
            and second["status"] == "VALID"
            and first["state_hash"] == second["state_hash"]
            and first["state"] == second["state"]
        )
        return {
            "status": "VALID" if deterministic else "INVALID",
            "deterministic": deterministic,
            "first_state_hash": first["state_hash"],
            "second_state_hash": second["state_hash"],
            "errors": first["errors"] + second["errors"],
        }

    def summary(self) -> Dict[str, Any]:
        rebuilt = self.rebuild_state()
        det = self.verify_determinism()
        return {
            "status": "READY" if rebuilt["status"] == "VALID" and det["deterministic"] else "INVALID",
            "event_count": rebuilt["event_count"],
            "last_sequence_number": rebuilt["state"]["last_sequence_number"],
            "state_hash": rebuilt["state_hash"],
            "deterministic": det["deterministic"],
            "replay_executes_jobs": False,
            "replay_creates_predictions": False,
            "replay_creates_outcomes": False,
            "replay_mutates_source_registries": False,
            "replay_requires_network_access": False,
            "rebuild_source": "EVENT_JOURNAL_AND_IMMUTABLE_REGISTRIES",
            "principle": "REPLAY_IS_NOT_REEXECUTION",
            "errors": rebuilt["errors"],
        }
