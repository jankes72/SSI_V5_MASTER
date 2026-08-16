
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta(
    schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS commands(
    command_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,

    root_identity_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    source_channel TEXT NOT NULL,

    original_content TEXT NOT NULL,
    payload_hash TEXT NOT NULL,

    command_type TEXT,
    priority TEXT,
    risk_class TEXT,
    required_approval INTEGER NOT NULL DEFAULT 0,
    ambiguity TEXT,

    target_scope TEXT,
    target_agent_id TEXT,
    target_world_id TEXT,
    target_domain_id TEXT,

    status TEXT NOT NULL,
    version INTEGER NOT NULL,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS derived_versions(
    derived_id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    source_payload_hash TEXT NOT NULL,
    UNIQUE(command_id, version),
    FOREIGN KEY(command_id) REFERENCES commands(command_id)
);

CREATE TABLE IF NOT EXISTS relationships(
    relationship_id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL,
    related_command_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    UNIQUE(command_id, related_command_id, relation_type)
);

CREATE TABLE IF NOT EXISTS approvals(
    approval_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    proposal_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clarifications(
    clarification_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    command_version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes(
    outcome_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    outcome_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_refs(
    audit_ref TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_idempotency(
    operation_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    result_ref TEXT NOT NULL,
    PRIMARY KEY(operation_type, idempotency_key)
);
"""

class RegistryError(Exception):
    pass

class IdempotencyConflict(RegistryError):
    pass

class ConcurrentUpdate(RegistryError):
    pass

class CommandRegistry:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._db.row_factory = sqlite3.Row

        with self._lock:
            self._db.executescript(SCHEMA)

            row = self._db.execute(
                "SELECT COUNT(*) AS c FROM schema_meta"
            ).fetchone()

            if row["c"] == 0:
                self._db.execute(
                    "INSERT INTO schema_meta(schema_version) VALUES(?)",
                    (SCHEMA_VERSION,),
                )

    def close(self):
        self._db.close()

    @contextmanager
    def transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def persist_intake(self, record):
        with self.transaction() as db:
            old = db.execute(
                """
                SELECT * FROM commands
                WHERE idempotency_key=?
                """,
                (record["idempotency_key"],),
            ).fetchone()

            if old:
                if old["payload_hash"] != record["payload_hash"]:
                    raise IdempotencyConflict(
                        "IDEMPOTENCY_CONTENT_CONFLICT"
                    )
                return dict(old), True

            created_at = record.get("created_at")
            if not created_at:
                from datetime import datetime, timezone
                created_at = datetime.now(timezone.utc).isoformat()

            db.execute(
                """
                INSERT INTO commands(
                    command_id,
                    receipt_id,
                    idempotency_key,
                    root_identity_id,
                    session_id,
                    device_id,
                    source_channel,
                    original_content,
                    payload_hash,
                    target_scope,
                    target_agent_id,
                    target_world_id,
                    target_domain_id,
                    status,
                    version,
                    created_at,
                    updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["command_id"],
                    record["receipt_id"],
                    record["idempotency_key"],
                    record["root_identity_id"],
                    record["session_id"],
                    record["device_id"],
                    record["source_channel"],
                    record["original_content"],
                    record["payload_hash"],
                    record.get("target_scope"),
                    record.get("target_agent_id"),
                    record.get("target_world_id"),
                    record.get("target_domain_id"),
                    "RECEIVED",
                    1,
                    created_at,
                    created_at,
                ),
            )

            row = db.execute(
                "SELECT * FROM commands WHERE command_id=?",
                (record["command_id"],),
            ).fetchone()

            return dict(row), False

    def get(self, command_id):
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_by_idempotency(self, key):
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM commands WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            return dict(row) if row else None

    def all_commands(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM commands ORDER BY command_id"
            ).fetchall()
            return [dict(x) for x in rows]

    def transition(
        self,
        command_id,
        *,
        expected_version,
        new_status,
        updated_at,
    ):
        with self.transaction() as db:
            row = db.execute(
                """
                SELECT version FROM commands
                WHERE command_id=?
                """,
                (command_id,),
            ).fetchone()

            if row is None:
                raise KeyError(command_id)

            if row["version"] != expected_version:
                raise ConcurrentUpdate("STALE_VERSION")

            cur = db.execute(
                """
                UPDATE commands
                SET status=?,
                    version=version+1,
                    updated_at=?
                WHERE command_id=? AND version=?
                """,
                (
                    new_status,
                    updated_at,
                    command_id,
                    expected_version,
                ),
            )

            if cur.rowcount != 1:
                raise ConcurrentUpdate("CONCURRENT_UPDATE")

        return self.get(command_id)

    def record_operation(
        self,
        *,
        operation_type,
        idempotency_key,
        payload_hash,
        result_ref,
    ):
        with self.transaction() as db:
            row = db.execute(
                """
                SELECT * FROM operation_idempotency
                WHERE operation_type=? AND idempotency_key=?
                """,
                (operation_type, idempotency_key),
            ).fetchone()

            if row:
                if row["payload_hash"] != payload_hash:
                    raise IdempotencyConflict(
                        "OPERATION_CONTENT_CONFLICT"
                    )
                return dict(row), True

            db.execute(
                """
                INSERT INTO operation_idempotency(
                    operation_type,
                    idempotency_key,
                    payload_hash,
                    result_ref
                )
                VALUES(?,?,?,?)
                """,
                (
                    operation_type,
                    idempotency_key,
                    payload_hash,
                    result_ref,
                ),
            )

            return {
                "operation_type": operation_type,
                "idempotency_key": idempotency_key,
                "payload_hash": payload_hash,
                "result_ref": result_ref,
            }, False

    def add_relationship(
        self,
        command_id,
        related_command_id,
        relation_type,
    ):
        if relation_type not in {"SUPERSEDES", "CANCELS"}:
            raise ValueError("INVALID_RELATIONSHIP")

        with self.transaction() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO relationships(
                    command_id,
                    related_command_id,
                    relation_type
                )
                VALUES(?,?,?)
                """,
                (
                    command_id,
                    related_command_id,
                    relation_type,
                ),
            )

    def integrity_check(self):
        with self._lock:
            result = self._db.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            return result == "ok"

    @staticmethod
    def file_integrity_check(path):
        try:
            db = sqlite3.connect(str(path))
            result = db.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            db.close()
            return result == "ok"
        except sqlite3.DatabaseError:
            return False
