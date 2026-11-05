"""Guide tool tests (#21): bundled playbooks, version stamped at serve time."""

from __future__ import annotations

import asyncio
from importlib import metadata

import pytest

from agentic_windows_mcp import __main__ as main_module
from agentic_windows_mcp.tools.guide import TOPICS, guide_text, server_version


def _guide():
    return main_module.mcp._tool_manager._tools["Guide"].fn


class TestGuideContent:
    def test_all_topics_serve_with_the_running_version(self):
        version = metadata.version("agentic-windows-mcp")
        assert server_version() == version
        for topic in TOPICS:
            text = guide_text(topic)
            assert f"agentic-windows-mcp {version}" in text
            assert f"guide topic: {topic}" in text
            assert len(text) > 500

    def test_topics_cover_their_promised_anchors(self):
        assert "STALE_REF" in guide_text("core")
        assert "observe" in guide_text("core").lower()
        assert "UIPI" in guide_text("windows")
        assert "Ctrl+Alt+Shift+Space" in guide_text("operations")
        assert "last_fault" in guide_text("operations")

    def test_version_is_injected_not_baked_in(self):
        # A version bump without touching the Markdown still serves the
        # new version: the stamp comes from the caller, not the file.
        stamped = guide_text("core", version="9.9.9-test")
        assert "agentic-windows-mcp 9.9.9-test" in stamped
        for topic in TOPICS:
            raw = guide_text(topic, version="9.9.9-test")
            assert "0.8." not in raw  # no stale baked-in version anywhere


class TestGuideTool:
    def test_default_topic_is_core(self):
        result = asyncio.run(_guide()())
        assert "guide topic: core" in result

    def test_unknown_topic_lists_the_available_ones(self):
        result = asyncio.run(_guide()(topic="nope"))
        assert "Unknown guide topic" in result
        for topic in TOPICS:
            assert topic in result

    def test_topic_matching_is_forgiving(self):
        result = asyncio.run(_guide()(topic=" Windows "))
        assert "guide topic: windows" in result
