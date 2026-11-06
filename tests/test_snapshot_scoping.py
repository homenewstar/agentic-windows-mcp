"""Issue #18 — per-window snapshot scoping, drill-down by ref, and
explicit partial/truncation markers.

Covers the registry merge behind drill-down (labels and generation are
preserved, new elements append), window-candidate resolution, the
budget-cut markers the traversal stamps on semantic nodes, and the
completeness verdict those markers feed.
"""

import sys
from _ctypes import COMError
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="snapshot scoping drives the Windows UIA stack"
)

from agentic_windows_mcp.desktop import service as desktop_service
from agentic_windows_mcp.desktop.service import Desktop
from agentic_windows_mcp.tree.budget import TreeElementBudget
from agentic_windows_mcp.tree.service import Tree
from agentic_windows_mcp.tree.views import (
    BoundingBox,
    SemanticNode,
    TreeElementNode,
    TreeState,
    _prune_structural,
)
from agentic_windows_mcp.tools._snapshot_helpers import _capture_subtree_state
from agentic_windows_mcp.uia import (
    Rect,
    UIADeadElementError,
    UIANotSupportedError,
)


def _node(name, runtime_id, *, window_handle=4242):
    box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
    return TreeElementNode(
        bounding_box=box, center=box.get_center(), name=name,
        control_type="Button", window_name="Win", metadata={},
        runtime_id=runtime_id, window_handle=window_handle,
    )


def _desktop():
    desktop = Desktop.__new__(Desktop)
    desktop._refs_generation = 0
    desktop._element_refs = {}
    desktop._refs_tree_state = None
    desktop._refs_nodes = {}
    return desktop


class TestDrillDownRegistration:
    def test_extend_preserves_labels_and_generation(self):
        desktop = _desktop()
        gen = desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("A", (1,)), _node("B", (2,))])
        )
        assert gen == 1
        subtree_b = _node("B", (2,))
        returned = desktop.register_element_refs(
            TreeState(interactive_nodes=[subtree_b, _node("C", (3,))]),
            extend=True,
        )
        assert returned == 1  # Same identity scope: no new generation.
        assert sorted(desktop._element_refs) == [0, 1, 2]
        assert desktop._element_refs[2].name == "C"
        # The existing element's slot now holds the fresher node.
        assert desktop._refs_nodes[1] is subtree_b

    def test_extend_on_empty_registry_behaves_like_register(self):
        desktop = _desktop()
        gen = desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("A", (1,))]), extend=True
        )
        assert gen == 1
        assert sorted(desktop._element_refs) == [0]

    def test_wholesale_register_after_extend_replaces(self):
        desktop = _desktop()
        desktop.register_element_refs(TreeState(interactive_nodes=[_node("A", (1,))]))
        desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("B", (2,))]), extend=True
        )
        gen = desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("Z", (9,))])
        )
        assert gen == 2
        assert [ref.name for ref in desktop._element_refs.values()] == ["Z"]

    def test_extend_recomputes_uniqueness_across_the_merge(self):
        desktop = _desktop()
        desktop.register_element_refs(TreeState(interactive_nodes=[_node("OK", (1,))]))
        assert desktop._element_refs[0].unique_at_capture is True
        # The subtree reveals a twin with the same identity predicate.
        desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("OK", (2,))]), extend=True
        )
        assert desktop._element_refs[0].unique_at_capture is False
        assert desktop._element_refs[1].unique_at_capture is False


class TestWindowCandidates:
    def _rig(self, monkeypatch):
        desktop = _desktop()
        windows = [
            SimpleNamespace(name="Notepad — a.txt", handle=111),
            SimpleNamespace(name="Notepad — b.txt", handle=222),
            SimpleNamespace(name="Calculator", handle=333),
        ]
        monkeypatch.setattr(desktop, "get_controls_handles", lambda: set())
        monkeypatch.setattr(
            desktop, "get_windows",
            lambda controls_handles=None: (windows, set()),
        )
        monkeypatch.setattr(desktop, "get_active_window", lambda windows=None: None)
        processes = {111: "notepad.exe", 222: "notepad.exe", 333: "calc.exe"}
        monkeypatch.setattr(
            desktop_service, "_window_process_name", lambda handle: processes[handle]
        )
        return desktop

    def test_title_substring_can_be_ambiguous(self, monkeypatch):
        desktop = self._rig(monkeypatch)
        candidates = desktop.find_window_candidates("notepad")
        assert [c["handle"] for c in candidates] == [111, 222]

    def test_process_name_matches_with_or_without_exe(self, monkeypatch):
        desktop = self._rig(monkeypatch)
        assert [c["handle"] for c in desktop.find_window_candidates("calc.exe")] == [333]
        assert [c["handle"] for c in desktop.find_window_candidates("calc")] == [333]

    def test_unique_title_and_no_match(self, monkeypatch):
        desktop = self._rig(monkeypatch)
        assert [c["handle"] for c in desktop.find_window_candidates("calculator")] == [333]
        assert desktop.find_window_candidates("missing") == []


