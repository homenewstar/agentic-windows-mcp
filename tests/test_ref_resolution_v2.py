"""Issue #12 — strict ref resolution v2.

Refs carry full identity evidence (AutomationId, uniqueness at capture,
window owner pid + process creation-time token). Resolution tries the
RuntimeId first; when it no longer resolves, the identity predicate is
counted over the live window: 1 match resolves, 0 is STALE_REF, 2+ is
AMBIGUOUS_TARGET, an unreadable field is RESOLUTION_UNKNOWN, and a window
handle whose owner changed since capture fails closed as recycled.
"""

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="element refs drive the Windows UIA stack"
)

from agentic_windows_mcp.desktop import service as desktop_service
from agentic_windows_mcp.desktop.service import (
    AmbiguousRefError,
    Desktop,
    StaleRefError,
)
from agentic_windows_mcp.tree.views import BoundingBox, TreeElementNode, TreeState
from agentic_windows_mcp.uia import PatternId


class FakePattern:
    def __init__(self):
        self.calls = []

    def Invoke(self):
        self.calls.append("Invoke")
        return True


class FakeControl:
    def __init__(self, name, runtime_id, *, localized_type="button",
                 automation_id="", children=(), patterns=None):
        self.Name = name
        self.LocalizedControlType = localized_type
        self.AutomationId = automation_id
        self.ProcessId = os.getpid()  # own pid: never trips the #14 UIPI guard
        self._rid = list(runtime_id)
        self._children = list(children)
        self._patterns = patterns or {}

    def GetRuntimeId(self):
        return list(self._rid)

    def GetChildren(self):
        return list(self._children)

    def GetPattern(self, pattern_id):
        return self._patterns.get(pattern_id)


class ExplodingNameControl(FakeControl):
    @property
    def Name(self):
        raise RuntimeError("name read failed")

    @Name.setter
    def Name(self, value):
        pass


def _node(name, runtime_id, *, automation_id="", handle=4242, metadata=None):
    box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
    meta = dict(metadata or {})
    if automation_id:
        meta["automation_id"] = automation_id
    return TreeElementNode(
        bounding_box=box,
        center=box.get_center(),
        name=name,
        control_type="Button",
        window_name="Win",
        metadata=meta,
        runtime_id=runtime_id,
        window_handle=handle,
    )


def _desktop(monkeypatch, root, nodes):
    desktop = Desktop.__new__(Desktop)
    desktop._refs_generation = 0
    desktop._element_refs = {}
    generation = desktop.register_element_refs(TreeState(interactive_nodes=nodes))
    monkeypatch.setattr(
        desktop_service.uia, "ControlFromHandle", lambda handle: root
    )
    return desktop, generation


def _root(children):
    return FakeControl("Win", (1,), localized_type="window", children=children)


class TestEvidence:
    def test_ref_records_identity_evidence(self, monkeypatch):
        desktop, _ = _desktop(
            monkeypatch, _root([]),
            [_node("Save", (7, 8), automation_id="saveBtn",
                   metadata={"help_text": "Saves the file"})],
        )
        ref = desktop._element_refs[0]
        assert ref.automation_id == "saveBtn"
        assert ref.description == "Saves the file"
        assert ref.unique_at_capture is True

    def test_twins_are_not_unique_at_capture(self, monkeypatch):
        nodes = [
            _node("Delete", (7, 8), automation_id="delBtn"),
            _node("Delete", (7, 9), automation_id="delBtn"),
        ]
        desktop, _ = _desktop(monkeypatch, _root([]), nodes)
        assert desktop._element_refs[0].unique_at_capture is False
        assert desktop._element_refs[1].unique_at_capture is False


