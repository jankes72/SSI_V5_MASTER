
import time
from datetime import datetime, timezone

class AuthenticationError(Exception):
    pass

class AuthenticationService:
    def __init__(self, store, bootstrap_service,
                 max_failures=5, backoff_seconds=0.01):
        self.store = store
        self.bootstrap = bootstrap_service
        self.max_failures = max_failures
        self.backoff_seconds = backoff_seconds
        self.failures = {}

    def authenticate(self, root_identity_id, device_id, password):
        key = (root_identity_id, device_id)
        failures = self.failures.get(key, 0)

        if failures >= self.max_failures:
            raise AuthenticationError("AUTHENTICATION_TEMPORARILY_BLOCKED")

        device = self.store.db.execute(
            """SELECT root_identity_id,trust_status
               FROM devices WHERE device_id=?""",
            (device_id,)
        ).fetchone()

        # Constant-shape external error.
        ok_device = bool(
            device and
            device["root_identity_id"] == root_identity_id and
            device["trust_status"] == "TRUSTED"
        )

        ok_password = self.bootstrap.verify_password(
            root_identity_id, password
        )

        if not (ok_device and ok_password):
            self.failures[key] = failures + 1
            time.sleep(self.backoff_seconds)
            raise AuthenticationError("AUTHENTICATION_FAILED")

        self.failures.pop(key, None)

        return {
            "root_identity_id": root_identity_id,
            "device_id": device_id,
            "assurance_level": "PRIMARY_AUTHENTICATED"
        }

    def require_step_up(self, auth_context, step_up_proof):
        if not step_up_proof:
            raise AuthenticationError("STEP_UP_REQUIRED")
        return {
            **auth_context,
            "assurance_level": "ELEVATED"
        }