def _fake_button(name):
    button = MagicMock()
    button.CachedIsOffscreen = False
    button.CachedControlTypeName = "ButtonControl"
    button.CachedIsControlElement = True
    button.CachedBoundingRectangle = Rect(10, 10, 110, 60)
    button.CachedIsEnabled = True
    button.CachedHasKeyboardFocus = False
    button.CachedName = name
    button.CachedLocalizedControlType = "button"
    button.CachedAcceleratorKey = ""
    button.GetCachedPropertyValue.return_value = 43
    button.GetCachedPattern.return_value = None
    return button


def _fake_group(child_count=5):
    group = MagicMock()
    group.CachedIsOffscreen = False
    group.CachedControlTypeName = "GroupControl"
    group.CachedIsControlElement = True
    group.CachedBoundingRectangle = Rect(0, 0, 200, 200)
    group.CachedIsEnabled = True
    group.CachedHasKeyboardFocus = False
    group.CachedName = "Panel"
    group.CachedLocalizedControlType = "group"
    group.CachedAcceleratorKey = ""
    group.GetCachedPropertyValue.return_value = 43
    group.GetCachedPattern.return_value = None
    group._children = [_fake_button(f"B{i}") for i in range(child_count)]
    return group


@pytest.fixture
def tree_instance():
    mock_desktop = MagicMock()
    mock_desktop.get_screen_size.return_value = SimpleNamespace(width=1920, height=1080)
    mock_desktop.get_screen_box.return_value = BoundingBox(
        left=0, top=0, right=1920, bottom=1080, width=1920, height=1080
    )
    return Tree(mock_desktop)


class TestTruncationMarkers:
    def test_budget_cut_marks_the_container_and_the_budget(
        self, tree_instance, monkeypatch
    ):
        group = _fake_group(5)
        children = {id(group): group._children}
        monkeypatch.setattr(
            "agentic_windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            lambda node, cache_request: children.get(id(node), []),
        )
        monkeypatch.setattr(
            "agentic_windows_mcp.tree.service.random_point_within_bounding_box",
            lambda node, scale_factor: (60, 35),
        )
        monkeypatch.setattr(
            "agentic_windows_mcp.tree.service.AccessibleRoleNames", {43: "PushButton"}
        )
        tree_instance.element_budget = TreeElementBudget(2)
        window_sem = SemanticNode(
            control_type="Window", element_type="window",
            name="Win", window_name="Win",
        )
        interactive = []
        tree_instance.tree_traversal(
            group, Rect(0, 0, 200, 200), "Win", False,
            interactive, [], [], [], current_semantic_node=window_sem,
        )
        assert len(interactive) == 2
        assert tree_instance.element_budget.truncated is True
        group_sem = window_sem.children[0]
        assert group_sem.metadata["truncated"] is True
        assert group_sem.metadata["children_count"] == 5
        assert group_sem.metadata["children_shown"] == 2
        rendered = TreeState(semantic_tree_root=window_sem).semantic_tree_to_string()
        assert "[truncated — 5 children, not all shown]" in rendered

    def test_prune_keeps_a_cut_container(self):
        root = SemanticNode(control_type="Desktop", element_type="desktop", name="Desktop")
        cut = SemanticNode(
            control_type="Group", element_type="structural", name="Panel",
            metadata={"truncated": True, "children_count": 7},
        )
        empty = SemanticNode(control_type="Group", element_type="structural", name="Empty")
        root.add_child(cut)
        root.add_child(empty)
        _prune_structural(root)
        assert [child.name for child in root.children] == ["Panel"]


class TestCompleteness:
    def test_complete_requires_status_and_no_truncation(self):
        assert TreeState(status=True, truncated=False).is_complete() is True
        assert TreeState(status=True, truncated=True).is_complete() is False
        assert TreeState(status=False, truncated=False).is_complete() is False


