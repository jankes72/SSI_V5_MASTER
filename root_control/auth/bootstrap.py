
import hashlib
import hmac
import secrets
from datetime import datetime, timezone

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def scrypt_hash(secret: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        secret.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32
    )

class BootstrapError(Exception):
    pass

class RootBootstrapService:
    def __init__(self, store):
        self.store = store

    def bootstrap(self, password: str, *, local_physical_access: bool):
        if not local_physical_access:
            raise BootstrapError("LOCAL_PHYSICAL_ACCESS_REQUIRED")
        if len(password) < 14:
            raise BootstrapError("WEAK_CREDENTIAL")

        row = self.store.db.execute(
            "SELECT permanently_closed FROM bootstrap_state WHERE singleton=1"
        ).fetchone()
        if row["permanently_closed"]:
            raise BootstrapError("BOOTSTRAP_PERMANENTLY_CLOSED")

        existing = self.store.db.execute(
            "SELECT root_identity_id FROM root_identity WHERE active=1"
        ).fetchone()
        if existing:
            raise BootstrapError("ACTIVE_ROOT_ALREADY_EXISTS")

        root_id = "root_" + secrets.token_hex(12)
        salt = secrets.token_bytes(16)
        password_hash = scrypt_hash(password, salt)

        recovery_secret = secrets.token_urlsafe(32)
        recovery_hash = hashlib.sha256(
            recovery_secret.encode("utf-8")
        ).digest()

        created = utcnow()

        with self.store.db:
            self.store.db.execute(
                """INSERT INTO root_identity
                (root_identity_id,password_salt,password_hash,recovery_hash,created_at,active)
                VALUES(?,?,?,?,?,1)""",
                (root_id,salt,password_hash,recovery_hash,created)
            )
            self.store.db.execute(
                "UPDATE bootstrap_state SET permanently_closed=1 WHERE singleton=1"
            )

        self.store.audit(
            created, "ROOT_BOOTSTRAP", root_id, root_id, "SUCCESS"
        )

        # Recovery secret is returned once; never persisted in plaintext.
        return {
            "root_identity_id": root_id,
            "recovery_material_once": recovery_secret
        }

    def verify_password(self, root_identity_id: str, password: str) -> bool:
        row = self.store.db.execute(
            """SELECT password_salt,password_hash
               FROM root_identity
               WHERE root_identity_id=? AND active=1""",
            (root_identity_id,)
        ).fetchone()
        if not row:
            return False
        calculated = scrypt_hash(password, row["password_salt"])
        return hmac.compare_digest(calculated, row["password_hash"])
