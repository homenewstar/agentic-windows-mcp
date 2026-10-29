"""Issue #7 — Snapshot pagination beyond the 500-element capture cap, and the
Type tool's clear_first alias."""

from agentic_windows_mcp.tree.budget import TreeElementBudget, resolve_max_tree_elements
from agentic_windows_mcp.tree.views import (
    BoundingBox,
    Center,
    TreeElementNode,
    TreeState,
)


def _node(i: int) -> TreeElementNode:
    box = BoundingBox(left=i, top=i, right=i + 10, bottom=i + 10, width=10, height=10)
    return TreeElementNode(
        bounding_box=box,
        center=box.get_center(),
        name=f"Item {i}",
        control_type="Button",
        window_name="Dense Window",
        metadata={},
    )


def _state(n: int) -> TreeState:
    return TreeState(interactive_nodes=[_node(i) for i in range(n)])


class TestInteractivePage:
    def test_first_page_carries_global_indices(self):
        text = _state(10).interactive_page_to_string(0, 3)
        assert "[0]" in text and "[2]" in text
        assert "[3]" not in text
        assert 'window "Dense Window"' in text

    def test_later_page_continues_numbering(self):
        text = _state(10).interactive_page_to_string(7, 3)
        assert "[7]" in text and "[9]" in text
        assert "[6]" not in text

    def test_page_beyond_end_is_explicit(self):
        text = _state(4).interactive_page_to_string(10, 5)
        assert "No interactive elements in this page range" in text

    def test_partial_last_page(self):
        text = _state(8).interactive_page_to_string(6, 500)
        assert "[6]" in text and "[7]" in text


class TestBudgetFloor:
    def test_default_limit_is_500(self):
        assert resolve_max_tree_elements({}) == 500

    def test_min_elements_raises_the_floor(self):
        # Mirrors Tree.get_state: budget = max(env limit, min_elements).
        limit = max(resolve_max_tree_elements({}), 1200)
        budget = TreeElementBudget(limit)
        assert budget.limit == 1200
        for _ in range(700):
            assert budget.try_consume()
        assert not budget.truncated


class TestTypeClearFirst:
    def test_clear_first_is_accepted_and_ors_with_clear(self):
        # The tool-level merge logic: clear or clear_first -> clear.
        # Exercised directly here because the tool wrapper needs a desktop.
        from agentic_windows_mcp.desktop.utils import as_bool

        def merge(clear, clear_first):
            result = as_bool(clear, "clear")
            if clear_first is not None:
                result = result or as_bool(clear_first, "clear_first")
            return result

        assert merge(False, True) is True
        assert merge(False, "true") is True
        assert merge(True, None) is True
        assert merge(False, None) is False
        assert merge(False, False) is False
