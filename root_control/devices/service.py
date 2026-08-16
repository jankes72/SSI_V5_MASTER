
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

def utcnow():
    return datetime.now(timezone.utc)

class DeviceError(Exception):
    pass

class DeviceService:
    def __init__(self, store):
        self.store = store

    def create_challenge(self, root_identity_id, ttl_seconds=120):
        token = secrets.token_urlsafe(32)
        challenge_id = "enr_" + secrets.token_hex(12)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = utcnow() + timedelta(seconds=ttl_seconds)

        with self.store.db:
            self.store.db.execute(
                """INSERT INTO enrollment_challenges
                (challenge_id,root_identity_id,challenge_hash,expires_at)
                VALUES(?,?,?,?)""",
                (challenge_id,root_identity_id,token_hash,expires.isoformat())
            )

        return {
            "challenge_id": challenge_id,
            "challenge_secret_once": token,
            "expires_at": expires.isoformat()
        }

    def enroll(self, challenge_id, challenge_secret,
               device_type, device_name, public_credential):
        row = self.store.db.execute(
            "SELECT * FROM enrollment_challenges WHERE challenge_id=?",
            (challenge_id,)
        ).fetchone()

        if not row:
            raise DeviceError("UNKNOWN_CHALLENGE")
        if row["consumed_at"]:
            raise DeviceError("CHALLENGE_ALREADY_USED")
        if datetime.fromisoformat(row["expires_at"]) <= utcnow():
            raise DeviceError("CHALLENGE_EXPIRED")

        supplied = hashlib.sha256(
            challenge_secret.encode()
        ).hexdigest()

        if not secrets.compare_digest(
            supplied, row["challenge_hash"]
        ):
            raise DeviceError("WRONG_CHALLENGE")

        device_id = "dev_" + secrets.token_hex(12)
        fingerprint = hashlib.sha256(
            public_credential.encode()
        ).hexdigest()
        registered = utcnow().isoformat()

        with self.store.db:
            self.store.db.execute(
                """INSERT INTO devices
                (device_id,root_identity_id,device_type,device_name,
                 credential_fingerprint,registered_at,trust_status)
                VALUES(?,?,?,?,?,?,?)""",
                (device_id,row["root_identity_id"],device_type,
                 device_name,fingerprint,registered,"TRUSTED")
            )
            self.store.db.execute(
                "UPDATE enrollment_challenges SET consumed_at=? WHERE challenge_id=?",
                (registered,challenge_id)
            )

        return device_id

    def revoke(self, device_id, reason):
        revoked = utcnow().isoformat()
        with self.store.db:
            cur = self.store.db.execute(
                """UPDATE devices SET trust_status='REVOKED',
                   revoked_at=?, revocation_reason=? WHERE device_id=?""",
                (revoked,reason,device_id)
            )
            self.store.db.execute(
                "UPDATE sessions SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
                (revoked,device_id)
            )
        if not cur.rowcount:
            raise DeviceError("UNKNOWN_DEVICE")

    def trusted(self, device_id):
        row = self.store.db.execute(
            "SELECT trust_status FROM devices WHERE device_id=?",
            (device_id,)
        ).fetchone()
        return bool(row and row["trust_status"] == "TRUSTED")
