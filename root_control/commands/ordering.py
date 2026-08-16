
import hashlib
import json
import re

class RootCommandOrderingService:
    def __init__(self, registry):
        self.registry = registry

    @staticmethod
    def payload_hash(payload):
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":")
        ).encode("utf-8")

        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def priority_rank(priority):
        if isinstance(priority, int):
            return priority

        m = re.fullmatch(r"P([0-9])", str(priority))
        if not m:
            raise ValueError("INVALID_PRIORITY")

        return int(m.group(1))

    @classmethod
    def ordering_key(cls, command):
        emergency = (
            0
            if command.get("command_type") == "EMERGENCY_STOP"
            else 1
        )

        return (
            emergency,
            cls.priority_rank(command["priority"]),
            command["created_at"],
            command["command_id"],
        )

    def order(self, commands):
        return sorted(commands, key=self.ordering_key)

    def record_idempotent_operation(
        self,
        *,
        operation_type,
        idempotency_key,
        payload,
        result_ref,
    ):
        return self.registry.record_operation(
            operation_type=operation_type,
            idempotency_key=idempotency_key,
            payload_hash=self.payload_hash(payload),
            result_ref=result_ref,
        )

    def supersedes(self, command_id, older_command_id):
        self.registry.add_relationship(
            command_id,
            older_command_id,
            "SUPERSEDES",
        )

    def cancels(self, command_id, target_command_id):
        self.registry.add_relationship(
            command_id,
            target_command_id,
            "CANCELS",
        )
