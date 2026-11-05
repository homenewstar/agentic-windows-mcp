"""Issue #16 — actionability auto-wait for ref actions.

The battery (visible / enabled / stable) polls transient failures until a
bounded budget expires; terminal failures (unsupported pattern, read-only)
fail fast; wait_timeout=0 restores immediate dispatch.
"""

import sys
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="element refs drive the Windows UIA stack"
)

from agentic_windows_mcp.desktop import service as desktop_service
from agentic_windows_mcp.desktop.service import Desktop
from agentic_windows_mcp.tree.views import BoundingBox, TreeElementNode, TreeState
from agentic_windows_mcp.uia import PatternId

RECT_A = SimpleNamespace(left=0, top=0, right=10, bottom=10)
RECT_B = SimpleNamespace(left=5, top=0, right=15, bottom=10)


class FakePattern:
    def __init__(self):
        self.calls = []

    def Invoke(self):
        self.calls.append("Invoke")
        return True

    def SetValue(self, value):
        self.calls.append(("SetValue", value))
        return True

    @property
    def IsReadOnly(self):
        return False


class ReadOnlyPattern(FakePattern):
    @property
    def IsReadOnly(self):
        return True


class FakeControl:
    """A control whose actionability evolves with reads.

    enabled_after: IsEnabled returns False for the first N reads, then True.
    offscreen: IsOffscreen always True. jitter: BoundingRectangle alternates.
    """

    def __init__(self, name, runtime_id, *, patterns=None, enabled_after=0,
                 offscreen=False, jitter=False):
        self.Name = name
        self.LocalizedControlType = "button"
        self.AutomationId = ""
        self._rid = list(runtime_id)
        self._patterns = patterns or {}
        self._enabled_after = enabled_after
        self._enabled_reads = 0
        self._offscreen = offscreen
        self._jitter = jitter
        self._rect_reads = 0

    @property
    def IsEnabled(self):
        self._enabled_reads += 1
        return self._enabled_reads > self._enabled_after

    @property
    def IsOffscreen(self):
        return self._offscreen

    @property
    def BoundingRectangle(self):
        if not self._jitter:
            return RECT_A
        self._rect_reads += 1
        return RECT_A if self._rect_reads % 2 else RECT_B

    def GetRuntimeId(self):
        return list(self._rid)

    def GetChildren(self):
        return []

    def GetPattern(self, pattern_id):
        return self._patterns.get(pattern_id)


def _node(name, runtime_id):
    box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
    return TreeElementNode(
        bounding_box=box, center=box.get_center(), name=name,
        control_type="Button", window_name="Win", metadata={},
        runtime_id=runtime_id, window_handle=4242,
    )


def _desktop(monkeypatch, target):
    desktop = Desktop.__new__(Desktop)
    desktop._refs_generation = 0
    desktop._element_refs = {}
    gen = desktop.register_element_refs(
        TreeState(interactive_nodes=[_node(target.Name, tuple(target._rid))])
    )
    root = FakeControl("Win", (1,))
    root.GetChildren = lambda: [target]
    monkeypatch.setattr(
        desktop_service.uia, "ControlFromHandle", lambda handle: root
    )
    return desktop, gen


class TestAutoWait:
    def test_becomes_enabled_within_budget(self, monkeypatch):
        pattern = FakePattern()
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: pattern},
            enabled_after=10,
        )
        desktop, gen = _desktop(monkeypatch, target)
        started = time.monotonic()
        desktop.invoke_element(0, snapshot=gen, wait_timeout=3.0)
        elapsed = time.monotonic() - started
        assert pattern.calls == ["Invoke"]
        assert elapsed >= 0.3  # it genuinely waited through some polls
        assert elapsed < 3.0

    def test_permanently_disabled_times_out_naming_enabled(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8),
            patterns={PatternId.InvokePattern: FakePattern()},
            enabled_after=10 ** 9,
        )
        desktop, gen = _desktop(monkeypatch, target)
        started = time.monotonic()
        with pytest.raises(TimeoutError) as excinfo:
            desktop.invoke_element(0, snapshot=gen, wait_timeout=0.4)
        elapsed = time.monotonic() - started
        assert elapsed >= 0.35
        message = str(excinfo.value)
        assert message.startswith("ACTIONABILITY_TIMEOUT:")
        assert "failing check: enabled" in message

    def test_offscreen_times_out_naming_visible(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8),
            patterns={PatternId.InvokePattern: FakePattern()},
            offscreen=True,
        )
        desktop, gen = _desktop(monkeypatch, target)
        with pytest.raises(TimeoutError) as excinfo:
            desktop.invoke_element(0, snapshot=gen, wait_timeout=0.3)
        assert "failing check: visible" in str(excinfo.value)

    def test_unstable_rect_times_out_naming_stable(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8),
            patterns={PatternId.InvokePattern: FakePattern()},
            jitter=True,
        )
        desktop, gen = _desktop(monkeypatch, target)
        with pytest.raises(TimeoutError) as excinfo:
            desktop.invoke_element(0, snapshot=gen, wait_timeout=0.3)
        assert "failing check: stable" in str(excinfo.value)

    def test_unsupported_pattern_fails_immediately(self, monkeypatch):
        target = FakeControl("Save", (7, 8), patterns={})
        desktop, gen = _desktop(monkeypatch, target)
        started = time.monotonic()
        with pytest.raises(ValueError) as excinfo:
            desktop.invoke_element(0, snapshot=gen, wait_timeout=5.0)
        assert time.monotonic() - started < 0.5
        assert str(excinfo.value).startswith("UNSUPPORTED_PATTERN:")

    def test_zero_timeout_dispatches_immediately(self, monkeypatch):
        # Single-shot semantics: even a not-yet-enabled element is
        # dispatched to, exactly like pre-#16 behaviour.
        pattern = FakePattern()
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: pattern},
            enabled_after=10 ** 9,
        )
        desktop, gen = _desktop(monkeypatch, target)
        desktop.invoke_element(0, snapshot=gen, wait_timeout=0)
        assert pattern.calls == ["Invoke"]

    def test_invalid_wait_timeout_rejected(self, monkeypatch):
        target = FakeControl(
            "Save", (7, 8), patterns={PatternId.InvokePattern: FakePattern()}
        )
        desktop, gen = _desktop(monkeypatch, target)
        with pytest.raises(ValueError, match="wait_timeout"):
            desktop.invoke_element(0, snapshot=gen, wait_timeout=99)


class TestSetValueTerminalChecks:
    def test_readonly_fails_fast_as_not_editable(self, monkeypatch):
        pattern = ReadOnlyPattern()
        target = FakeControl(
            "Name", (7, 9), patterns={PatternId.ValuePattern: pattern}
        )
        desktop, gen = _desktop(monkeypatch, target)
        started = time.monotonic()
        with pytest.raises(ValueError) as excinfo:
            desktop.set_element_value(0, "hello", snapshot=gen, wait_timeout=5.0)
        assert time.monotonic() - started < 0.5
        assert str(excinfo.value).startswith("NOT_EDITABLE:")
        assert pattern.calls == []

    def test_writable_set_value_still_dispatches(self, monkeypatch):
        pattern = FakePattern()
        target = FakeControl(
            "Name", (7, 9), patterns={PatternId.ValuePattern: pattern}
        )
        desktop, gen = _desktop(monkeypatch, target)
        desktop.set_element_value(0, "hello", snapshot=gen, wait_timeout=1.0)
        assert pattern.calls == [("SetValue", "hello")]
