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

    def test_indicator_substate_is_surfaced_without_wedging(self):
        # Issue #40: a degraded indicator (cursor ring unplaced) is
        # reported in the health block for diagnosis, but the probe
        # still passes, so it must not read as wedged or feed the
        # recovery/escalation accounting — only the probe boolean can.
        owner = ready_controller()
        owner.set_health_probe(
            lambda: True,
            indicator=lambda: {
                "state": "degraded",
                "detail": "cursor not locatable (win32 error 5)",
            },
        )
        health = owner.status()["health"]
        assert health["indicator"] == {
            "state": "degraded",
            "detail": "cursor not locatable (win32 error 5)",
        }
        assert health["wedged"] is False

    def test_indicator_substate_is_none_without_provider(self):
        owner = ready_controller()
        assert owner.status()["health"]["indicator"] is None


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


class TestLatchedUnavailableRecovery:
    """A transient fault can latch the coordinator in "unavailable" while
    every wedged signal stays green (watchdog alive, heartbeat fresh, no
    call in flight). The latch must still reach the recovery handler once
    it outlives the grace period, or only a manual server-process restart
    can free the channel."""

    def _latched(self, owner, *, age=120.0):
        with owner._lock:
            owner._set_locked("unavailable")
        owner._unavailable_since = time.monotonic() - age

    def test_latch_is_not_wedged_but_still_recovers(self):
        owner = ready_controller()
        owner.health_recovery = True
        calls = []
        owner.set_recovery_handler(lambda: calls.append(1) or "restarted")
        self._latched(owner)
        result = owner.status()
        assert result["health"]["wedged"] is False
        assert calls == [1]
        events = result["health"]["recovery_events"]
        assert len(events) == 1
        assert events[0]["trigger"] == "unavailable_latch"
        assert events[0]["outcome"] == "restarted"

    def test_latch_within_grace_period_does_not_recover(self):
        owner = ready_controller()
        owner.health_recovery = True
        calls = []
        owner.set_recovery_handler(lambda: calls.append(1) or "restarted")
        self._latched(owner, age=5.0)
        owner.status()
        assert calls == []

    def test_latch_recovery_respects_cooldown(self):
        owner = ready_controller()
        owner.health_recovery = True
        calls = []
        owner.set_recovery_handler(lambda: calls.append(1) or "restarted")
        self._latched(owner)
        owner.status()
        assert calls == [1]
        # Still latched with an old timestamp: the 120s cooldown, not the
        # grace period, is what bounds the retry rate.
        owner.status()
        assert calls == [1]

    def test_latch_does_not_recover_when_stopped(self):
        owner = ready_controller()
        owner.health_recovery = True
        calls = []
        owner.set_recovery_handler(lambda: calls.append(1) or "restarted")
        self._latched(owner)
        owner._stop.set()
        owner.status()
        assert calls == []

    def test_latch_does_not_recover_when_recovery_disabled(self):
        owner = ready_controller()
        calls = []
        owner.set_recovery_handler(lambda: calls.append(1) or "restarted")
        self._latched(owner)
        owner.status()
        assert calls == []

    def test_health_reports_emergency_and_pending_holds(self):
        owner = ready_controller()
        owner.input_ledger.enable()  # As begin_call does before any press.
        owner.input_ledger.press("key_a", lambda: None, lambda: None, lambda: False)
        try:
            health = owner.status()["health"]
            assert health["pending_holds"] == ["key_a"]
            assert health["emergency"] is False
        finally:
            owner.input_ledger.release_all()
        assert owner.status()["health"]["pending_holds"] == []
