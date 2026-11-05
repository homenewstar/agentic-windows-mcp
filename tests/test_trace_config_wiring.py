"""Issue #29 — config-driven trace switch + wired replay screenshots.

#20's trace could only be enabled by an environment variable, and its
replay-screenshot writer had no caller. Now: [trace] in config.toml is
the switch (env remains an override for `enabled` only, and can never
enable screenshots), the tool-call wrapper stores pre/post frames when
capture is explicitly on, and a Session start can request trace +
artifacts for itself.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from agentic_windows_mcp.infrastructure import trace as trace_module
from agentic_windows_mcp.infrastructure.analytics import with_analytics
from agentic_windows_mcp.infrastructure.config import (
    TraceConfig,
    WindowsMCPConfig,
    load_config,
    write_config,
)

win32_only = pytest.mark.skipif(
    sys.platform != "win32", reason="the Desktop service drives the Windows UIA stack"
)


@pytest.fixture(autouse=True)
def _reset_trace_singleton():
    yield
    writer = trace_module._writer
    if writer is not None:
        writer.close()
    trace_module._writer = None
    trace_module._screenshot_provider = None


class TestTraceConfigParsing:
    def test_trace_section_is_parsed(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text(
            '[trace]\nenabled = true\ndirectory = "D:/traces"\n'
            "capture_screenshots = true\n",
            encoding="utf-8",
        )
        cfg = load_config(path)
        assert cfg.trace.enabled is True
        assert cfg.trace.directory == "D:/traces"
        assert cfg.trace.capture_screenshots is True

    def test_defaults_are_off(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("[server]\nport = 8000\n", encoding="utf-8")
        cfg = load_config(path)
        assert cfg.trace == TraceConfig()

    def test_write_config_round_trips_trace(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = WindowsMCPConfig()
        cfg.trace.enabled = True
        cfg.trace.capture_screenshots = True
        write_config(cfg, path)
        text = path.read_text(encoding="utf-8")
        assert "[trace]" in text and "capture_screenshots = true" in text
        assert load_config(path).trace == cfg.trace

    def test_write_config_omits_default_trace(self, tmp_path):
        path = tmp_path / "config.toml"
        write_config(WindowsMCPConfig(), path)
        assert "[trace]" not in path.read_text(encoding="utf-8")

    def test_bad_types_are_rejected(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[trace]\nenabled = "yes"\n', encoding="utf-8")
        with pytest.raises(ValueError):
            load_config(path)


class TestResolveSettings:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("AGENTIC_WINDOWS_MCP_TRACE", raising=False)
        settings = trace_module.resolve_trace_settings(TraceConfig())
        assert settings == {
            "enabled": False,
            "directory": None,
            "capture_screenshots": False,
        }

    def test_config_enables(self, monkeypatch):
        monkeypatch.delenv("AGENTIC_WINDOWS_MCP_TRACE", raising=False)
        config = TraceConfig(enabled=True, directory="D:/traces",
                             capture_screenshots=True)
        settings = trace_module.resolve_trace_settings(config)
        assert settings["enabled"] is True
        assert settings["capture_screenshots"] is True
        assert str(settings["directory"]) == "D:\\traces" or \
            settings["directory"].as_posix() == "D:/traces"

    def test_env_overrides_in_both_directions(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TRACE", "0")
        assert trace_module.resolve_trace_settings(
            TraceConfig(enabled=True))["enabled"] is False
        monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TRACE", "1")
        assert trace_module.resolve_trace_settings(
            TraceConfig(enabled=False))["enabled"] is True

    def test_env_can_never_enable_screenshots(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TRACE", "1")
        settings = trace_module.resolve_trace_settings(TraceConfig())
        assert settings["enabled"] is True
        assert settings["capture_screenshots"] is False


class TestInitAndEnsure:
    def test_init_trace_uses_config_directory(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENTIC_WINDOWS_MCP_TRACE", raising=False)
        config = TraceConfig(enabled=True, directory=str(tmp_path))
        writer = trace_module.init_trace(server_version="9.9", config=config)
        assert writer is not None
        assert writer.path.parent == tmp_path
        assert writer.capture_screenshots is False
        writer.record("tool_call", tool="Click", ok=True, duration_ms=1)
        records, warnings = trace_module.read_trace(tmp_path)
        assert warnings == 0
        assert any(r.get("event") == "tool_call" for r in records)

    def test_init_trace_stays_off_without_a_switch(self, monkeypatch):
        monkeypatch.delenv("AGENTIC_WINDOWS_MCP_TRACE", raising=False)
        assert trace_module.init_trace(config=TraceConfig()) is None

    def test_ensure_trace_starts_despite_switches_off(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            trace_module, "default_trace_dir", lambda: tmp_path
        )
        writer = trace_module.ensure_trace()
        assert writer is not None
        assert writer.capture_screenshots is False

    def test_ensure_trace_only_turns_capture_on(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            trace_module, "default_trace_dir", lambda: tmp_path
        )
        writer = trace_module.ensure_trace()
        assert trace_module.ensure_trace(capture_screenshots=True) is writer
        assert writer.capture_screenshots is True
        assert trace_module.ensure_trace() is writer
        assert writer.capture_screenshots is True  # never turned back off


class TestReplayWiring:
    def _arm(self, tmp_path, *, capture=True):
        writer = trace_module.TraceWriter(
            tmp_path, server_version="t", capture_screenshots=capture
        )
        trace_module._writer = writer
        trace_module.set_screenshot_provider(lambda: b"PNGDATA")
        return writer

    def test_capture_frame_gating(self, tmp_path):
        assert trace_module.capture_frame() is None  # no writer
        self._arm(tmp_path, capture=False)
        assert trace_module.capture_frame() is None  # capture off
        trace_module._writer.capture_screenshots = True
        assert trace_module.capture_frame() == b"PNGDATA"

    def test_provider_failure_degrades_to_no_frame(self, tmp_path):
        self._arm(tmp_path)

        def boom():
            raise RuntimeError("no frame")

        trace_module.set_screenshot_provider(boom)
        assert trace_module.capture_frame() is None

    def test_action_call_leaves_pre_and_post_frames(self, tmp_path):
        writer = self._arm(tmp_path)

        @with_analytics(None, "Click-Tool")
        async def fake_click():
            return "done"

        assert asyncio.run(fake_click()) == "done"
        writer.close()
        records, warnings = trace_module.read_trace(tmp_path)
        assert warnings == 0
        pre = next(r for r in records if r.get("event") == "tool_call_pre")
        post = next(r for r in records if r.get("event") == "tool_call")
        assert pre["screenshot"].endswith(".png")
        assert post["screenshot"].endswith(".png")
        assert len(list(tmp_path.glob("*.png"))) == 2

    def test_capture_tools_are_excluded(self, tmp_path):
        writer = self._arm(tmp_path)

        @with_analytics(None, "State-Tool")
        async def fake_snapshot():
            return "seen"

        asyncio.run(fake_snapshot())
        writer.close()
        records, _ = trace_module.read_trace(tmp_path)
        assert [r["event"] for r in records if "event" in r] == ["tool_call"]
        assert "screenshot" not in records[-1]
        assert not list(tmp_path.glob("*.png"))

    def test_no_frames_when_capture_off(self, tmp_path):
        writer = self._arm(tmp_path, capture=False)

        @with_analytics(None, "Click-Tool")
        async def fake_click():
            return "done"

        asyncio.run(fake_click())
        writer.close()
        records, _ = trace_module.read_trace(tmp_path)
        event = next(r for r in records if r.get("event") == "tool_call")
        assert set(event) == {"ts", "event", "tool", "ok", "duration_ms"}
        assert not list(tmp_path.glob("*.png"))


@win32_only
class TestSessionTraceRequest:
    def _tool(self, monkeypatch, tmp_path):
        from agentic_windows_mcp.desktop.service import Desktop
        from agentic_windows_mcp.infrastructure.sessions import SessionStore
        from agentic_windows_mcp.tools import session as session_tools
        from tests.test_multi_tools import FakeMCP

        store = SessionStore(root=tmp_path)
        monkeypatch.setattr(session_tools, "SessionStore", lambda: store)
        ensure_calls = {}

        def fake_ensure(*, capture_screenshots=False):
            ensure_calls["capture_screenshots"] = capture_screenshots
            return SimpleNamespace(path=tmp_path / "trace-fake.jsonl")

        monkeypatch.setattr(session_tools, "ensure_trace", fake_ensure)
        mcp = FakeMCP()
        session_tools.register(
            mcp, get_desktop=lambda: Desktop.__new__(Desktop),
            get_analytics=lambda: None,
        )
        return mcp.tools["Session"], store, ensure_calls

    def test_start_with_artifacts_enables_capture(self, monkeypatch, tmp_path):
        tool, store, ensure_calls = self._tool(monkeypatch, tmp_path)
        result = asyncio.run(
            tool(mode="start", session_id="alpha", trace=True, artifacts=True)
        )
        assert ensure_calls == {"capture_screenshots": True}
        assert "replay screenshots ON" in result
        manifest = store.read("alpha")
        assert manifest["options"]["trace"] is True
        assert manifest["options"]["artifacts"] is True

    def test_start_without_request_never_touches_trace(
        self, monkeypatch, tmp_path
    ):
        tool, store, ensure_calls = self._tool(monkeypatch, tmp_path)
        result = asyncio.run(tool(mode="start", session_id="alpha"))
        assert ensure_calls == {}
        assert "Trace is recording" not in result
