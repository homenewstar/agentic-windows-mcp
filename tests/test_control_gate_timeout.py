"""The control gate must not wedge when a gated tool call hangs.

Regression: one unbounded call (a browser evaluate against a renderer
blocked by a native dialog, a subprocess whose output pipe is held open by
a long-lived child) held the gate lock forever; every later gated call
queued behind it and only ControlStatus (which bypasses the gate) answered,
until the server was restarted. The gate now enforces a per-call deadline
(AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS): the await is cancelled, the lease and
lock are released, and later calls proceed.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from agentic_windows_mcp.tools.control_notifications import ControlToolGate


class _FakeController:
    enabled = True
    policy = "strict"

    def __init__(self) -> None:
        self.ended: list[int] = []

    def status(self) -> dict:
        return {"state": "ready", "generation": 1}

    def begin_call(self, name: str) -> int:
        return 42

    def checkpoint(self, token: int) -> None:
        return None

    def end_call(self, token: int) -> None:
        self.ended.append(token)


class _FakeNotifier:
    def remember(self, context) -> None:
        return None


def _context() -> SimpleNamespace:
    return SimpleNamespace(message=SimpleNamespace(name="PowerShell", arguments={}))


async def _fast(context):
    return "ok"


async def _hang(context):
    await asyncio.sleep(3600)
    return "never"


def test_hung_call_times_out_and_releases_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "0.3")
    controller = _FakeController()
    gate = ControlToolGate(controller, _FakeNotifier())

    async def run() -> float:
        start = time.monotonic()
        with pytest.raises(ToolError) as excinfo:
            await gate.on_call_tool(_context(), _hang)
        elapsed = time.monotonic() - start
        payload = json.loads(str(excinfo.value))
        assert payload["code"] == "TOOL_TIMEOUT"
        # The timed-out call balanced its lease...
        assert controller.ended == [42]
        # ...and the gate lock is free: a later fast call succeeds at once.
        assert await gate.on_call_tool(_context(), _fast) == "ok"
        return elapsed

    elapsed = asyncio.run(run())
    assert elapsed < 5, "deadline must fire promptly, not wait for the hung call"


def test_fast_call_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "5")
    controller = _FakeController()
    gate = ControlToolGate(controller, _FakeNotifier())
    assert asyncio.run(gate.on_call_tool(_context(), _fast)) == "ok"
    assert controller.ended == [42]


def test_deadline_disabled_with_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "0")
    controller = _FakeController()
    gate = ControlToolGate(controller, _FakeNotifier())

    async def slow(context):
        await asyncio.sleep(0.4)
        return "slow-ok"

    assert asyncio.run(gate.on_call_tool(_context(), slow)) == "slow-ok"


def test_timeout_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_windows_mcp.tools.control_notifications import tool_timeout_seconds

    monkeypatch.delenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", raising=False)
    assert tool_timeout_seconds() == 180.0
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "garbage")
    assert tool_timeout_seconds() == 180.0
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "12.5")
    assert tool_timeout_seconds() == 12.5


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
