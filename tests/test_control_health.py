"""Issue #8 — ControlStatus channel health: call timing, wedged verdict,
and opt-in scoped recovery."""

import time

import pytest

from agentic_windows_mcp.desktop import control


def ready_controller() -> control.ControlCoordinator:
    owner = control.ControlCoordinator()
    with owner._lock:
        owner._set_locked("ready")
    return owner


class TestHealthBlock:
    def test_status_includes_health_when_idle(self):
        owner = ready_controller()
        health = owner.status()["health"]
        assert health["active_calls"] == 0
        assert health["current_call"] is None
        assert health["wedged"] is False
        assert health["recovery_enabled"] is False
        assert health["recovery_events"] == []

    def test_call_timing_is_recorded(self):
        owner = ready_controller()
        token = owner.begin_call("Click")
        try:
            health = owner.status()["health"]
            assert health["active_calls"] == 1
            assert health["current_call"] == "Click"
        finally:
            owner.end_call(token)
        health = owner.status()["health"]
        assert health["active_calls"] == 0
        assert health["last_call"] == "Click"
        assert health["last_call_seconds"] is not None
        assert health["last_call_seconds"] >= 0

    def test_long_in_flight_call_is_wedged(self):
        owner = ready_controller()
        token = owner.begin_call("Snapshot")
        try:
            owner._call_started_at = time.monotonic() - 200.0
            health = owner.status()["health"]
            assert health["call_age_seconds"] > 120.0
            assert health["wedged"] is True
        finally:
            owner.end_call(token)

    def test_stale_heartbeat_is_wedged(self):
        owner = ready_controller()
        owner._thread_beat = time.monotonic() - 60.0
        assert owner.status()["health"]["wedged"] is True

    def test_disabled_controller_is_never_wedged(self):
        owner = control.ControlCoordinator()
        owner.enabled = False
        owner._thread_beat = time.monotonic() - 600.0
        assert owner.status()["health"]["wedged"] is False


class TestRecovery:
    def _wedged(self, owner):
        owner._call_name = "Snapshot"
        owner._active_calls = 1
        owner._call_started_at = time.monotonic() - 200.0

    def test_recovery_off_by_default(self):
        owner = ready_controller()
        calls = []
        owner.set_recovery_handler(lambda: calls.append(1) or "ok")
        self._wedged(owner)
        owner.status()
        assert calls == []
        assert owner.status()["health"]["recovery_events"] == []

    def test_recovery_runs_once_when_enabled_and_is_surfaced(self):
        owner = ready_controller()
        owner.health_recovery = True
        calls = []
        owner.set_recovery_handler(lambda: calls.append(1) or "restarted")
        self._wedged(owner)
        result = owner.status()
        assert calls == [1]
        events = result["health"]["recovery_events"]
        assert len(events) == 1
        assert events[0]["outcome"] == "restarted"
        # Cooldown: a second wedged status does not retry immediately.
        owner.status()
        assert calls == [1]

    def test_recovery_failure_is_recorded_not_raised(self):
        owner = ready_controller()
        owner.health_recovery = True

        def boom():
            raise RuntimeError("nope")

        owner.set_recovery_handler(boom)
        self._wedged(owner)
        result = owner.status()
        events = result["health"]["recovery_events"]
        assert len(events) == 1
        assert "error" in events[0]["outcome"]
