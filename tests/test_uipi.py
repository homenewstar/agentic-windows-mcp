"""Issue #14 — UIPI pre-check: a target process at a higher integrity level
than the server is denied with PERM_DENIED before any input is sent, because
SendInput would otherwise report success while UIPI blocks delivery
(findings ledger W-002)."""

import sys

import pytest

from agentic_windows_mcp.uia import integrity

MEDIUM = 0x2000
HIGH = 0x3000
SYSTEM = 0x4000


class TestComparator:
    @pytest.mark.parametrize(
        "target,own,expected",
        [
            (HIGH, MEDIUM, True),
            (SYSTEM, HIGH, True),
            (MEDIUM, MEDIUM, False),
            (MEDIUM, HIGH, False),
            (None, MEDIUM, False),
            (HIGH, None, False),
            (None, None, False),
        ],
    )
    def test_is_strictly_higher(self, target, own, expected):
        assert integrity.is_strictly_higher(target, own) is expected

    def test_level_labels(self):
        assert integrity.level_label(MEDIUM) == "medium"
        assert integrity.level_label(HIGH) == "high"
        assert integrity.level_label(None) == "unknown"


class TestCheckTargetProcess:
    def _patch_levels(self, monkeypatch, target, own):
        monkeypatch.setattr(
            integrity, "integrity_level_of", lambda pid: target if pid else None
        )
        monkeypatch.setattr(integrity, "own_integrity_level", lambda: own)

    def test_higher_target_denied_with_perm_denied_prefix(self, monkeypatch):
        self._patch_levels(monkeypatch, HIGH, MEDIUM)
        with pytest.raises(integrity.UipiBlockedError) as excinfo:
            integrity.check_target_process(4321, "Click")
        assert str(excinfo.value).startswith("PERM_DENIED:")
        assert "4321" in str(excinfo.value)

    def test_equal_level_allowed(self, monkeypatch):
        self._patch_levels(monkeypatch, MEDIUM, MEDIUM)
        integrity.check_target_process(4321, "Click")  # no raise

    def test_lower_target_allowed(self, monkeypatch):
        self._patch_levels(monkeypatch, MEDIUM, HIGH)
        integrity.check_target_process(4321, "Click")

    def test_unknown_levels_allowed(self, monkeypatch):
        # An unreadable level is unknown, not proof of a higher one.
        self._patch_levels(monkeypatch, None, MEDIUM)
        integrity.check_target_process(4321, "Click")
        self._patch_levels(monkeypatch, HIGH, None)
        integrity.check_target_process(4321, "Click")

    def test_no_pid_is_a_noop(self, monkeypatch):
        self._patch_levels(monkeypatch, HIGH, MEDIUM)
        integrity.check_target_process(None, "Click")


@pytest.mark.skipif(sys.platform != "win32", reason="drives the Windows UIA stack")
class TestServiceIntegration:
    def test_invoke_denied_before_pattern_dispatch(self, monkeypatch):
        """End-to-end at the service seam: a ref whose process outranks the
        server raises PERM_DENIED and the pattern is never invoked."""
        from agentic_windows_mcp.desktop import service as desktop_service
        from agentic_windows_mcp.desktop.service import Desktop
        from agentic_windows_mcp.tree.views import (
            BoundingBox,
            TreeElementNode,
            TreeState,
        )
        from agentic_windows_mcp.uia import PatternId

        class FakePattern:
            def __init__(self):
                self.calls = []

            def Invoke(self):
                self.calls.append("Invoke")
                return True

        class FakeControl:
            ProcessId = 4321

            def __init__(self, patterns, name="Save", runtime_id=(7, 8), children=()):
                self.Name = name
                self._patterns = patterns
                self._rid = list(runtime_id)
                self._children = list(children)

            def GetRuntimeId(self):
                return list(self._rid)

            def GetChildren(self):
                return list(self._children)

            def GetPattern(self, pattern_id):
                return self._patterns.get(pattern_id)

        pattern = FakePattern()
        target = FakeControl({PatternId.InvokePattern: pattern})
        root = FakeControl({}, name="Win", runtime_id=(1,), children=[target])

        box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
        node = TreeElementNode(
            bounding_box=box, center=box.get_center(), name="Save",
            control_type="Button", window_name="Win", metadata={},
            runtime_id=(7, 8), window_handle=4242,
        )
        desktop = Desktop.__new__(Desktop)
        desktop._refs_generation = 0
        desktop._element_refs = {}
        gen = desktop.register_element_refs(TreeState(interactive_nodes=[node]))
        monkeypatch.setattr(
            desktop_service.uia, "ControlFromHandle", lambda handle: root
        )
        monkeypatch.setattr(
            integrity, "integrity_level_of", lambda pid: HIGH if pid == 4321 else None
        )
        monkeypatch.setattr(integrity, "own_integrity_level", lambda: MEDIUM)

        with pytest.raises(integrity.UipiBlockedError) as excinfo:
            desktop.invoke_element(0, snapshot=gen)
        assert str(excinfo.value).startswith("PERM_DENIED:")
        assert pattern.calls == []  # nothing was dispatched
