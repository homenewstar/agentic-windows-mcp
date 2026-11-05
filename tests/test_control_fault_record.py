"""Issue #25 (half A) — fail-open faults are recorded and visible.

Every _fail_open records {site, error, at_monotonic} on the coordinator
and ControlStatus surfaces it as health["last_fault"]; the control
worker loop's catch-all logs the swallowed exception with its traceback
(logger.exception) and records it under site "worker.loop_exception".
"""

import logging
import queue
import threading

from agentic_windows_mcp.desktop import control, control_worker


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

    def _fail_open(self, site="unknown", error=None):
        self.fail_open_calls.append((site, error))
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
    site, error = owner.fail_open_calls[0]
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
