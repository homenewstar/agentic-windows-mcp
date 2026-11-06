"""Issue #34 — window-scoped snapshot refs must carry UIA identity.

A window-scoped Snapshot crops the screenshot to the window's bounds by
default (#28), which routes the capture through the region filter in
Desktop.get_state. That filter rebuilt every element node from display
fields only, dropping runtime_id and window_handle — so refs registered
from a scoped capture had no identity to re-acquire and every ref tool
(Get/Is/Invoke/SetValue/Toggle, Batch, drill-down) refused them with
STALE_REF, while refs from a full-desktop capture of the same elements
resolved normally. These tests pin the unified behaviour: identity
evidence survives the region filter, lands on the registered refs, and
a scoped-capture ref resolves against the live window.
"""

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="element refs drive the Windows UIA stack"
)

from agentic_windows_mcp.desktop import service as desktop_service
from agentic_windows_mcp.desktop.service import Desktop, StaleRefError
from agentic_windows_mcp.tree.views import (
    BoundingBox,
    ScrollElementNode,
    TreeElementNode,
    TreeState,
)

HANDLE = 4242
REGION = BoundingBox(left=0, top=0, right=500, bottom=500, width=500, height=500)


class FakeControl:
    def __init__(self, name, runtime_id, *, localized_type="button",
                 automation_id="", children=()):
        self.Name = name
        self.LocalizedControlType = localized_type
        self.AutomationId = automation_id
        self.ProcessId = os.getpid()  # own pid: never trips the #14 UIPI guard
        self._rid = list(runtime_id)
        self._children = list(children)

    def GetRuntimeId(self):
        return list(self._rid)

    def GetChildren(self):
        return list(self._children)


def _node(name, runtime_id, *, automation_id="", handle=HANDLE):
    box = BoundingBox(left=10, top=10, right=60, bottom=40, width=50, height=30)
    metadata = {"automation_id": automation_id} if automation_id else {}
    return TreeElementNode(
        bounding_box=box,
        center=box.get_center(),
        name=name,
        control_type="Button",
        window_name="Win",
        metadata=metadata,
        runtime_id=runtime_id,
        window_handle=handle,
    )


def _scroll_node(name, runtime_id, *, handle=HANDLE):
    box = BoundingBox(left=10, top=50, right=200, bottom=300, width=190, height=250)
    return ScrollElementNode(
        name=name,
        control_type="Pane",
        window_name="Win",
        bounding_box=box,
        center=box.get_center(),
        metadata={"vertical_scrollable": True},
        runtime_id=runtime_id,
        window_handle=handle,
    )


def _desktop():
    desktop = Desktop.__new__(Desktop)
    desktop._refs_generation = 0
    desktop._element_refs = {}
    desktop._refs_tree_state = None
    desktop._refs_nodes = {}
    return desktop


def _scoped_capture_state(desktop, nodes, scrollable=()):
    """Simulate a window-scoped capture: walk output -> region filter.

    The scoped Snapshot defaults to cropping to the window's bounds,
    and Desktop.get_state applies the region filter to the tree before
    refs are registered — this is the exact step that dropped identity.
    """
    state = TreeState(
        interactive_nodes=list(nodes),
        scrollable_nodes=list(scrollable),
    )
    return desktop._filter_tree_state_to_region(state, REGION)


class TestRegionFilterPreservesIdentity:
    def test_interactive_node_identity_survives(self):
        desktop = _desktop()
        filtered = _scoped_capture_state(
            desktop, [_node("Save", (7, 8), automation_id="saveBtn")]
        )
        (node,) = filtered.interactive_nodes
        assert node.runtime_id == (7, 8)
        assert node.window_handle == HANDLE

    def test_scrollable_node_identity_survives(self):
        desktop = _desktop()
        filtered = _scoped_capture_state(
            desktop, [], scrollable=[_scroll_node("Results", (3, 4))]
        )
        (node,) = filtered.scrollable_nodes
        assert node.runtime_id == (3, 4)
        assert node.window_handle == HANDLE


class TestScopedRefs:
    def test_scoped_ref_carries_identity_evidence(self):
        desktop = _desktop()
        filtered = _scoped_capture_state(
            desktop, [_node("Save", (7, 8), automation_id="saveBtn")]
        )
        desktop.register_element_refs(filtered)
        ref = desktop._element_refs[0]
        assert ref.runtime_id == (7, 8)
        assert ref.window_handle == HANDLE
        assert ref.automation_id == "saveBtn"

    def test_scoped_ref_resolves_against_live_window(self, monkeypatch):
        desktop = _desktop()
        filtered = _scoped_capture_state(
            desktop, [_node("Save", (7, 8), automation_id="saveBtn")]
        )
        monkeypatch.setattr(
            desktop_service, "_window_identity", lambda handle: (1234, 999.0)
        )
        generation = desktop.register_element_refs(filtered)
        live = FakeControl("Save", (7, 8), automation_id="saveBtn")
        root = FakeControl(
            "Win", (1,), localized_type="window", children=[live]
        )
        monkeypatch.setattr(
            desktop_service.uia, "ControlFromHandle", lambda handle: root
        )
        ref, control = desktop.resolve_element_ref(0, snapshot=generation)
        assert control is live
        assert ref.runtime_id == (7, 8)

    def test_unfiltered_ref_still_resolves_the_same(self, monkeypatch):
        # The full-capture path (no region) is the reference behaviour;
        # both paths must mint interchangeable refs.
        desktop = _desktop()
        monkeypatch.setattr(
            desktop_service, "_window_identity", lambda handle: (1234, 999.0)
        )
        generation = desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("Save", (7, 8), automation_id="saveBtn")])
        )
        live = FakeControl("Save", (7, 8), automation_id="saveBtn")
        root = FakeControl(
            "Win", (1,), localized_type="window", children=[live]
        )
        monkeypatch.setattr(
            desktop_service.uia, "ControlFromHandle", lambda handle: root
        )
        ref, control = desktop.resolve_element_ref(0, snapshot=generation)
        assert control is live
