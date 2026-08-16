
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol


class StatusSource(Protocol):
    def read_status(self) -> dict | None:
        ...


class StatusPermissionDenied(Exception):
    pass


@dataclass(frozen=True)
class SystemStatusView:
    runtime_availability: str
    cycle_id: str | None
    phase: str | None
    cycle_started_at: str | None
    cycle_ends_at: str | None
    remaining_seconds: int | None
    active_command_id: str | None
    safe_mode: bool | None
    adapter_mode: str
    last_update: str | None
    freshness: str
    source_kind: str

    def to_dict(self):
        return asdict(self)


class ProgrammerRootSystemStatusService:
    """
    Stage 03 read model.

    It never reads Director/Runtime files directly.
    It consumes only an injected approved status source.

    Before Stage 04 the expected sources are SIMULATOR or TEST.
    """

    ALLOWED_PRE_STAGE4_SOURCES = {"SIMULATOR", "TEST"}

    def __init__(
        self,
        source: StatusSource,
        *,
        authorize: Callable[[dict], bool],
        stale_after_seconds: int = 30,
        now_fn=None,
    ):
        self.source = source
        self.authorize = authorize
        self.stale_after_seconds = stale_after_seconds
        self.now_fn = now_fn or (
            lambda: datetime.now(timezone.utc)
        )

    @staticmethod
    def _parse_time(value):
        if not value:
            return None

        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed

    def get_status(self, request_context: dict) -> SystemStatusView:
        if not self.authorize(request_context):
            raise StatusPermissionDenied("STATUS_READ_DENIED")

        try:
            raw = self.source.read_status()
        except (ConnectionError, TimeoutError):
            return SystemStatusView(
                runtime_availability="UNAVAILABLE",
                cycle_id=None,
                phase=None,
                cycle_started_at=None,
                cycle_ends_at=None,
                remaining_seconds=None,
                active_command_id=None,
                safe_mode=None,
                adapter_mode="OFF",
                last_update=None,
                freshness="OFFLINE",
                source_kind="UNAVAILABLE",
            )

        if raw is None:
            return SystemStatusView(
                runtime_availability="UNKNOWN",
                cycle_id=None,
                phase=None,
                cycle_started_at=None,
                cycle_ends_at=None,
                remaining_seconds=None,
                active_command_id=None,
                safe_mode=None,
                adapter_mode="OFF",
                last_update=None,
                freshness="UNKNOWN",
                source_kind="UNKNOWN",
            )

        source_kind = str(raw.get("source_kind", "UNKNOWN"))

        if source_kind not in self.ALLOWED_PRE_STAGE4_SOURCES:
            raise ValueError(
                "LIVE_OR_UNAPPROVED_STATUS_SOURCE_FORBIDDEN"
            )

        updated_at = self._parse_time(raw.get("last_update"))

        if updated_at is None:
            freshness = "UNKNOWN"
        else:
            age = (self.now_fn() - updated_at).total_seconds()
            freshness = (
                "STALE"
                if age > self.stale_after_seconds
                else "CONFIRMED"
            )

        return SystemStatusView(
            runtime_availability=str(
                raw.get("runtime_availability", "UNKNOWN")
            ),
            cycle_id=raw.get("cycle_id"),
            phase=raw.get("phase"),
            cycle_started_at=raw.get("cycle_started_at"),
            cycle_ends_at=raw.get("cycle_ends_at"),
            remaining_seconds=raw.get("remaining_seconds"),
            active_command_id=raw.get("active_command_id"),
            safe_mode=raw.get("safe_mode"),
            adapter_mode=str(raw.get("adapter_mode", "OFF")),
            last_update=raw.get("last_update"),
            freshness=freshness,
            source_kind=source_kind,
        )
