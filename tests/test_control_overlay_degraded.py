"""Issue #40 — a cursor that cannot be located degrades the AI control
indicator (border + banner, halo ring deferred) instead of killing the
display thread and latching the whole control channel.

Live shape being pinned (10/11/2026): _build_layers raised on a failed
GetCursorPos, the display thread died with a sticky error, every lease
arm re-raised it, in-process recovery (which never restarts the
overlay) could not clear it, and the #25 ladder escalated to a process
restart after ~14 minutes of channel outage.
"""

import ctypes
import threading
import time
from ctypes import wintypes

import pytest

from agentic_windows_mcp.desktop import control_overlay, flash_overlay


DETAIL = (
    "cursor not locatable for AI control indicator "
    "(GetCursorPos failed, win32 error 5, thread desktop 'Default')"
)


@pytest.fixture(autouse=True)
def _stop_indicator():
    control_overlay.stop()
    yield
    control_overlay.stop()


class FakeLayer:
    """Records construction and lifecycle events; registry reset per test."""

    made: list = []

    def __init__(self, x, y, width, height, bgra, name, *, breathes=True):
        self.x, self.y, self.width, self.height = x, y, width, height
        self.name = name
        self.breathes = breathes
        self.hwnd = 1000 + len(FakeLayer.made)
        self.events: list[str] = []
        FakeLayer.made.append(self)

    def set_opacity(self, opacity):
        self.events.append("opacity")

    def show(self):
        self.events.append("show")

    def hide(self):
        self.events.append("hide")

    def move(self, x, y):
        self.events.append("move")

    def close(self):
        self.events.append("close")


@pytest.fixture
def fake_layers(monkeypatch):
    FakeLayer.made = []
    monkeypatch.setattr(control_overlay, "_Layer", FakeLayer)
    monkeypatch.setattr(flash_overlay, "_pump_messages", lambda hwnd: None)
    return FakeLayer.made


def test_locate_cursor_success_returns_point(monkeypatch):
    monkeypatch.setattr(control_overlay._user32_probe, "GetCursorPos", lambda ptr: True)
    point, detail = control_overlay._locate_cursor()
    assert point is not None
    assert detail is None


def test_locate_cursor_failure_reports_win32_error_and_desktop(monkeypatch):
    # Issue #40 (c): the pre-fix raise discarded both facts, which is
    # why the 10/11 incident's cause is still unidentified.
    monkeypatch.setattr(control_overlay._user32_probe, "GetCursorPos", lambda ptr: False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    monkeypatch.setattr(control_overlay, "_thread_desktop_name", lambda: "Default")
    point, detail = control_overlay._locate_cursor()
    assert point is None
    assert detail is not None
    assert "win32 error 5" in detail
    assert "Default" in detail


def test_build_layers_without_cursor_keeps_border_and_banner(monkeypatch, fake_layers):
    monkeypatch.setattr(control_overlay, "_locate_cursor", lambda: (None, DETAIL))
    layers, ring, cursor_detail = control_overlay._build_layers(
        ((0, 0, 800, 600),), pending=False
    )
    assert ring is None
    assert cursor_detail == DETAIL
    names = [layer.name for layer in layers]
    # The static display still satisfies the inform-the-user purpose.
    assert {"0_top", "0_bottom", "0_left", "0_right"} <= set(names)
    assert "cursor" not in names


def test_indicator_degrades_instead_of_dying_and_acks_changes(monkeypatch, fake_layers):
    monkeypatch.setattr(control_overlay, "_monitor_rects", lambda: ((0, 0, 800, 600),))
    monkeypatch.setattr(control_overlay, "_locate_cursor", lambda: (None, DETAIL))
    control_overlay.start()
    indicator = control_overlay._instance
    assert indicator is not None
    # change() acknowledges while degraded — no stored error is raised.
    assert control_overlay.set_active(True) is True
    assert control_overlay.is_healthy() is True
    assert indicator.error is None
    assert indicator.thread.is_alive()
    status = control_overlay.indicator_status()
    assert status == {"state": "degraded", "detail": DETAIL}
    # Lease arm/disarm cycles during degradation complete cleanly; the
    # 10/11 failure mode was exactly this cycle re-raising a dead
    # thread's stored error into _fail_open on every arm.
    assert control_overlay.set_pending(True) is True
    assert control_overlay.set_active(False) is True
    assert control_overlay.set_active(True) is True
    assert control_overlay.is_healthy() is True
    assert indicator.error is None
    control_overlay.stop()


def test_ring_appears_and_degraded_clears_when_cursor_returns(monkeypatch, fake_layers):
    monkeypatch.setattr(control_overlay, "_monitor_rects", lambda: ((0, 0, 800, 600),))
    locatable = {"ok": False}

    def locate():
        if locatable["ok"]:
            return wintypes.POINT(100, 100), None
        return None, DETAIL

    monkeypatch.setattr(control_overlay, "_locate_cursor", locate)
    # The follow path reads through the shared handle; keep it quiet.
    monkeypatch.setattr(control_overlay._user32, "GetCursorPos", lambda ptr: True)
    control_overlay.start()
    assert control_overlay.set_active(True) is True
    assert control_overlay.indicator_status()["state"] == "degraded"
    locatable["ok"] = True
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if control_overlay.indicator_status()["state"] == "ok":
            break
        time.sleep(0.02)
    assert control_overlay.indicator_status() == {"state": "ok", "detail": None}
    rings = [layer for layer in FakeLayer.made if layer.name == "cursor"]
    assert rings, "ring layer was never placed after the cursor returned"
    assert "show" in rings[-1].events
    assert control_overlay.is_healthy() is True
    control_overlay.stop()


def test_genuine_indicator_error_still_reports_failed(monkeypatch):
    # Regression guard for #8/#35: a real display-thread fault (stored
    # error) must keep failing the probe and reporting "failed" — the
    # degraded state must not become a blanket that hides dead threads.
    class FailedIndicator:
        thread = threading.current_thread()
        stopping = False
        error = RuntimeError("window failed")
        degraded = None
        heartbeat = time.monotonic()

        def stop(self):
            pass

    monkeypatch.setattr(control_overlay, "_instance", FailedIndicator())
    assert control_overlay.is_healthy() is False
    status = control_overlay.indicator_status()
    assert status["state"] == "failed"
    assert "window failed" in (status["detail"] or "")
