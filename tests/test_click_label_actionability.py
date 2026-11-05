"""Issue #27 — actionability auto-wait for Click-by-label.

#16 gave the battery (visible / enabled / stable, bounded poll) to the
ref actions Invoke/SetValue/Toggle. Click-by-label dispatched at the
snapshot coordinates immediately; it now resolves the label's live
element, waits for it to become actionable, and clicks its live centre.
Labels the battery cannot apply to (not ref-tracked, no UIA identity)
keep the pre-#27 snapshot-coordinate behaviour.
"""

import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="element refs drive the Windows UIA stack"
)

from agentic_windows_mcp.desktop import service as desktop_service
from agentic_windows_mcp.desktop.service import Desktop, StaleRefError
from agentic_windows_mcp.tree.views import BoundingBox, TreeElementNode, TreeState
from agentic_windows_mcp.uia import PatternId

from tests.test_actionability import FakeControl, FakePattern
from tests.test_multi_tools import FakeMCP

RECT_A_CENTER = (5, 5)  # FakeControl's RECT_A is (0,0,10,10)


def _node(name, runtime_id):
    box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
    return TreeElementNode(
        bounding_box=box, center=box.get_center(), name=name,
        control_type="Button", window_name="Win", metadata={},
        runtime_id=runtime_id, window_handle=4242,
    )


def _desktop(monkeypatch, target, *, window_alive=True):
    desktop = Desktop.__new__(Desktop)
    desktop._refs_generation = 0
    desktop._element_refs = {}
    desktop.register_element_refs(
        TreeState(interactive_nodes=[_node(target.Name, tuple(target._rid))])
    )
    if window_alive:
        root = FakeControl("Win", (1,))
        root.GetChildren = lambda: [target]
    else:
        root = None
    monkeypatch.setattr(
        desktop_service.uia, "ControlFromHandle", lambda handle: root
    )
    return desktop


class TestLabelBattery:
    def test_becomes_enabled_then_returns_live_centre(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: FakePattern()},
            enabled_after=10,
        )
        desktop = _desktop(monkeypatch, target)
        started = time.monotonic()
        point = desktop.await_label_click_target(0, wait_timeout=3.0)
        elapsed = time.monotonic() - started
        assert point == RECT_A_CENTER
        assert elapsed >= 0.3  # it genuinely waited through some polls
        assert elapsed < 3.0

    def test_permanently_disabled_times_out_naming_enabled(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: FakePattern()},
            enabled_after=10 ** 9,
        )
        desktop = _desktop(monkeypatch, target)
        started = time.monotonic()
        with pytest.raises(TimeoutError) as excinfo:
            desktop.await_label_click_target(0, wait_timeout=0.4)
        assert time.monotonic() - started >= 0.35
        message = str(excinfo.value)
        assert message.startswith("ACTIONABILITY_TIMEOUT:")
        assert "failing check: enabled" in message

    def test_zero_timeout_skips_battery(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: FakePattern()},
            enabled_after=10 ** 9,
        )
        desktop = _desktop(monkeypatch, target)
        started = time.monotonic()
        assert desktop.await_label_click_target(0, wait_timeout=0) is None
        assert time.monotonic() - started < 0.2
        assert target._enabled_reads == 0  # the element was never polled

    def test_unknown_label_is_not_applicable(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: FakePattern()}
        )
        desktop = _desktop(monkeypatch, target)
        assert desktop.await_label_click_target(99, wait_timeout=1.0) is None

    def test_element_without_uia_identity_is_not_applicable(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: FakePattern()}
        )
        desktop = Desktop.__new__(Desktop)
        desktop._refs_generation = 0
        desktop._element_refs = {}
        desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("Save", None)])
        )
        assert desktop.await_label_click_target(0, wait_timeout=1.0) is None

    def test_window_gone_fails_fast_as_stale(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: FakePattern()}
        )
        desktop = _desktop(monkeypatch, target, window_alive=False)
        started = time.monotonic()
        with pytest.raises(StaleRefError):
            desktop.await_label_click_target(0, wait_timeout=5.0)
        assert time.monotonic() - started < 0.5

    def test_invalid_wait_timeout_rejected(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: FakePattern()}
        )
        desktop = _desktop(monkeypatch, target)
        with pytest.raises(ValueError, match="wait_timeout"):
            desktop.await_label_click_target(0, wait_timeout=99)


class _StubDesktop:
    """Tool-level stub: records how Click obtained its point."""

    def __init__(self, live_point):
        self.desktop_state = object()
        self._live_point = live_point
        self.click_calls = []
        self.coordinate_lookups = []

    def await_label_click_target(self, label, wait_timeout):
        return self._live_point

    def get_coordinates_from_label(self, label):
        self.coordinate_lookups.append(label)
        return (40, 40)

    def click(self, loc=None, button="left", clicks=1):
        self.click_calls.append((loc, button, clicks))


def _click_tool(desktop):
    from agentic_windows_mcp.tools.input import register

    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools["Click"]


class TestClickToolPlumbing:
    def test_click_uses_live_point_when_battery_ran(self):
        desktop = _StubDesktop((7, 9))
        result = asyncio.run(_click_tool(desktop)(label=3))
        assert desktop.click_calls == [([7, 9], "left", 1)]
        assert desktop.coordinate_lookups == []
        assert "(7,9)" in result

    def test_click_falls_back_to_snapshot_coordinates(self):
        desktop = _StubDesktop(None)
        result = asyncio.run(_click_tool(desktop)(label=3))
        assert desktop.click_calls == [([40, 40], "left", 1)]
        assert desktop.coordinate_lookups == [3]
        assert "(40,40)" in result
