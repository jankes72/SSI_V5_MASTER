
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import sqlite3
import time


@dataclass(frozen=True)
class DirectorCheckpoint:
    checkpoint_id: str
    last_event_sequence: int
    updated_unix: int


class DirectorCatchup:
    """
    Director event catch-up over the world event journal.

    Guarantees:
      * reads only events with sequence_number > last_event_sequence
      * does not execute world cycles
      * does not dispatch compute jobs
      * checkpoint advancement is explicit
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.events_db = self.root / "events" / "world_event_journal.sqlite3"
        self.state_db = self.root / "director" / "director_catchup.sqlite3"
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.state_db)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS director_checkpoints(
                checkpoint_id TEXT PRIMARY KEY,
                last_event_sequence INTEGER NOT NULL,
                updated_unix INTEGER NOT NULL
            )
            """
        )
        self.conn.commit()

    def checkpoint(self, checkpoint_id: str = "DIRECTOR_MAIN") -> DirectorCheckpoint:
        row = self.conn.execute(
            "SELECT * FROM director_checkpoints WHERE checkpoint_id=?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            return DirectorCheckpoint(checkpoint_id, 0, 0)
        return DirectorCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            last_event_sequence=int(row["last_event_sequence"]),
            updated_unix=int(row["updated_unix"]),
        )

    def read_new_events(
        self,
        *,
        checkpoint_id: str = "DIRECTOR_MAIN",
        limit: int = 1000,
    ) -> Dict[str, Any]:
        cp = self.checkpoint(checkpoint_id)
        if not self.events_db.exists():
            return {
                "checkpoint_id": checkpoint_id,
                "from_sequence_exclusive": cp.last_event_sequence,
                "events": [],
                "event_count": 0,
                "max_sequence": cp.last_event_sequence,
            }

        conn = sqlite3.connect(f"file:{self.events_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM world_events
                    WHERE sequence_number > ?
                    ORDER BY sequence_number
                    LIMIT ?
                    """,
                    (cp.last_event_sequence, int(limit)),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT * FROM events
                    WHERE sequence_number > ?
                    ORDER BY sequence_number
                    LIMIT ?
                    """,
                    (cp.last_event_sequence, int(limit)),
                ).fetchall()
        finally:
            conn.close()

        events = [dict(r) for r in rows]
        max_sequence = cp.last_event_sequence
        if events:
            max_sequence = max(int(e["sequence_number"]) for e in events)

        return {
            "checkpoint_id": checkpoint_id,
            "from_sequence_exclusive": cp.last_event_sequence,
            "events": events,
            "event_count": len(events),
            "max_sequence": max_sequence,
        }

    def advance_checkpoint(
        self,
        *,
        checkpoint_id: str,
        new_sequence: int,
    ) -> DirectorCheckpoint:
        current = self.checkpoint(checkpoint_id)
        if int(new_sequence) < current.last_event_sequence:
            raise ValueError("checkpoint cannot move backwards")

        now = int(time.time())
        self.conn.execute(
            """
            INSERT INTO director_checkpoints(checkpoint_id,last_event_sequence,updated_unix)
            VALUES(?,?,?)
            ON CONFLICT(checkpoint_id) DO UPDATE SET
                last_event_sequence=excluded.last_event_sequence,
                updated_unix=excluded.updated_unix
            """,
            (checkpoint_id, int(new_sequence), now),
        )
        self.conn.commit()
        return self.checkpoint(checkpoint_id)

    def catch_up_once(
        self,
        *,
        checkpoint_id: str = "DIRECTOR_MAIN",
        limit: int = 1000,
        advance: bool = False,
    ) -> Dict[str, Any]:
        batch = self.read_new_events(checkpoint_id=checkpoint_id, limit=limit)
        advanced_to = self.checkpoint(checkpoint_id).last_event_sequence

        if advance and batch["event_count"] > 0:
            advanced_to = self.advance_checkpoint(
                checkpoint_id=checkpoint_id,
                new_sequence=batch["max_sequence"],
            ).last_event_sequence

        return {
            "status": "READY",
            "checkpoint_id": checkpoint_id,
            "event_count": batch["event_count"],
            "from_sequence_exclusive": batch["from_sequence_exclusive"],
            "max_sequence": batch["max_sequence"],
            "checkpoint_after": advanced_to,
            "world_start_triggered": False,
            "compute_dispatch_triggered": False,
            "director_output_executed": False,
            "advance_requested": bool(advance),
        }

    @staticmethod
    def schedule_contract() -> Dict[str, Any]:
        return {
            "status": "READY",
            "manual_world_start": True,
            "director_may_recommend_schedule": True,
            "director_may_start_worlds_autonomously": False,
            "cron_required": False,
            "systemd_timer_required": False,
            "catchup_reads_only_new_events": True,
            "checkpoint_advancement_explicit": True,
        }
