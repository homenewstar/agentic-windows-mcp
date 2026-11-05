"""Sessions + JSONL trace (#20).

Sessions namespace the Desktop's snapshot ref registry: refs issued
under one session never resolve under another, and a cross-session
attempt fails with a STALE_REF naming the owning session. The trace
is an append-only JSONL, one segment per process, whose reader
tolerates a corrupt tail with counted warnings.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the Desktop service drives the Windows UIA stack"
)

from agentic_windows_mcp.desktop.service import Desktop, StaleRefError
from agentic_windows_mcp.infrastructure import trace as trace_module
from agentic_windows_mcp.infrastructure.sessions import SessionStore
from agentic_windows_mcp.tools import session as session_tools
from agentic_windows_mcp.tree.views import BoundingBox, TreeElementNode, TreeState


def _node(name, runtime_id, *, handle=4242):
    box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
    return TreeElementNode(
        bounding_box=box,
        center=box.get_center(),
        name=name,
        control_type="Button",
        window_name="Win",
        metadata={},
        runtime_id=runtime_id,
        window_handle=handle,
    )


def _desktop() -> Desktop:
    return Desktop.__new__(Desktop)


class TestSessionNamespacing:
    def test_refs_are_confined_to_their_session(self):
        desktop = _desktop()
        gen = desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("DefaultBtn", (1, 1))])
        )
        assert gen == 1

        info = desktop.activate_session("alpha")
        assert info == {"session": "alpha", "created": True, "generation": 0, "refs": 0}
        assert desktop.refs_generation() == 0
        assert desktop._element_refs == {}

        with pytest.raises(StaleRefError) as excinfo:
            desktop.resolve_element_ref(0)
        assert "belongs to session 'default'" in str(excinfo.value)
        assert "active session 'alpha'" in str(excinfo.value)

    def test_generations_and_refs_are_per_session(self):
        desktop = _desktop()
        desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("DefaultBtn", (1, 1))])
        )
        desktop.activate_session("alpha")
        gen_alpha = desktop.register_element_refs(
            TreeState(interactive_nodes=[_node("AlphaBtn", (2, 2))])
        )
        assert gen_alpha == 1  # alpha's own counter, not a global one
        assert desktop._element_refs[0].name == "AlphaBtn"

        assert desktop.end_session() == "alpha"
        assert desktop.active_session_id == "default"
        assert desktop.refs_generation() == 1
        assert desktop._element_refs[0].name == "DefaultBtn"

        info = desktop.activate_session("alpha")
        assert info["created"] is False
        assert desktop._element_refs[0].name == "AlphaBtn"  # resumed intact

    def test_invalid_session_ids_are_rejected(self):
        desktop = _desktop()
        for bad in ("../evil", "a/b", "", "x" * 65, "white space"):
            with pytest.raises(ValueError):
                desktop.activate_session(bad)

    def test_registry_properties_work_on_a_bare_instance(self):
        # Test rigs across the suite construct Desktop via __new__ and
        # assign the registry attributes directly; the session backing
        # store must not get in their way.
        desktop = _desktop()
        desktop._refs_generation = 7
        desktop._element_refs = {}
        desktop._refs_nodes = {}
        desktop._refs_tree_state = None
        assert desktop.refs_generation() == 7
        assert desktop.active_session_id == "default"


class TestSessionStore:
    def test_manifest_lifecycle(self, tmp_path):
        store = SessionStore(root=tmp_path)
        manifest = store.create("alpha", options={"via": "test"})
        assert manifest["created_at"] and manifest["ended_at"] is None
        assert store.create("alpha")["created_at"] == manifest["created_at"]
        ended = store.mark_ended("alpha")
        assert ended["ended_at"]
        assert [m["session_id"] for m in store.list_sessions()] == ["alpha"]
        assert store.read("missing") is None

    def test_store_rejects_traversal(self, tmp_path):
        store = SessionStore(root=tmp_path)
        with pytest.raises(ValueError):
            store.create("../evil")


class TestSessionTool:
    def _tool(self, monkeypatch, tmp_path, desktop):
        store = SessionStore(root=tmp_path)
        monkeypatch.setattr(session_tools, "SessionStore", lambda: store)

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self, **kwargs):
                def deco(fn):
                    self.tools[kwargs["name"]] = fn
                    return fn

                return deco

        mcp = FakeMCP()
        session_tools.register(
            mcp, get_desktop=lambda: desktop, get_analytics=lambda: None
        )
        return mcp.tools["Session"]

    def test_start_status_end_flow(self, monkeypatch, tmp_path):
        desktop = _desktop()
        tool = self._tool(monkeypatch, tmp_path, desktop)
        assert "Active session: 'default'" in asyncio.run(tool())
        started = asyncio.run(tool(mode="start", session_id="alpha"))
        assert "now active" in started
        status = asyncio.run(tool(mode="status"))
        assert "'alpha'" in status and "(active)" in status
        ended = asyncio.run(tool(mode="end"))
        assert "'alpha' ended" in ended
        assert desktop.active_session_id == "default"

    def test_start_rejects_bad_id(self, monkeypatch, tmp_path):
        tool = self._tool(monkeypatch, tmp_path, _desktop())
        assert asyncio.run(tool(mode="start", session_id="../x")).startswith("Error:")


class TestTrace:
    def test_segment_starts_with_a_meta_record(self, tmp_path):
        writer = trace_module.TraceWriter(tmp_path, server_version="1.2.3")
        writer.record("tool_call", tool="Snapshot", ok=True, duration_ms=5)
        writer.close()
        lines = writer.path.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0])
        assert meta["type"] == "meta"
        assert meta["schema"] == trace_module.SCHEMA_VERSION
        assert meta["pid"] == os.getpid()
        assert meta["server_version"] == "1.2.3"

    def test_tool_call_events_carry_no_arguments(self, tmp_path):
        writer = trace_module.TraceWriter(tmp_path)
        writer.record("tool_call", tool="SetValue", ok=True, duration_ms=3)
        writer.close()
        records, warnings = trace_module.read_trace(tmp_path)
        assert warnings == 0
        event = next(r for r in records if r.get("event") == "tool_call")
        assert set(event) == {"ts", "event", "tool", "ok", "duration_ms"}

    def test_reader_tolerates_a_corrupt_tail(self, tmp_path):
        writer = trace_module.TraceWriter(tmp_path)
        writer.record("session_started", session="alpha", created=True)
        writer.close()
        with writer.path.open("a", encoding="utf-8") as fh:
            fh.write("this is not json\n")
            fh.write('{"event": "truncated", "ts": "2026')
        records, warnings = trace_module.read_trace(tmp_path)
        assert warnings == 2
        assert any(r.get("event") == "session_started" for r in records)

    def test_reader_merges_segments_in_time_order(self, tmp_path):
        (tmp_path / "trace-111-20260101000000.jsonl").write_text(
            json.dumps({"ts": "2026-01-01T00:00:09", "event": "second"}) + "\n",
            encoding="utf-8",
        )
        (tmp_path / "trace-222-20260101000000.jsonl").write_text(
            json.dumps({"ts": "2026-01-01T00:00:01", "event": "first"}) + "\n",
            encoding="utf-8",
        )
        records, warnings = trace_module.read_trace(tmp_path)
        assert warnings == 0
        assert [r["event"] for r in records] == ["first", "second"]

    def test_screenshots_stay_off_unless_enabled(self, tmp_path):
        writer = trace_module.TraceWriter(tmp_path)
        writer.record("tool_call", tool="Click", ok=True, screenshot=b"PNG")
        writer.close()
        assert not list(tmp_path.glob("*.png"))
        records, _ = trace_module.read_trace(tmp_path)
        event = next(r for r in records if r.get("event") == "tool_call")
        assert "screenshot" not in event

    def test_enable_flag(self, monkeypatch):
        monkeypatch.delenv("AGENTIC_WINDOWS_MCP_TRACE", raising=False)
        assert trace_module.trace_enabled() is False
        monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TRACE", "1")
        assert trace_module.trace_enabled() is True
