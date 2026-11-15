"""Issue #41 — the indicator ack timeout ("did not acknowledge state
change") manufactured a sticky error that latched the control channel
for ~2 hours on 15/11/2026, while six in-process recoveries restarted
only the input-monitor threads and never touched the overlay that owns
the fault, and the #25 escalation ladder never fired because it only
advanced when someone polled ControlStatus.

The fix pinned here: the error is classified at the source (origin:
ack timeout vs display thread), the health surface reports the full
signature instead of masking it behind the stored error, recovery
restarts the overlay stop-then-start with confirmed teardown, and the
recovery/escalation ladder advances on its own cadence. Confirmation
at lease time is unchanged — an unacknowledged change still fails the
call, and an unacked hide still blocks a capture; only the fault's
permanence is reclassified, at recovery time.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentic_windows_mcp.desktop import control, control_overlay


@pytest.fixture(autouse=True)
def _stop_indicator():
    control_overlay.stop()
    yield
    control_overlay.stop()


class ControllableIndicator(control_overlay._Indicator):
    """A real _Indicator whose display thread is a simple ack loop.

    With ack_enabled=False it reproduces the 15/11 shape mechanically:
    the thread stays alive and keeps beating, but never applies a
    version, so change()'s acknowledgement wait expires.
    """

    def __init__(self):
        super().__init__()
        self.ack_enabled = True
        self.thread = threading.Thread(
            target=self._fake_run, name="fake-control-indicator", daemon=True
        )

    def _fake_run(self):
        self.heartbeat = time.monotonic()
        self.started.set()
        while True:
            with self.condition:
                self.condition.wait(timeout=0.02)
                if self.stopping:
                    return
                if self.ack_enabled:
                    self.applied = self.version
                self.heartbeat = time.monotonic()
                self.condition.notify_all()


@pytest.fixture
def controllable(monkeypatch):
    monkeypatch.setattr(control_overlay, "_Indicator", ControllableIndicator)
    monkeypatch.setattr(control_overlay, "_ACK_TIMEOUT_SECONDS", 0.3)
    control_overlay.start()
    indicator = control_overlay._instance
    assert indicator is not None
    yield indicator
    control_overlay.stop()


def _namespace_indicator(**overrides):
    """A partial _Indicator surface for signature-level classification
    tests (health reporting must tolerate partial fakes)."""
    fields = {
        "thread": threading.current_thread(),
        "stopping": False,
        "error": None,
        "error_origin": None,
        "heartbeat": time.monotonic(),
        "applied": 0,
        "version": 0,
        "degraded": None,
        "stop": lambda: None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def ready_controller() -> control.ControlCoordinator:
    owner = control.ControlCoordinator()
    with owner._lock:
        owner._set_locked("ready")
    return owner


def _latched(owner, *, age=120.0):
    with owner._lock:
        owner._set_locked("unavailable")
    owner._unavailable_since = time.monotonic() - age


def _tick(owner):
    # Bypass the 120 s recovery cooldown between simulated ticks.
    owner._last_recovery_at = 0.0
    return owner.recovery_tick()


class TestClassificationAtTheSource:
    def test_ack_timeout_records_origin_and_classifies_a(self, controllable):
        indicator = controllable
        indicator.ack_enabled = False
        with pytest.raises(RuntimeError, match="did not acknowledge"):
            control_overlay.set_active(True)
        # The error was manufactured by the caller's timeout, and the
        # origin is recorded where it happened — not inferred later.
        assert indicator.error_origin == "ack_timeout"
        status = control_overlay.indicator_status()
        assert status["state"] == "failed"
        assert status["error_origin"] == "ack_timeout"
        assert status["thread_alive"] is True
        assert status["applied"] == 0
        assert status["version"] == 1
        assert status["heartbeat_age_seconds"] is not None
        assert (
            status["heartbeat_age_seconds"]
            <= control_overlay._HEARTBEAT_FRESH_SECONDS
        )
        # Alive + beating + ack-origin error: Class A.
        assert status["fault_class"] == "A"
        # The detail no longer masks the live thread behind the stored
        # error (pre-#41 it returned the error alone, forever).
        detail = control_overlay.health_detail()
        assert detail is not None
        assert "did not acknowledge" in detail
        assert "origin: ack timeout" in detail
        assert "thread alive" in detail
        assert "applied 0 of version 1" in detail
        assert control_overlay.is_healthy() is False

    def test_thread_error_classifies_b(self, monkeypatch):
        fake = _namespace_indicator(
            error=RuntimeError("window failed"),
            error_origin="thread",
            applied=3,
            version=3,
        )
        monkeypatch.setattr(control_overlay, "_instance", fake)
        status = control_overlay.indicator_status()
        assert status["state"] == "failed"
        assert status["fault_class"] == "B"
        assert status["error_origin"] == "thread"
        detail = control_overlay.health_detail()
        assert detail is not None
        assert "window failed" in detail
        assert "origin: display thread" in detail

    def test_stalled_heartbeat_classifies_b(self, monkeypatch):
        fake = _namespace_indicator(heartbeat=time.monotonic() - 30.0)
        monkeypatch.setattr(control_overlay, "_instance", fake)
        status = control_overlay.indicator_status()
        assert status["state"] == "failed"
        assert status["fault_class"] == "B"
        assert status["error_origin"] is None
        assert "heartbeat stale" in (control_overlay.health_detail() or "")

    def test_ack_origin_with_stalled_heartbeat_reclassifies_b(self, monkeypatch):
        # Classification reads the signature at decision time, not the
        # origin alone: an ack timeout whose thread has since stalled
        # is not Class A — its heartbeat is not advancing.
        fake = _namespace_indicator(
            error=RuntimeError(
                "AI control indicator did not acknowledge state change"
            ),
            error_origin="ack_timeout",
            heartbeat=time.monotonic() - 30.0,
        )
        monkeypatch.setattr(control_overlay, "_instance", fake)
        status = control_overlay.indicator_status()
        assert status["state"] == "failed"
        assert status["fault_class"] == "B"


class TestCaptureInvariantAndReproof:
    def test_unacked_hide_still_blocks_capture_then_restart_reproves(
        self, controllable
    ):
        indicator = controllable
        indicator.ack_enabled = False
        with pytest.raises(RuntimeError, match="did not acknowledge"):
            control_overlay.set_active(True)
        # The display thread recovers on its own — beating and applying
        # again. The unacknowledged transition is still never accepted
        # as healthy: the sticky error keeps the probe False and a
        # capture's hide still refuses (the #40-pinned invariant,
        # re-pinned against the new semantics).
        indicator.ack_enabled = True
        deadline = time.monotonic() + 2.0
        while (
            indicator.applied < indicator.version
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert indicator.applied >= indicator.version
        assert control_overlay.is_healthy() is False
        with pytest.raises(RuntimeError, match="indicator failed"):
            with control_overlay.suspend_for_capture():
                pytest.fail("capture ran behind an unacknowledged hide")
        # What #41 changes is permanence, not confirmation: an overlay
        # restart re-proves the indicator from scratch, and only then
        # is it healthy again — the stale state was never accepted.
        control_overlay.restart()
        assert control_overlay._instance is not indicator
        assert control_overlay.is_healthy() is True
        status = control_overlay.indicator_status()
        assert status["state"] == "ok"
        assert status["fault_class"] is None
        assert control_overlay.set_active(True) is True


class TestClassARecovery:
    def test_class_a_episode_clears_without_process_restart(self, controllable):
        indicator = controllable
        owner = ready_controller()
        owner.input_ledger.enable()
        owner.set_health_probe(
            control_overlay.is_healthy,
            detail=control_overlay.health_detail,
            indicator=control_overlay.indicator_status,
        )
        attempts = []
        escalations = []

        def handler():
            # The production routing (see __main__._recover_control_channel):
            # restart the component that owns the fault.
            attempts.append(1)
            status = control_overlay.indicator_status()
            if status["state"] == "failed":
                control_overlay.restart()
                return (
                    "indicator overlay restarted "
                    f"(fault class {status['fault_class']})"
                )
            return "input monitor threads restarted"

        owner.health_recovery = True
        owner.set_recovery_handler(handler)
        owner.set_escalation_handler(escalations.append)

        indicator.ack_enabled = False
        with pytest.raises(RuntimeError, match="did not acknowledge"):
            control_overlay.set_active(True)
        # Latch the coordinator the way the worker probe does live.
        owner._fail_open(
            site="worker.health_check", detail=control_overlay.health_detail()
        )
        owner._mark_unavailable()
        health = owner.status()["health"]
        assert health["emergency"] is True
        assert health["indicator"]["state"] == "failed"
        assert health["indicator"]["fault_class"] == "A"

        indicator.ack_enabled = True  # Thread demonstrably beating again.
        _latched(owner)  # Backdate the latch past the recovery grace.
        result = owner.status()
        assert attempts == [1]
        event = result["health"]["recovery_events"][-1]
        assert event["action"] == "wedged_channel_recovery"
        assert "indicator overlay restarted" in event["outcome"]
        assert "fault class A" in event["outcome"]
        assert escalations == []
        # Cleared in-process: a fresh indicator owns the display now,
        # the probe is green, and the coordinator's own rehook
        # recovery can reopen the channel — no process restart
        # anywhere in the path.
        assert control_overlay._instance is not indicator
        assert control_overlay.is_healthy() is True
        owner._recover_after_rehook(
            healthy=True, sampled_at=time.monotonic()
        )
        assert owner.status()["state"] == "ready"


class TestClassC:
    def _wedged_indicator(self, monkeypatch):
        indicator = control_overlay._Indicator()
        indicator.thread = Mock()
        # join() can never confirm the exit: the native-wedge signature.
        indicator.thread.is_alive.return_value = True
        monkeypatch.setattr(control_overlay, "_instance", indicator)
        return indicator

    def test_unconfirmable_teardown_never_stacks_a_second_owner(
        self, monkeypatch
    ):
        indicator = self._wedged_indicator(monkeypatch)
        with pytest.raises(RuntimeError, match="did not stop"):
            control_overlay.restart()
        # The failed owner is retained; start() was never reached.
        assert control_overlay._instance is indicator

    def test_failed_teardown_counts_toward_escalation(self, monkeypatch):
        self._wedged_indicator(monkeypatch)
        owner = ready_controller()
        owner.health_recovery = True

        def handler():
            control_overlay.restart()  # Raises out of the teardown.
            return "unreachable"

        owner.set_recovery_handler(handler)
        escalations = []
        owner.set_escalation_handler(escalations.append)
        owner._thread_beat = time.monotonic() - 60.0  # Wedged verdict.
        _tick(owner)
        _tick(owner)
        assert escalations == []
        assert len(owner._failed_recovery_at) == 2
        _tick(owner)
        # The third evaluation escalates instead of spending another
        # teardown attempt — driven only by recovery_tick(), with no
        # status() poll anywhere in this test.
        assert escalations == ["repeated_recovery_failures"]
        actions = [e["action"] for e in owner._recovery_events]
        assert actions[-1] == "process_restart_escalation"


class TestDepolledLadder:
    def test_latch_ladder_advances_without_status_polls(self):
        owner = ready_controller()
        calls = []
        escalations = []
        owner.health_recovery = True
        owner.set_recovery_handler(lambda: calls.append(1) or "restarted")
        owner.set_escalation_handler(escalations.append)
        _latched(owner)
        # No status() calls anywhere below: before #41 this ladder
        # only moved when a ControlStatus poll drove _maybe_recover.
        for _ in range(3):
            _tick(owner)
        assert len(calls) == 3
        assert escalations == []
        _tick(owner)
        assert len(calls) == 3  # Escalated instead of a fourth attempt.
        assert escalations == ["latch_retrips"]
        event = owner._recovery_events[-1]
        assert event["action"] == "process_restart_escalation"
        assert event["reason"] == "latch_retrips"

    def test_driver_thread_spawns_only_when_armed_and_is_reaped(
        self, monkeypatch
    ):
        monkeypatch.setattr(control, "_interactive_desktop", lambda: True)

        def stub_run(owner):
            def run():
                owner._thread_beat = control.time.monotonic()
                owner._ready.set()
                owner._stop.wait()

            return run

        unarmed = control.ControlCoordinator()
        monkeypatch.setattr(unarmed, "_run", stub_run(unarmed))
        unarmed.start()
        try:
            assert unarmed._recovery_driver is None
        finally:
            unarmed.stop()

        armed = control.ControlCoordinator()
        monkeypatch.setattr(armed, "_run", stub_run(armed))
        armed.health_recovery = True
        armed.set_recovery_handler(lambda: "restarted")
        armed.start()
        driver = armed._recovery_driver
        try:
            assert driver is not None
            assert driver.is_alive()
        finally:
            armed.stop()
        assert not driver.is_alive()
        assert armed._recovery_driver is None
