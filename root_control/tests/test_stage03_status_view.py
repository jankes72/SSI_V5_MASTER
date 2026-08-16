
from datetime import datetime, timezone
import unittest

from root_control.status.read_model import (
    ProgrammerRootSystemStatusService,
    StatusPermissionDenied,
)


NOW = datetime(
    2026, 8, 16, 12, 0, 0,
    tzinfo=timezone.utc
)


class FakeSource:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def read_status(self):
        if self.error:
            raise self.error
        return self.value


def base_status(last_update):
    return {
        "source_kind": "SIMULATOR",
        "runtime_availability": "SIMULATED_AVAILABLE",
        "cycle_id": "cycle-test-001",
        "phase": "PLANNING",
        "cycle_started_at": "2026-08-16T11:59:00+00:00",
        "cycle_ends_at": "2026-08-16T12:09:00+00:00",
        "remaining_seconds": 540,
        "active_command_id": "cmd-test-001",
        "safe_mode": False,
        "adapter_mode": "SIMULATOR_ONLY",
        "last_update": last_update,
    }


class StatusViewTests(unittest.TestCase):

    def make_service(self, source, authorize=lambda ctx: True):
        return ProgrammerRootSystemStatusService(
            source,
            authorize=authorize,
            stale_after_seconds=30,
            now_fn=lambda: NOW,
        )

    def test_fresh_status_is_confirmed(self):
        svc = self.make_service(
            FakeSource(
                base_status(
                    "2026-08-16T11:59:50+00:00"
                )
            )
        )

        result = svc.get_status({"profile": "LOCAL"})

        self.assertEqual(result.freshness, "CONFIRMED")
        self.assertEqual(result.cycle_id, "cycle-test-001")
        self.assertEqual(
            result.adapter_mode,
            "SIMULATOR_ONLY"
        )

    def test_stale_status_is_stale(self):
        svc = self.make_service(
            FakeSource(
                base_status(
                    "2026-08-16T11:50:00+00:00"
                )
            )
        )

        result = svc.get_status({"profile": "LOCAL"})

        self.assertEqual(result.freshness, "STALE")

    def test_unavailable_source_is_offline(self):
        svc = self.make_service(
            FakeSource(error=ConnectionError("offline"))
        )

        result = svc.get_status({"profile": "LOCAL"})

        self.assertEqual(result.freshness, "OFFLINE")
        self.assertEqual(
            result.runtime_availability,
            "UNAVAILABLE"
        )

    def test_missing_source_state_is_unknown(self):
        svc = self.make_service(FakeSource(None))

        result = svc.get_status({"profile": "LOCAL"})

        self.assertEqual(result.freshness, "UNKNOWN")

    def test_permission_denied(self):
        svc = self.make_service(
            FakeSource(base_status(
                "2026-08-16T11:59:50+00:00"
            )),
            authorize=lambda ctx: False,
        )

        with self.assertRaises(StatusPermissionDenied):
            svc.get_status({"profile": "LOCAL"})

    def test_local_mobile_parity(self):
        raw = base_status(
            "2026-08-16T11:59:50+00:00"
        )

        svc = self.make_service(FakeSource(raw))

        local = svc.get_status(
            {"profile": "LOCAL"}
        ).to_dict()

        mobile = svc.get_status(
            {"profile": "MOBILE"}
        ).to_dict()

        self.assertEqual(local, mobile)

    def test_live_source_is_forbidden_before_stage4(self):
        raw = base_status(
            "2026-08-16T11:59:50+00:00"
        )
        raw["source_kind"] = "LIVE_RUNTIME"

        svc = self.make_service(FakeSource(raw))

        with self.assertRaises(ValueError):
            svc.get_status({"profile": "LOCAL"})


if __name__ == "__main__":
    unittest.main()
