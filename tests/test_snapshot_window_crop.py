"""Issue #28 — browser AX drill-down, window-bounds helper, and the
window-scoped Snapshot screenshot crop.

The wrapper-descent keep/skip decision is a measurement, not code: it
lives in probes/windows/FINDINGS.md row W-007, backed by the
bench_locator wrapper measurement.
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from agentic_windows_mcp.browser.devtools_manager import BrowserDevToolsManager

pytestmark_win32 = pytest.mark.skipif(
    sys.platform != "win32", reason="UIA window bounds need the Windows stack"
)


# ---------------------------------------------------------------------------
# Browser accessibility-tree drill-down
# ---------------------------------------------------------------------------

def _ax_node(node_id, role, name, child_ids, parent_id=None):
    node = {
        "nodeId": node_id,
        "role": {"value": role},
        "name": {"value": name},
        "ignored": False,
        "childIds": child_ids,
    }
    if parent_id is not None:
        node["parentId"] = parent_id
    return node


class _FakeSession:
    def __init__(self, nodes):
        self._nodes = nodes

    async def send(self, method, params=None):
        assert method == "Accessibility.getFullAXTree"
        return {"nodes": self._nodes}


def _manager(nodes):
    manager = BrowserDevToolsManager.__new__(BrowserDevToolsManager)
    page = SimpleNamespace(url="https://example.test/")
    session = _FakeSession(nodes)

    async def _page(page_id=None):
        return page

    async def _cdp(p):
        return session, None

    async def _domains(s, *names):
        return None

    manager._get_page_by_id_or_active = _page
    manager._get_cdp = _cdp
    manager._ensure_domains = _domains
    return manager


def _nodes():
    return [
        _ax_node(1, "RootWebArea", "Page", [2, 4]),
        _ax_node(2, "main", "Content", [3], parent_id=1),
        _ax_node(3, "button", "Save", [], parent_id=2),
        _ax_node(4, "banner", "Top", [], parent_id=1),
    ]


class TestAxDrillDown:
    def test_full_tree_carries_node_ids(self):
        manager = _manager(_nodes())
        result = asyncio.run(manager._get_accessibility_tree_internal())
        tree = result["tree"]
        assert tree["node_id"] == 1
        assert [c["node_id"] for c in tree["children"]] == [2, 4]
        assert "root_node_id" not in result

    def test_drill_roots_at_the_node_with_its_subtree_only(self):
        manager = _manager(_nodes())
        result = asyncio.run(
            manager._get_accessibility_tree_internal(root_node_id=2)
        )
        assert result["root_node_id"] == 2
        assert result["node_count"] == 4  # the capture is the whole page
        tree = result["tree"]
        assert tree["node_id"] == 2
        assert [c["node_id"] for c in tree["children"]] == [3]
        assert tree["children"][0]["name"] == "Save"

    def test_drill_unknown_node_id_raises(self):
        manager = _manager(_nodes())
        with pytest.raises(ValueError, match="root_node_id 99"):
            asyncio.run(manager._get_accessibility_tree_internal(root_node_id=99))


# ---------------------------------------------------------------------------
# Window bounds + scoped screenshot crop
# ---------------------------------------------------------------------------

@pytestmark_win32
class TestWindowBounds:
    def _desktop(self, monkeypatch, rect):
        from agentic_windows_mcp.desktop import service as desktop_service
        from agentic_windows_mcp.desktop.service import Desktop

        desktop = Desktop.__new__(Desktop)
        if rect is None:
            control = None
        else:
            control = SimpleNamespace(
                BoundingRectangle=SimpleNamespace(
                    left=rect[0], top=rect[1], right=rect[2], bottom=rect[3]
                )
            )
        monkeypatch.setattr(
            desktop_service.uia, "ControlFromHandle", lambda handle: control
        )
        return desktop

    def test_live_bounds(self, monkeypatch):
        desktop = self._desktop(monkeypatch, (10, 20, 410, 320))
        assert desktop.get_window_bounds(4242) == (10, 20, 410, 320)

    def test_window_gone(self, monkeypatch):
        desktop = self._desktop(monkeypatch, None)
        assert desktop.get_window_bounds(4242) is None

    def test_minimised_sentinel_is_not_a_bounds(self, monkeypatch):
        desktop = self._desktop(monkeypatch, (-32000, -32000, -31840, -31960))
        assert desktop.get_window_bounds(4242) is None

    def test_zero_area_is_not_a_bounds(self, monkeypatch):
        desktop = self._desktop(monkeypatch, (10, 10, 10, 50))
        assert desktop.get_window_bounds(4242) is None


class _CropStubDesktop:
    def __init__(self, bounds):
        self._bounds = bounds
        self.bounds_lookups = []

    def get_window_bounds(self, handle):
        self.bounds_lookups.append(handle)
        return self._bounds


def _snapshot_tool(monkeypatch, desktop):
    from agentic_windows_mcp.tools import snapshot as snapshot_tools
    from tests.test_multi_tools import FakeMCP

    calls = {}

    def fake_capture(desktop_arg, **kwargs):
        calls.update(kwargs)
        return {}

    monkeypatch.setattr(snapshot_tools, "capture_desktop_state", fake_capture)
    monkeypatch.setattr(
        snapshot_tools, "build_snapshot_response",
        lambda result, include_ui_details: ["ok"],
    )
    mcp = FakeMCP()
    snapshot_tools.register(
        mcp, get_desktop=lambda: desktop, get_analytics=lambda: None
    )
    return mcp.tools["Snapshot"], calls


class TestScopedCrop:
    def test_scoped_snapshot_crops_to_window_bounds(self, monkeypatch):
        desktop = _CropStubDesktop((10, 20, 410, 320))
        tool, calls = _snapshot_tool(monkeypatch, desktop)
        result = asyncio.run(tool(window=4242, use_vision=True))
        assert result == ["ok"]
        assert calls["region"] == [10, 20, 410, 320]
        assert "cropped to the window's bounds" in calls["capture_note"]

    def test_crop_can_be_turned_off(self, monkeypatch):
        desktop = _CropStubDesktop((10, 20, 410, 320))
        tool, calls = _snapshot_tool(monkeypatch, desktop)
        asyncio.run(tool(window=4242, crop_to_window=False))
        assert calls["region"] is None
        assert desktop.bounds_lookups == []

    def test_explicit_region_wins_over_crop(self, monkeypatch):
        desktop = _CropStubDesktop((10, 20, 410, 320))
        tool, calls = _snapshot_tool(monkeypatch, desktop)
        asyncio.run(tool(window=4242, region=[0, 0, 100, 100]))
        assert calls["region"] == [0, 0, 100, 100]
        assert desktop.bounds_lookups == []

    def test_unusable_bounds_fall_back_to_full_screen(self, monkeypatch):
        desktop = _CropStubDesktop(None)
        tool, calls = _snapshot_tool(monkeypatch, desktop)
        asyncio.run(tool(window=4242))
        assert calls["region"] is None

    def test_unscoped_snapshot_never_crops(self, monkeypatch):
        desktop = _CropStubDesktop((10, 20, 410, 320))
        tool, calls = _snapshot_tool(monkeypatch, desktop)
        asyncio.run(tool())
        assert calls["region"] is None
        assert desktop.bounds_lookups == []
