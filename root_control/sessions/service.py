
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

def utcnow():
    return datetime.now(timezone.utc)

class SessionError(Exception):
    pass

class SessionService:
    def __init__(self, store, idle_seconds=900, absolute_seconds=3600):
        self.store = store
        self.idle_seconds = idle_seconds
        self.absolute_seconds = absolute_seconds

    def issue(self, auth_context, rotation_parent=None):
        device = self.store.db.execute(
            "SELECT trust_status FROM devices WHERE device_id=?",
            (auth_context["device_id"],)
        ).fetchone()

        if not device or device["trust_status"] != "TRUSTED":
            raise SessionError("UNTRUSTED_DEVICE")

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        session_id = "ses_" + secrets.token_hex(12)
        issued = utcnow()
        expires = issued + timedelta(seconds=self.absolute_seconds)
        idle = issued + timedelta(seconds=self.idle_seconds)

        with self.store.db:
            self.store.db.execute(
                """INSERT INTO sessions
                (session_id,root_identity_id,device_id,token_hash,
                 assurance_level,issued_at,expires_at,idle_expires_at,
                 rotation_parent)
                 VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    auth_context["root_identity_id"],
                    auth_context["device_id"],
                    token_hash,
                    auth_context["assurance_level"],
                    issued.isoformat(),
                    expires.isoformat(),
                    idle.isoformat(),
                    rotation_parent
                )
            )

        return {
            "session_id": session_id,
            "session_token_once": raw_token,
            "expires_at": expires.isoformat()
        }

    def validate(self, token):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = self.store.db.execute(
            """SELECT s.*, d.trust_status
               FROM sessions s
               JOIN devices d ON d.device_id=s.device_id
               WHERE s.token_hash=?""",
            (token_hash,)
        ).fetchone()

        if not row:
            raise SessionError("INVALID_SESSION")
        if row["revoked_at"]:
            raise SessionError("REVOKED_SESSION")
        if row["trust_status"] != "TRUSTED":
            raise SessionError("REVOKED_DEVICE")

        current = utcnow()

        if datetime.fromisoformat(row["expires_at"]) <= current:
            raise SessionError("EXPIRED_SESSION")
        if datetime.fromisoformat(row["idle_expires_at"]) <= current:
            raise SessionError("IDLE_SESSION")

        return dict(row)

    def revoke(self, session_id):
        with self.store.db:
            self.store.db.execute(
                "UPDATE sessions SET revoked_at=? WHERE session_id=?",
                (utcnow().isoformat(), session_id)
            )

    def global_revoke(self, root_identity_id):
        with self.store.db:
            self.store.db.execute(
                """UPDATE sessions SET revoked_at=?
                   WHERE root_identity_id=? AND revoked_at IS NULL""",
                (utcnow().isoformat(), root_identity_id)
            )
