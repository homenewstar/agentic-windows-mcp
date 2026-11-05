"""Issue #25 (half A) — fail-open faults are recorded and visible.

Every _fail_open records {site, error, at_monotonic} on the coordinator
and ControlStatus surfaces it as health["last_fault"]; the control
worker loop's catch-all logs the swallowed exception with its traceback
(logger.exception) and records it under site "worker.loop_exception".

Issue #26 — condition faults (no exception exists) record the measured
condition in ``detail``: the worker health check names which condition
failed (the probe's own explanation can carry the exception the
overlay thread stored), so no path leaves a bare site with error null.
"""

import logging
import queue
import threading
import time
from types import SimpleNamespace

from agentic_windows_mcp.desktop import control, control_overlay, control_worker


def _health(owner):
    return owner._health_locked(control.time.monotonic())


def test_last_fault_starts_empty():
    owner = control.ControlCoordinator()
    assert _health(owner)["last_fault"] is None


def test_fail_open_records_site_error_and_timestamp():
    owner = control.ControlCoordinator()
    owner._fail_open(site="worker.loop_exception", error=ValueError("boom"))
    fault = _health(owner)["last_fault"]
    assert fault["site"] == "worker.loop_exception"
    assert fault["error"] == "ValueError: boom"
    assert isinstance(fault["at_monotonic"], float)
    assert fault["at_monotonic"] > 0


def test_fail_open_without_exception_records_none_error():
    owner = control.ControlCoordinator()
    owner._fail_open(site="queue.full")
    fault = _health(owner)["last_fault"]
    assert fault["site"] == "queue.full"
    assert fault["error"] is None


def test_latest_fault_wins_and_survives_recovery():
    owner = control.ControlCoordinator()
    owner._fail_open(site="hook.key_deadline")
    owner._fail_open(site="worker.health_check")
    assert _health(owner)["last_fault"]["site"] == "worker.health_check"
    # The record is a history of the last fault, not a state flag: a
    # successful rehook recovery must not erase the diagnosis.
    owner._rehook_completed = owner._fault_token
    owner._recover_after_rehook(
        owner._fault_token, healthy=True, sampled_at=control.time.monotonic()
    )
    assert not owner._emergency
    assert _health(owner)["last_fault"]["site"] == "worker.health_check"


def test_fail_open_still_latches_emergency():
    owner = control.ControlCoordinator()
    owner._fail_open(site="worker.loop_exception", error=RuntimeError("x"))
    assert owner._emergency
    assert owner.input_ledger._blocked
    assert not owner._suppress


def test_refresh_deadline_lock_busy_records_its_site():
    owner = control.ControlCoordinator()
    owner._state = "ai"
    owner._visual_armed = True
    with owner._lock:
        control_worker.refresh_deadline(owner, control.time.monotonic())
    fault = _health(owner)["last_fault"]
    assert fault is not None
    assert fault["site"] == "refresh_deadline.lock_busy"


class _StubOwner:
    """Minimal owner for run_control_worker: its _handle always raises."""

    def __init__(self):
        self._release_requested = threading.Event()
        self._stop = threading.Event()
        self._emergency = False
        self._events = queue.Queue()
        self._fault_token = object()
        self._rehook_completed = None
        self._rotating = False
        self._health_probe = None
        self._thread = threading.Thread()  # not alive; never reached
        self._thread_beat = 0.0
        self.fail_open_calls = []
        self.marked_unavailable = 0

    def _handle(self, event):
        raise RuntimeError("loop boom")

    def _fail_open(self, site="unknown", error=None, detail=None):
        self.fail_open_calls.append((site, error, detail))
        self._emergency = True

    def _mark_unavailable(self):
        self.marked_unavailable += 1
        self._stop.set()


def test_worker_catch_all_logs_and_records_the_exception(caplog):
    owner = _StubOwner()
    with caplog.at_level(
        logging.ERROR, logger="agentic_windows_mcp.desktop.control_worker"
    ):
        control_worker.run_control_worker(owner, lambda: True)
    assert owner.marked_unavailable == 1
    assert len(owner.fail_open_calls) == 1
    site, error, detail = owner.fail_open_calls[0]
    assert site == "worker.loop_exception"
    assert isinstance(error, RuntimeError)
    assert "loop boom" in str(error)
    # The exception is logged with its traceback, not swallowed.
    records = [
        r
        for r in caplog.records
        if r.name == "agentic_windows_mcp.desktop.control_worker"
        and r.levelno >= logging.ERROR
    ]
    assert records, "worker catch-all must log the fault"
    assert any(
        r.exc_info and r.exc_info[0] is RuntimeError for r in records
    ), "logger.exception must attach the traceback"


# --- Issue #26: condition faults record the measured condition --------