class TestSubtreeCapture:
    def test_get_subtree_state_walks_from_the_given_control(
        self, tree_instance, monkeypatch
    ):
        group = _fake_group(0)
        group.Name = "Panel"
        group.LocalizedControlType = "group"
        group.BoundingRectangle = Rect(0, 0, 200, 200)
        monkeypatch.setattr(
            "agentic_windows_mcp.tree.service.ControlFromHandle",
            lambda handle: SimpleNamespace(Name="Win"),
        )
        monkeypatch.setattr(
            "agentic_windows_mcp.tree.service.CacheRequestFactory",
            SimpleNamespace(
                create_tree_traversal_cache=lambda: SimpleNamespace(TreeScope=0)
            ),
        )
        monkeypatch.setattr(
            "agentic_windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            lambda node, cache_request: [],
        )
        state = tree_instance.get_subtree_state(group, 4242)
        assert state.root_node.name == "Panel"
        assert state.semantic_tree_root.element_type == "structural"
        assert state.semantic_tree_root.name == "Panel"
        assert state.interactive_nodes == []
        assert state.is_complete() is True

    def test_drill_down_walks_the_roots_children(
        self, tree_instance, monkeypatch
    ):
        group = _fake_group(2)
        group.Name = "Panel"
        group.LocalizedControlType = "group"
        group.BoundingRectangle = Rect(0, 0, 200, 200)
        children = {id(group): group._children}
        _subtree_rig(monkeypatch)
        monkeypatch.setattr(
            "agentic_windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            lambda node, cache_request: children.get(id(node), []),
        )
        monkeypatch.setattr(
            "agentic_windows_mcp.tree.service.random_point_within_bounding_box",
            lambda node, scale_factor: (60, 35),
        )
        monkeypatch.setattr(
            "agentic_windows_mcp.tree.service.AccessibleRoleNames", {43: "PushButton"}
        )
        state = tree_instance.get_subtree_state(group, 4242)
        assert [node.name for node in state.interactive_nodes] == ["B0", "B1"]
        assert state.root_node.name == "Panel"
        assert state.is_complete() is True


def _subtree_rig(monkeypatch):
    monkeypatch.setattr(
        "agentic_windows_mcp.tree.service.ControlFromHandle",
        lambda handle: SimpleNamespace(Name="Win"),
    )
    monkeypatch.setattr(
        "agentic_windows_mcp.tree.service.CacheRequestFactory",
        SimpleNamespace(
            create_tree_traversal_cache=lambda: SimpleNamespace(TreeScope=0)
        ),
    )


class _RootWithFailingBounds:
    """A drill-down root whose live bounds read fails the way the live
    PC's Notepad document element did in issue #33."""

    def __init__(self, error):
        self._error = error
        self.Name = "Panel"
        self.LocalizedControlType = "group"

    @property
    def BoundingRectangle(self):
        raise self._error


class TestSubtreeCaptureFailures:
    """Issue #33: get_subtree_state converts raw COM failures into the
    typed UIA exceptions, exactly like get_nodes does for a window."""

    def test_unavailable_root_becomes_typed_dead_element_error(
        self, tree_instance, monkeypatch
    ):
        _subtree_rig(monkeypatch)
        root = _RootWithFailingBounds(
            COMError(
                -2147220991,
                "An event was unable to invoke any of the subscribers",
                (None, None, None, 0, None),
            )
        )
        with pytest.raises(UIADeadElementError) as excinfo:
            tree_instance.get_subtree_state(root, 4242)
        # The raw COMError must not escape: the typed exception carries
        # the UIA code instead (0x80040201 = UIA_E_ELEMENTNOTAVAILABLE).
        assert not isinstance(excinfo.value, COMError)
        assert excinfo.value.code == -2147220991

    def test_other_com_failure_becomes_its_typed_error(
        self, tree_instance, monkeypatch
    ):
        _subtree_rig(monkeypatch)
        root = _RootWithFailingBounds(
            COMError(-2147220988, "not supported", (None, None, None, 0, None))
        )
        with pytest.raises(UIANotSupportedError):
            tree_instance.get_subtree_state(root, 4242)


class TestSubtreeCaptureTranslation:
    """Issue #33: the snapshot helper translates typed UIA failures
    into the #13 message vocabulary (STALE_REF / UIA_CAPTURE_FAILED)."""

    def _desktop(self, side_effect=None, result=None):
        def get_subtree_state(root, handle):
            if side_effect is not None:
                raise side_effect
            return result

        return SimpleNamespace(
            tree=SimpleNamespace(get_subtree_state=get_subtree_state)
        )

    def test_success_passes_through(self):
        sentinel = object()
        desktop = self._desktop(result=sentinel)
        assert _capture_subtree_state(desktop, object(), 4242) is sentinel

    def test_dead_element_becomes_stale_ref(self):
        desktop = self._desktop(side_effect=UIADeadElementError(-2147220991, None))
        with pytest.raises(desktop_service.StaleRefError) as excinfo:
            _capture_subtree_state(desktop, object(), 4242)
        assert str(excinfo.value).startswith("STALE_REF:")

    def test_other_uia_failure_becomes_capture_failed(self):
        desktop = self._desktop(side_effect=UIANotSupportedError(-2147220988, None))
        with pytest.raises(RuntimeError) as excinfo:
            _capture_subtree_state(desktop, object(), 4242)
        assert str(excinfo.value).startswith("UIA_CAPTURE_FAILED:")
