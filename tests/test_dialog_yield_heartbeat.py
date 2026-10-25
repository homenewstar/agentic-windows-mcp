"""Regression: the control heartbeat must not override a dialog yield.

Field failure: the consent-dialog monitor released input suppression,
but refresh_deadline() -- the watchdog heartbeat running every ~0.25s --
set _suppress back to True unconditionally, so the user's release lasted
a fraction of a second and they still needed the takeover chord.
"""

from __future__ import annotations

import threading
import time

from agentic_windows_mcp.desktop import control_worker


class _Ledger:
    def __init__(self) -> None:
        self.blocked = 0

    def block_new(self) -> None:
        self.blocked += 1


class _Owner:
    def __init__(self, dialog_yield: bool) -> None:
        self._fault_token = object()
        self._emergency = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state = "ai"
        self._visual_armed = True
        self._fast_takeover = False
        self._dialog_yield = dialog_yield
        self._deadline = 0.0
        self._suppress = False
        self.input_ledger = _Ledger()
        self.fail_open_calls = 0

    def _fail_open(self) -> None:
        self.fail_open_calls += 1


def test_heartbeat_keeps_suppression_off_during_dialog_yield():
    owner = _Owner(dialog_yield=True)
    control_worker.refresh_deadline(owner, time.monotonic())
    assert owner._suppress is False
    # The deadline still renews (the lease is alive), only swallowing is off.
    assert owner._deadline > 0.0
    assert owner.fail_open_calls == 0


def test_heartbeat_suppresses_normally_without_yield():
    owner = _Owner(dialog_yield=False)
    control_worker.refresh_deadline(owner, time.monotonic())
    assert owner._suppress is True
    assert owner._deadline > 0.0