def test_fail_open_records_detail():
    owner = control.ControlCoordinator()
    owner._fail_open(site="worker.health_check", detail="monitor beat stale (3.2s old)")
    fault = _health(owner)["last_fault"]
    assert fault["site"] == "worker.health_check"
    assert fault["error"] is None
    assert fault["detail"] == "monitor beat stale (3.2s old)"


def test_fail_open_detail_defaults_to_none():
    owner = control.ControlCoordinator()
    owner._fail_open(site="queue.full", error=RuntimeError("x"))
    fault = _health(owner)["last_fault"]
    assert fault["error"] == "RuntimeError: x"
    assert fault["detail"] is None


def test_lock_busy_fault_carries_detail():
    owner = control.ControlCoordinator()
    owner._state = "ai"
    owner._visual_armed = True
    with owner._lock:
        control_worker.refresh_deadline(owner, control.time.monotonic())
    fault = _health(owner)["last_fault"]
    assert fault["site"] == "refresh_deadline.lock_busy"
    assert fault["detail"]


class _HealthStubOwner:
    """Owner that reaches the worker's health check exactly once."""

    def __init__(self, *, probe, beat_age=0.0, thread_alive=True,
                 probe_detail=None):
        self._release_requested = threading.Event()
        self._stop = threading.Event()
        self._emergency = False
        self._events = queue.Queue()
        self._fault_token = object()
        self._rehook_completed = None
        self._rotating = False
        self._health_probe = probe
        self._probe_detail = (
            (lambda: probe_detail) if probe_detail is not None else None
        )
        if thread_alive:
            self._thread = threading.Thread(
                target=self._stop.wait, args=(30,), daemon=True
            )
            self._thread.start()
        else:
            self._thread = threading.Thread()  # never started
        self._thread_beat = time.monotonic() - beat_age
        self.fail_open_calls = []
        self.marked_unavailable = 0

    def _handle(self, event):
        pass

    def _fail_open(self, site="unknown", error=None, detail=None):
        self.fail_open_calls.append((site, error, detail))
        self._emergency = True

    def _mark_unavailable(self):
        self.marked_unavailable += 1
        self._stop.set()


def _run_health_check(owner):
    control_worker.run_control_worker(owner, lambda: True)
    assert len(owner.fail_open_calls) == 1
    site, error, detail = owner.fail_open_calls[0]
    assert site == "worker.health_check"
    assert error is None
    assert detail
    return detail


def test_health_check_records_probe_explanation():
    owner = _HealthStubOwner(
        probe=lambda: False,
        probe_detail="indicator error: RuntimeError: ack timeout",
    )
    detail = _run_health_check(owner)
    assert "indicator error: RuntimeError: ack timeout" in detail


def test_health_check_records_stale_beat():
    owner = _HealthStubOwner(probe=lambda: True, beat_age=5.0)
    detail = _run_health_check(owner)
    assert "monitor beat stale" in detail


def test_health_check_records_dead_monitor_thread():
    owner = _HealthStubOwner(probe=lambda: True, thread_alive=False)
    detail = _run_health_check(owner)
    assert "monitor thread not alive" in detail


def test_health_check_records_non_interactive_desktop():
    owner = _HealthStubOwner(probe=lambda: True)
    control_worker.run_control_worker(owner, lambda: False)
    site, error, detail = owner.fail_open_calls[0]
    assert site == "worker.health_check"
    assert "desktop not interactive" in detail


def test_probe_detail_surfaces_overlay_error(monkeypatch):
    indicator = SimpleNamespace(
        thread=threading.Thread(target=lambda: None),
        stopping=False,
        error=ValueError("overlay blew up"),
        heartbeat=time.monotonic(),
    )
    indicator.thread.start()
    indicator.thread.join()
    monkeypatch.setattr(control_overlay, "_instance", indicator)
    assert control_overlay.is_healthy() is False
    detail = control_overlay.health_detail()
    assert detail is not None
    assert "ValueError: overlay blew up" in detail


def test_health_detail_none_when_healthy_and_reasons_for_each_state(monkeypatch):
    live = threading.Thread(target=lambda: time.sleep(5), daemon=True)
    live.start()
    try:
        healthy = SimpleNamespace(
            thread=live, stopping=False, error=None,
            heartbeat=time.monotonic(),
        )
        monkeypatch.setattr(control_overlay, "_instance", healthy)
        assert control_overlay.health_detail() is None

        healthy.heartbeat = time.monotonic() - 30
        assert "heartbeat stale" in control_overlay.health_detail()
        healthy.heartbeat = time.monotonic()
        healthy.stopping = True
        assert "stopping" in control_overlay.health_detail()
    finally:
        monkeypatch.setattr(control_overlay, "_instance", None)
    assert control_overlay.health_detail() == "indicator not started"
