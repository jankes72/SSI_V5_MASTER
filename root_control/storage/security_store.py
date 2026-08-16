
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS root_identity (
    root_identity_id TEXT PRIMARY KEY,
    password_salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    recovery_hash BLOB NOT NULL,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0,1))
);

CREATE TABLE IF NOT EXISTS bootstrap_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    permanently_closed INTEGER NOT NULL CHECK(permanently_closed IN (0,1))
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    root_identity_id TEXT NOT NULL,
    device_type TEXT NOT NULL,
    device_name TEXT NOT NULL,
    credential_fingerprint TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    trust_status TEXT NOT NULL,
    revoked_at TEXT,
    revocation_reason TEXT
);

CREATE TABLE IF NOT EXISTS enrollment_challenges (
    challenge_id TEXT PRIMARY KEY,
    root_identity_id TEXT NOT NULL,
    challenge_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    root_identity_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    assurance_level TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    idle_expires_at TEXT NOT NULL,
    revoked_at TEXT,
    rotation_parent TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_ref TEXT,
    object_ref TEXT,
    result TEXT NOT NULL
);
"""

class SecurityStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute(
            "INSERT OR IGNORE INTO bootstrap_state(singleton,permanently_closed) VALUES(1,0)"
        )
        self.db.commit()

    def close(self):
        self.db.close()

    def audit(self, created_at, event_type, actor_ref, object_ref, result):
        self.db.execute(
            "INSERT INTO audit(created_at,event_type,actor_ref,object_ref,result) VALUES(?,?,?,?,?)",
            (created_at,event_type,actor_ref,object_ref,result)
        )
        self.db.commit()
