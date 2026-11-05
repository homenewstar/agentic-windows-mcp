"""Issue #17 — Find / Get / Is query tools over the current snapshot.

Find searches the snapshot registry (the same global labels a paged
Snapshot prints); Get reads one live property and Is evaluates one
predicate, both through the strict resolver, with tri-state honesty:
an unreadable property or predicate is "unknown", never guessed, and
a password value is never read back.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="element refs drive the Windows UIA stack"
)

from agentic_windows_mcp.desktop import service as desktop_service
from agentic_windows_mcp.desktop.service import Desktop, StaleRefError
from agentic_windows_mcp.tree.views import (
    BoundingBox,
    Center,
    SemanticNode,
    TextElementNode,
    TreeElementNode,
    TreeState,
)
from agentic_windows_mcp.uia import PatternId

RECT = SimpleNamespace(left=0, top=0, right=10, bottom=10)


class FakeValuePattern:
    def __init__(self, value="", readonly=False):
        self.Value = value
        self.IsReadOnly = readonly


class FakeTogglePattern:
    def __init__(self, state):
        self.ToggleState = state


class FakeSelectionItemPattern:
    def __init__(self, selected):
        self.IsSelected = selected


class FakeExpandCollapsePattern:
    def __init__(self, state):
        self.ExpandCollapseState = state


class FakeControl:
    def __init__(self, name, runtime_id, *, patterns=None, enabled=True,
                 offscreen=False, focused=False):
        self.Name = name
        self.LocalizedControlType = "button"
        self.AutomationId = ""
        self.IsPassword = False
        self.HasKeyboardFocus = focused
        self.BoundingRectangle = RECT
        self.ProcessId = os.getpid()
        self._rid = list(runtime_id)
        self._patterns = patterns or {}
        self._enabled = enabled
        self._offscreen = offscreen

    @property
    def IsEnabled(self):
        return self._enabled

    @property
    def IsOffscreen(self):
        return self._offscreen

    def GetRuntimeId(self):
        return list(self._rid)

    def GetChildren(self):
        return []

    def GetPattern(self, pattern_id):
        return self._patterns.get(pattern_id)


class ExplodingEnabledControl(FakeControl):
    @property
    def IsEnabled(self):
        raise RuntimeError("provider exploded")


def _node(name, control_type, runtime_id, *, window_name="Win", handle=4242,
          metadata=None, x=5, y=5):
    box = BoundingBox(left=x - 5, top=y - 5, right=x + 5, bottom=y + 5,
                      width=10, height=10)
    return TreeElementNode(
        bounding_box=box, center=box.get_center(), name=name,
        control_type=control_type, window_name=window_name,
        metadata=metadata or {}, runtime_id=runtime_id, window_handle=handle,
    )


def _desktop(monkeypatch, nodes, live=None, **tree_kwargs):
    desktop = Desktop.__new__(Desktop)
    desktop._refs_generation = 0
    desktop._element_refs = {}
    gen = desktop.register_element_refs(
        TreeState(interactive_nodes=nodes, **tree_kwargs)
    )
    if live is not None:
        root = FakeControl("Win", (1,))
        root.GetChildren = lambda: list(live)
        monkeypatch.setattr(
            desktop_service.uia, "ControlFromHandle", lambda handle: root
        )
    return desktop, gen


class TestFind:
    def _rig(self, monkeypatch):
        nodes = [
            _node("Save", "Button", (7, 1), metadata={"automation_id": "btnSave"}),
            _node("Save As", "Button", (7, 2), metadata={"automation_id": "btnSaveAs"}),
            _node("Name", "Edit", (7, 3), window_name="Other Window"),
        ]
        return _desktop(
            monkeypatch, nodes,
            dom_informative_nodes=[TextElementNode(text="Save your work often")],
        )

    def test_substring_match_returns_global_labels(self, monkeypatch):
        desktop, gen = self._rig(monkeypatch)
        result = desktop.find_elements(text="save")
        assert result["generation"] == gen
        assert [m["label"] for m in result["matches"]] == [0, 1]
        assert result["matches"][0]["automation_id"] == "btnSave"

    def test_exact_match_is_full_string(self, monkeypatch):
        desktop, _ = self._rig(monkeypatch)
        result = desktop.find_elements(text="Save", exact=True)
        assert [m["label"] for m in result["matches"]] == [0]

    def test_role_and_automation_id_filters(self, monkeypatch):
        desktop, _ = self._rig(monkeypatch)
        assert [m["label"] for m in desktop.find_elements(role="edit")["matches"]] == [2]
        assert [
            m["label"]
            for m in desktop.find_elements(automation_id="btnSaveAs")["matches"]
        ] == [1]

    def test_window_filter(self, monkeypatch):
        desktop, _ = self._rig(monkeypatch)
        result = desktop.find_elements(window="other")
        assert [m["label"] for m in result["matches"]] == [2]
        assert result["matches"][0]["window_name"] == "Other Window"

    def test_static_text_matches_have_no_ref(self, monkeypatch):
        desktop, _ = self._rig(monkeypatch)
        result = desktop.find_elements(text="save your")
        assert result["matches"] == []
        assert result["static_matches"] == ["Save your work often"]
        # Role/automation filters exclude static text entirely.
        assert desktop.find_elements(text="save", role="Button")["total_static"] == 0

    def test_semantic_path_is_reported(self, monkeypatch):
        node = _node("Save", "Button", (7, 1))
        semantic = SemanticNode(
            control_type="Desktop", element_type="desktop",
            children=[
                SemanticNode(
                    control_type="Window", element_type="window", name="Win",
                    children=[
                        SemanticNode(
                            control_type="Button", element_type="interactive",
                            name="Save", center=Center(5, 5),
                        )
                    ],
                )
            ],
        )
        desktop, _ = _desktop(monkeypatch, [node], semantic_tree_root=semantic)
        result = desktop.find_elements(text="Save")
        assert result["matches"][0]["path"] == "Win"

    def test_no_snapshot_yet(self, monkeypatch):
        desktop = Desktop.__new__(Desktop)
        desktop._refs_generation = 0
        desktop._element_refs = {}
        desktop._refs_tree_state = None
        result = desktop.find_elements(text="x")
        assert result["has_snapshot"] is False
        assert result["matches"] == []

    def test_superseded_snapshot_param_is_stale(self, monkeypatch):
        desktop, gen = self._rig(monkeypatch)
        with pytest.raises(StaleRefError, match="STALE_REF"):
            desktop.find_elements(text="save", snapshot=gen + 9)


class TestGetIs:
    def _rig(self, monkeypatch, control, *, metadata=None):
        node = _node("Field", "Edit", (7, 8), metadata=metadata or {})
        return _desktop(monkeypatch, [node], live=[control])

    def test_get_value_and_enabled(self, monkeypatch):
        control = FakeControl(
            "Field", (7, 8),
            patterns={PatternId.ValuePattern: FakeValuePattern("hello")},
        )
        desktop, _ = self._rig(monkeypatch, control)
        _ref, known, value = desktop.get_ref_property(0, "value")
        assert (known, value) == (True, "hello")
        _ref, known, value = desktop.get_ref_property(0, "enabled")
        assert (known, value) == (True, True)
        _ref, known, value = desktop.get_ref_property(0, "bounds")
        assert (known, value) == (True, [0, 0, 10, 10])

    def test_get_password_value_is_withheld(self, monkeypatch):
        control = FakeControl(
            "Field", (7, 8),
            patterns={PatternId.ValuePattern: FakeValuePattern("hunter2")},
        )
        desktop, _ = self._rig(monkeypatch, control, metadata={"is_password": True})
        _ref, known, value = desktop.get_ref_property(0, "value")
        assert known is True
        assert "hunter2" not in value
        assert "secure field" in value

    def test_get_unexposed_property_is_unknown(self, monkeypatch):
        control = FakeControl("Field", (7, 8), patterns={})
        desktop, _ = self._rig(monkeypatch, control)
        _ref, known, value = desktop.get_ref_property(0, "toggle_state")
        assert known is False
        assert value is None

    def test_get_unknown_property_name_rejected(self, monkeypatch):
        control = FakeControl("Field", (7, 8))
        desktop, _ = self._rig(monkeypatch, control)
        with pytest.raises(ValueError, match="Unknown property"):
            desktop.get_ref_property(0, "nonsense")

    def test_get_on_stale_ref_raises_typed_error(self, monkeypatch):
        control = FakeControl("Field", (7, 8))
        desktop, _ = self._rig(monkeypatch, control)
        with pytest.raises(StaleRefError, match="STALE_REF"):
            desktop.get_ref_property(99, "name")

    def test_is_predicates(self, monkeypatch):
        control = FakeControl(
            "Field", (7, 8), offscreen=True,
            patterns={PatternId.TogglePattern: FakeTogglePattern(1)},
        )
        desktop, _ = self._rig(monkeypatch, control)
        assert desktop.evaluate_ref_predicate(0, "enabled")[1] is True
        assert desktop.evaluate_ref_predicate(0, "visible")[1] is False
        assert desktop.evaluate_ref_predicate(0, "checked")[1] is True

    def test_is_indeterminate_checked_is_unknown(self, monkeypatch):
        control = FakeControl(
            "Field", (7, 8),
            patterns={PatternId.TogglePattern: FakeTogglePattern(2)},
        )
        desktop, _ = self._rig(monkeypatch, control)
        assert desktop.evaluate_ref_predicate(0, "checked")[1] is None

    def test_is_failed_read_is_unknown_not_false(self, monkeypatch):
        control = ExplodingEnabledControl("Field", (7, 8))
        desktop, _ = self._rig(monkeypatch, control)
        assert desktop.evaluate_ref_predicate(0, "enabled")[1] is None

    def test_is_editable_and_expanded(self, monkeypatch):
        control = FakeControl(
            "Field", (7, 8),
            patterns={
                PatternId.ValuePattern: FakeValuePattern("x", readonly=True),
                PatternId.ExpandCollapsePattern: FakeExpandCollapsePattern(1),
            },
        )
        desktop, _ = self._rig(monkeypatch, control)
        assert desktop.evaluate_ref_predicate(0, "editable")[1] is False
        assert desktop.evaluate_ref_predicate(0, "expanded")[1] is True


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *, name, **kwargs):
        def decorator(func):
            self.tools[name] = func
            return func

        return decorator


class TestQueryTools:
    def _rig(self, monkeypatch):
        from agentic_windows_mcp.tools import query

        control = FakeControl(
            "Save", (7, 1),
            patterns={PatternId.InvokePattern: object()},
        )
        node = _node("Save", "Button", (7, 1),
                     metadata={"automation_id": "btnSave"})
        desktop, _gen = _desktop(monkeypatch, [node], live=[control])
        mcp = FakeMCP()
        query.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
        return mcp

    def test_find_tool_lists_refs(self, monkeypatch):
        mcp = self._rig(monkeypatch)
        result = asyncio.run(mcp.tools["Find"](text="sav"))
        assert "@e0" in result
        assert "btnSave" in result

    def test_find_tool_requires_a_filter(self, monkeypatch):
        mcp = self._rig(monkeypatch)
        with pytest.raises(ValueError, match="at least one filter"):
            asyncio.run(mcp.tools["Find"]())

    def test_get_tool_on_stale_ref_is_typed(self, monkeypatch):
        mcp = self._rig(monkeypatch)
        with pytest.raises(ValueError, match="STALE_REF"):
            asyncio.run(mcp.tools["Get"](ref="@e99", property="name"))

    def test_is_tool_reports_true(self, monkeypatch):
        mcp = self._rig(monkeypatch)
        result = asyncio.run(mcp.tools["Is"](ref="@e0", predicate="enabled"))
        assert result == "Is @e0 enabled: true"
