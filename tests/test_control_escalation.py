"""Issue #25 (half B) — escalation to process restart when in-process
recovery cannot clear a control-channel fault.

Thread restarts (the #8 recovery handler) get a bounded record: after
repeated failed attempts, or after the latch keeps re-tripping despite
successful restarts, the coordinator records a
"process_restart_escalation" recovery event and invokes the registered
escalation handler so the external supervisor rebuilds the process.
Escalation is opt-in with health_recovery, bounded by the same
cooldown, and never fires while a call is active.
"""

import time

from agentic_windows_mcp.desktop import control


def ready_controller() -> control.ControlCoordinator:
    owner = control.ControlCoordinator()
    with owner._lock:
        owner._set_locked("ready")
    return owner


def _wedged(owner):
    # Stale monitor heartbeat: the wedged verdict with no call in flight.
    owner._thread_beat = time.monotonic() - 60.0


def _latched(owner, *, age=120.0):
    with owner._lock:
        owner._set_locked("unavailable")
    owner._unavailable_since = time.monotonic() - age


def _arm(owner, handler):
    owner.health_recovery = True
    owner.set_recovery_handler(handler)
    escalations = []
    owner.set_escalation_handler(escalations.append)
    return escalations


def _attempt(owner):
    # Bypass the 120s recovery cooldown between simulated attempts.
    owner._last_recovery_at = 0.0
    return owner.status()


def _failing_handler(calls):
    def handler():
        calls.append(1)
        raise RuntimeError("Physical input monitor did not stop cleanly")

    return handler


class TestFailureEscalation:
    def test_repeated_failures_escalate_instead_of_retrying(self):
        owner = ready_controller()
        calls = []
        escalations = _arm(owner, _failing_handler(calls))
        _wedged(owner)
        _attempt(owner)
        _attempt(owner)
        assert calls == [1, 1]
        assert escalations == []
        result = _attempt(owner)
        # The third trigger escalates rather than spending a third
        # thread restart on a fault restarts cannot clear.
        assert calls == [1, 1]
        assert escalations == ["repeated_recovery_failures"]
        events = result["health"]["recovery_events"]
        escalation = events[-1]
        assert escalation["action"] == "process_restart_escalation"
        assert escalation["reason"] == "repeated_recovery_failures"
        assert escalation["trigger"] == "wedged"
        assert "error" in events[0]["outcome"]

    def test_escalation_is_recorded_even_without_an_exit_handler(self):
        owner = ready_controller()
        owner.health_recovery = True
        calls = []
        owner.set_recovery_handler(_failing_handler(calls))
        _wedged(owner)
        _attempt(owner)
        _attempt(owner)
        result = _attempt(owner)
        assert result["health"]["escalation_armed"] is False
        actions = [e["action"] for e in result["health"]["recovery_events"]]
        assert "process_restart_escalation" in actions

    def test_stale_failures_fall_out_of_the_window(self):
        owner = ready_controller()
        calls = []
        escalations = _arm(owner, _failing_handler(calls))
        _wedged(owner)
        _attempt(owner)
        # The one failure ages out of the escalation window, so the
        # record restarts: two fresh failures are needed to escalate.
        owner._failed_recovery_at = [time.monotonic() - 2000.0]
        _attempt(owner)
        _attempt(owner)
        assert escalations == []
        assert len(calls) == 3
        _attempt(owner)
        assert escalations == ["repeated_recovery_failures"]


class TestLatchRetripEscalation:
    def test_latch_retrips_despite_successful_restarts_escalate(self):
        owner = ready_controller()
        calls = []
        escalations = _arm(owner, lambda: calls.append(1) or "restarted")
        _latched(owner)
        for _ in range(3):
            _attempt(owner)
        assert len(calls) == 3
        assert escalations == []
        result = _attempt(owner)
        assert len(calls) == 3  # No fourth restart is attempted.
        assert escalations == ["latch_retrips"]
        escalation = result["health"]["recovery_events"][-1]
        assert escalation["action"] == "process_restart_escalation"
        assert escalation["reason"] == "latch_retrips"
        assert escalation["trigger"] == "unavailable_latch"


class TestDeferral:
    def test_escalation_never_fires_while_a_call_is_active(self):
        owner = ready_controller()
        calls = []
        escalations = _arm(owner, _failing_handler(calls))
        _wedged(owner)
        _attempt(owner)
        _attempt(owner)
        # A call is now in flight: the earned escalation must defer.
        owner._active_calls = 1
        owner._call_name = "Click"
        owner._call_started_at = time.monotonic()
        result = _attempt(owner)
        assert escalations == []
        assert len(calls) == 2  # No further restart attempt either.
        assert result["health"]["escalation_pending"] is True
        # Once the call drains, the next status poll fires it.
        owner._active_calls = 0
        owner._call_name = None
        owner._call_started_at = 0.0
        result = owner.status()
        assert escalations == ["repeated_recovery_failures"]
        assert result["health"]["escalation_pending"] is False
        escalation = result["health"]["recovery_events"][-1]
        assert escalation["action"] == "process_restart_escalation"
        assert escalation["trigger"] == "deferred"


class TestOptIn:
    def test_no_escalation_without_health_recovery(self):
        owner = ready_controller()
        calls = []
        owner.set_recovery_handler(_failing_handler(calls))
        escalations = []
        owner.set_escalation_handler(escalations.append)
        _wedged(owner)
        for _ in range(4):
            _attempt(owner)
        assert calls == []
        assert escalations == []
        assert owner.status()["health"]["recovery_events"] == []

    def test_health_reports_escalation_arming(self):
        owner = ready_controller()
        health = owner.status()["health"]
        assert health["escalation_armed"] is False
        assert health["escalation_pending"] is False
        _arm(owner, lambda: "restarted")
        health = owner.status()["health"]
        assert health["escalation_armed"] is True
