"""Issue #3 — element refs: Snapshot @e/@s refs, Invoke/SetValue/Toggle via
UIA patterns, STALE_REF on drift."""

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="element refs drive the Windows UIA stack"
)

from agentic_windows_mcp.desktop import service as desktop_service
from agentic_windows_mcp.desktop.service import Desktop, StaleRefError
from agentic_windows_mcp.tools.elements import parse_ref
from agentic_windows_mcp.tree.views import (
    BoundingBox,
    Center,
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

    def SetValue(self, value):
        self.calls.append(("SetValue", value))
        return True

    def Toggle(self):
        self.calls.append("Toggle")
        return True

    @property
    def ToggleState(self):
        return 1


class FakeControl:
    def __init__(self, name, runtime_id, children=(), patterns=None):
        self.Name = name
        self._rid = list(runtime_id)
        self._children = list(children)
        self._patterns = patterns or {}

    def GetRuntimeId(self):
        return list(self._rid)

    def GetChildren(self):
        return list(self._children)

    def GetPattern(self, pattern_id):
        return self._patterns.get(pattern_id)


def _node(name, runtime_id, handle=4242, control_type="Button"):
    box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
    return TreeElementNode(
        bounding_box=box,
        center=box.get_center(),
        name=name,
        control_type=control_type,
        window_name="Win",
        metadata={},
        runtime_id=runtime_id,
        window_handle=handle,
    )


def _desktop_with_refs(monkeypatch, root, nodes):
    desktop = Desktop.__new__(Desktop)
    desktop._refs_generation = 0
    desktop._element_refs = {}
    generation = desktop.register_element_refs(TreeState(interactive_nodes=nodes))
    monkeypatch.setattr(
        desktop_service.uia, "ControlFromHandle", lambda handle: root
    )
    return desktop, generation


class TestParseRef:
    @pytest.mark.parametrize(
        "raw,expected",
        [("@e12", 12), ("@s3", 3), ("12", 12), (12, 12), ("@e0", 0), (" e7 ", 7)],
    )
    def test_valid_refs(self, raw, expected):
        assert parse_ref(raw) == expected

    @pytest.mark.parametrize("raw", ["", "@e", "abc", "@x1", -1, None, 1.5, True])
    def test_invalid_refs(self, raw):
        with pytest.raises(ValueError):
            parse_ref(raw)


class TestRenderRefs:
    def test_snapshot_strings_carry_refs(self):
        state = TreeState(interactive_nodes=[_node("Save", (1, 2))])
        text = state.interactive_elements_to_string()
        assert "@e0" in text
        assert '"Save"' in text


class TestResolveAndAct:
    def test_invoke_by_ref(self, monkeypatch):
        pattern = FakePattern()
        target = FakeControl("Save", (7, 8), patterns={PatternId.InvokePattern: pattern})
        root = FakeControl("Win", (1,), children=[target])
        desktop, gen = _desktop_with_refs(monkeypatch, root, [_node("Save", (7, 8))])
        ref = desktop.invoke_element(0, snapshot=gen)
        assert ref.name == "Save"
        assert pattern.calls == ["Invoke"]

    def test_set_value_by_ref(self, monkeypatch):
        pattern = FakePattern()
        target = FakeControl("Name", (7, 9), patterns={PatternId.ValuePattern: pattern})
        root = FakeControl("Win", (1,), children=[target])
        desktop, gen = _desktop_with_refs(
            monkeypatch, root, [_node("Name", (7, 9), control_type="Edit")]
        )
        desktop.set_element_value(0, "hello", snapshot=gen)
        assert pattern.calls == [("SetValue", "hello")]

    def test_toggle_by_ref_reports_state(self, monkeypatch):
        pattern = FakePattern()
        target = FakeControl("Agree", (7, 10), patterns={PatternId.TogglePattern: pattern})
        root = FakeControl("Win", (1,), children=[target])
        desktop, gen = _desktop_with_refs(
            monkeypatch, root, [_node("Agree", (7, 10), control_type="CheckBox")]
        )
        _, state = desktop.toggle_element(0, snapshot=gen)
        assert pattern.calls == ["Toggle"]
        assert state == 1

    def test_window_moved_still_resolves(self, monkeypatch):
        """Re-acquisition is by RuntimeId, not coordinates: a target nested
        deeper in a restructured subtree is still found."""
        pattern = FakePattern()
        target = FakeControl("Save", (7, 8), patterns={PatternId.InvokePattern: pattern})
        middle = FakeControl("Panel", (5,), children=[target])
        root = FakeControl("Win", (1,), children=[middle])
        desktop, gen = _desktop_with_refs(monkeypatch, root, [_node("Save", (7, 8))])
        desktop.invoke_element(0, snapshot=gen)
        assert pattern.calls == ["Invoke"]


class TestStaleRef:
    def test_superseded_snapshot(self, monkeypatch):
        root = FakeControl("Win", (1,), children=[])
        desktop, gen = _desktop_with_refs(monkeypatch, root, [_node("Save", (7, 8))])
        with pytest.raises(StaleRefError) as exc:
            desktop.resolve_element_ref(0, snapshot=gen + 1)
        assert "STALE_REF" in str(exc.value)

    def test_unknown_label(self, monkeypatch):
        root = FakeControl("Win", (1,), children=[])
        desktop, _ = _desktop_with_refs(monkeypatch, root, [_node("Save", (7, 8))])
        with pytest.raises(StaleRefError):
            desktop.resolve_element_ref(99)

    def test_element_gone(self, monkeypatch):
        root = FakeControl("Win", (1,), children=[FakeControl("Other", (9, 9))])
        desktop, _ = _desktop_with_refs(monkeypatch, root, [_node("Save", (7, 8))])
        with pytest.raises(StaleRefError) as exc:
            desktop.resolve_element_ref(0)
        assert "no longer exists" in str(exc.value)

    def test_element_renamed(self, monkeypatch):
        target = FakeControl("Save As", (7, 8))
        root = FakeControl("Win", (1,), children=[target])
        desktop, _ = _desktop_with_refs(monkeypatch, root, [_node("Save", (7, 8))])
        with pytest.raises(StaleRefError):
            desktop.resolve_element_ref(0)

    def test_no_uia_identity(self, monkeypatch):
        root = FakeControl("Win", (1,), children=[])
        desktop, _ = _desktop_with_refs(monkeypatch, root, [_node("Word", None)])
        with pytest.raises(StaleRefError) as exc:
            desktop.resolve_element_ref(0)
        assert "no UIA identity" in str(exc.value)

    def test_unsupported_pattern_is_not_stale(self, monkeypatch):
        target = FakeControl("Save", (7, 8), patterns={})
        root = FakeControl("Win", (1,), children=[target])
        desktop, _ = _desktop_with_refs(monkeypatch, root, [_node("Save", (7, 8))])
        with pytest.raises(ValueError) as exc:
            desktop.invoke_element(0)
        assert "UNSUPPORTED_PATTERN" in str(exc.value)