class TestCountingFallback:
    def test_runtime_id_churn_resolves_by_counting(self, monkeypatch):
        pattern = FakePattern()
        live = FakeControl(
            "Save", (9, 9), automation_id="saveBtn",
            patterns={PatternId.InvokePattern: pattern},
        )
        desktop, gen = _desktop(
            monkeypatch, _root([live]),
            [_node("Save", (7, 8), automation_id="saveBtn")],
        )
        desktop.invoke_element(0, snapshot=gen)
        assert pattern.calls == ["Invoke"]

    def test_zero_matches_is_stale(self, monkeypatch):
        live = FakeControl("Other", (9, 9), automation_id="otherBtn")
        desktop, gen = _desktop(
            monkeypatch, _root([live]),
            [_node("Save", (7, 8), automation_id="saveBtn")],
        )
        with pytest.raises(StaleRefError) as excinfo:
            desktop.invoke_element(0, snapshot=gen)
        assert str(excinfo.value).startswith("STALE_REF:")

    def test_two_live_matches_are_ambiguous(self, monkeypatch):
        twins = [
            FakeControl("Save", (9, 9), automation_id="saveBtn"),
            FakeControl("Save", (9, 10), automation_id="saveBtn"),
        ]
        desktop, gen = _desktop(
            monkeypatch, _root(twins),
            [_node("Save", (7, 8), automation_id="saveBtn")],
        )
        with pytest.raises(AmbiguousRefError) as excinfo:
            desktop.invoke_element(0, snapshot=gen)
        assert str(excinfo.value).startswith("AMBIGUOUS_TARGET:")

    def test_non_unique_at_capture_refused_without_counting(self, monkeypatch):
        # One live element matches the predicate, but the ref was a twin at
        # capture and its RuntimeId is gone: no search may pick it out.
        live = FakeControl(
            "Delete", (5, 5), automation_id="delBtn",
            patterns={PatternId.InvokePattern: FakePattern()},
        )
        nodes = [
            _node("Delete", (7, 8), automation_id="delBtn"),
            _node("Delete", (7, 9), automation_id="delBtn"),
        ]
        desktop, gen = _desktop(monkeypatch, _root([live]), nodes)
        with pytest.raises(AmbiguousRefError):
            desktop.invoke_element(0, snapshot=gen)

    def test_unreadable_field_is_unproven_not_stale(self, monkeypatch):
        live = ExplodingNameControl("Save", (9, 9), automation_id="saveBtn")
        desktop, gen = _desktop(
            monkeypatch, _root([live]),
            [_node("Save", (7, 8), automation_id="saveBtn")],
        )
        with pytest.raises(TimeoutError) as excinfo:
            desktop.invoke_element(0, snapshot=gen)
        assert str(excinfo.value).startswith("RESOLUTION_UNKNOWN:")


class TestWindowIdentity:
    def _desktop_with_identity(self, monkeypatch, identity_sequence):
        pattern = FakePattern()
        live = FakeControl(
            "Save", (7, 8), automation_id="saveBtn",
            patterns={PatternId.InvokePattern: pattern},
        )
        desktop = Desktop.__new__(Desktop)
        desktop._refs_generation = 0
        desktop._element_refs = {}
        state = {"current": identity_sequence[0]}
        monkeypatch.setattr(
            desktop_service, "_window_identity", lambda handle: state["current"]
        )
        generation = desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("Save", (7, 8), automation_id="saveBtn")])
        )
        monkeypatch.setattr(
            desktop_service.uia, "ControlFromHandle", lambda handle: _root([live])
        )
        return desktop, generation, state, pattern

    def test_pid_change_means_recycled_handle(self, monkeypatch):
        desktop, gen, state, _ = self._desktop_with_identity(
            monkeypatch, [(111, 1000.0)]
        )
        state["current"] = (222, 1000.0)
        with pytest.raises(StaleRefError) as excinfo:
            desktop.invoke_element(0, snapshot=gen)
        assert "recycled" in str(excinfo.value)

    def test_process_restart_same_pid_means_recycled_handle(self, monkeypatch):
        desktop, gen, state, _ = self._desktop_with_identity(
            monkeypatch, [(111, 1000.0)]
        )
        state["current"] = (111, 2000.0)
        with pytest.raises(StaleRefError) as excinfo:
            desktop.invoke_element(0, snapshot=gen)
        assert "recycled" in str(excinfo.value)

    def test_same_identity_still_resolves(self, monkeypatch):
        desktop, gen, state, pattern = self._desktop_with_identity(
            monkeypatch, [(111, 1000.0)]
        )
        desktop.invoke_element(0, snapshot=gen)
        assert pattern.calls == ["Invoke"]
