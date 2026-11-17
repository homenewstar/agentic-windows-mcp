"""Issue #42 — the call ledger: timed-out calls stay lookup-able, and an
idempotency key makes a retry return the original record instead of
executing twice.

Before #42, a gated call that hit the server deadline returned
TOOL_TIMEOUT and vanished: the worker kept running, its result was
discarded, and no record existed anywhere. These tests pin the new
contract at the ledger and gate layers with fakes (the live duplicate
proof on a real PC is a separate, schedulable step).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from agentic_windows_mcp.infrastructure.call_ledger import (
    CallLedger,
    get_call_ledger,
)
from agentic_windows_mcp.tools.control_notifications import ControlToolGate


# ---------------------------------------------------------------------------
# Ledger units
# ---------------------------------------------------------------------------


class TestCallLedger:
    def test_lifecycle_and_phase_split(self) -> None:
        ledger = CallLedger()
        record = ledger.start("PowerShell")
        assert record.state == "queued"
        time.sleep(0.05)
        ledger.mark_running(record)
        assert record.state == "running"
        assert record.gate_wait_seconds is not None and record.gate_wait_seconds >= 0.04
        time.sleep(0.05)
        ledger.finish(record, ok=True)
        assert record.state == "completed"
        assert record.handler_seconds is not None and record.handler_seconds >= 0.04
        assert record.gate_wait_seconds < record.handler_seconds + 0.05

    def test_timeout_then_finish_keeps_timed_out_flag(self) -> None:
        ledger = CallLedger()
        record = ledger.start("PowerShell")
        ledger.mark_running(record)
        ledger.mark_timed_out(record)
        assert record.state == "timed_out_worker_running"
        assert record.timed_out is True
        ledger.finish(record, ok=False, error=ValueError("boom"))
        assert record.state == "error"
        assert record.timed_out is True
        assert "ValueError" in (record.outcome_error or "")

    def test_abandon_never_touches_timed_out_record(self) -> None:
        ledger = CallLedger()
        record = ledger.start("PowerShell")
        ledger.mark_running(record)
        ledger.mark_timed_out(record)
        ledger.abandon(record, RuntimeError("caller went away"))
        assert record.state == "timed_out_worker_running"

    def test_start_or_find_is_atomic_per_key(self) -> None:
        ledger = CallLedger()
        first, created = ledger.start_or_find("PowerShell", "k-1")
        assert created
        second, created_again = ledger.start_or_find("PowerShell", "k-1")
        assert not created_again and second is first
        other_tool, created_other = ledger.start_or_find("Click", "k-1")
        assert created_other and other_tool is not first

    def test_eviction_drops_oldest_terminal_never_running(self) -> None:
        ledger = CallLedger(max_records=3)
        running = ledger.start("Click")
        ledger.mark_running(running)
        done1 = ledger.start("Click")
        ledger.finish(done1, ok=True)
        done2 = ledger.start("Click")
        ledger.finish(done2, ok=True)
        ledger.start("Click")  # 4th record pushes past capacity
        assert ledger.get(running.id) is running
        assert ledger.get(done1.id) is None  # oldest terminal evicted
        assert ledger.get(done2.id) is done2


# ---------------------------------------------------------------------------
# Gate behaviour
# ---------------------------------------------------------------------------


class _FakeController:
    enabled = True
    policy = "strict"

    def __init__(self, state: str = "ready", begin_delay: float = 0.0) -> None:
        self._state = state
        self._begin_delay = begin_delay
        self.ended: list[int] = []

    def status(self) -> dict:
        return {"state": self._state, "generation": 1}

    def begin_call(self, name: str) -> int:
        if self._begin_delay:
            time.sleep(self._begin_delay)
        return 42

    def checkpoint(self, token: int) -> None:
        return None

    def end_call(self, token: int) -> None:
        self.ended.append(token)


class _DisabledController:
    enabled = False
    policy = "strict"


class _FakeNotifier:
    def remember(self, context) -> None:
        return None


def _context(name: str = "PowerShell", arguments: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        message=SimpleNamespace(name=name, arguments=arguments or {})
    )


def test_timed_out_call_outcome_becomes_lookupable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "0.3")
    gate = ControlToolGate(_FakeController(), _FakeNotifier())
    ledger = get_call_ledger()

    async def slow(context):
        await asyncio.sleep(1.0)
        return "late-result"

    async def fast(context):
        return "ok"

    async def run() -> str:
        with pytest.raises(ToolError) as excinfo:
            await gate.on_call_tool(_context(), slow)
        payload = json.loads(str(excinfo.value))
        assert payload["code"] == "TOOL_TIMEOUT"
        call_id = payload["call_id"]
        assert call_id, "TOOL_TIMEOUT must carry the call id"
        record = ledger.get(call_id)
        assert record is not None
        assert record.state == "timed_out_worker_running"
        assert record.timed_out is True
        # The gate is free while the worker is still running.
        assert await gate.on_call_tool(_context("Click"), fast) == "ok"
        await asyncio.sleep(1.0)
        assert record.state == "completed"
        assert record.handler_seconds is not None and record.handler_seconds >= 0.9
        return call_id

    asyncio.run(run())


def test_handler_error_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "5")
    gate = ControlToolGate(_DisabledController(), _FakeNotifier())
    ledger = get_call_ledger()
    key = "err-recording-42"

    async def failing(context):
        raise ValueError("handler blew up")

    async def run() -> None:
        with pytest.raises(ValueError):
            await gate.on_call_tool(
                _context(arguments={"idempotency_key": key}), failing
            )

    asyncio.run(run())
    record = ledger.find_by_idempotency_key(key, "PowerShell")
    assert record is not None
    assert record.state == "error"
    assert "ValueError" in (record.outcome_error or "")


def test_blocked_call_settles_its_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "5")
    gate = ControlToolGate(_FakeController(state="user"), _FakeNotifier())
    ledger = get_call_ledger()
    key = "blocked-settles-42"

    async def never(context):  # pragma: no cover - must not run
        raise AssertionError("blocked call reached the handler")

    async def run() -> None:
        with pytest.raises(ToolError):
            await gate.on_call_tool(
                _context(arguments={"idempotency_key": key}), never
            )

    asyncio.run(run())
    record = ledger.find_by_idempotency_key(key, "PowerShell")
    assert record is not None
    assert record.terminal, "a rejected call must not linger as queued"


def test_phase_split_separates_gate_wait_from_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "5")
    gate = ControlToolGate(_FakeController(begin_delay=0.4), _FakeNotifier())
    ledger = get_call_ledger()
    key = "phase-split-42"

    async def fast(context):
        return "ok"

    async def run() -> None:
        result = await gate.on_call_tool(
            _context(arguments={"idempotency_key": key}), fast
        )
        assert result == "ok"

    asyncio.run(run())
    record = ledger.find_by_idempotency_key(key, "PowerShell")
    assert record is not None and record.state == "completed"
    assert record.gate_wait_seconds is not None and record.gate_wait_seconds >= 0.3
    assert record.handler_seconds is not None
    assert record.handler_seconds < record.gate_wait_seconds


def test_idempotent_duplicate_does_not_execute_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "5")
    gate = ControlToolGate(_FakeController(), _FakeNotifier())
    executions: list[int] = []

    async def counted(context):
        executions.append(1)
        return "first-result"

    async def run() -> None:
        first = await gate.on_call_tool(
            _context(arguments={"idempotency_key": "dup-42"}), counted
        )
        assert first == "first-result"
        second = await gate.on_call_tool(
            _context(arguments={"idempotency_key": "dup-42"}), counted
        )
        payload = json.loads(second.content[0].text)
        assert payload["code"] == "IDEMPOTENT_REPLAY"
        assert payload["state"] == "completed"

    asyncio.run(run())
    assert executions == [1], "the duplicate submission executed the handler"


def test_idempotent_duplicate_while_original_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "0.3")
    gate = ControlToolGate(_FakeController(), _FakeNotifier())
    executions: list[int] = []

    async def slow(context):
        executions.append(1)
        await asyncio.sleep(2.0)
        return "late"

    async def run() -> None:
        task = asyncio.create_task(
            gate.on_call_tool(_context(arguments={"idempotency_key": "run-42"}), slow)
        )
        await asyncio.sleep(0.5)  # past the deadline; worker still running
        second = await gate.on_call_tool(
            _context(arguments={"idempotency_key": "run-42"}), slow
        )
        payload = json.loads(second.content[0].text)
        assert payload["code"] == "IDEMPOTENT_IN_PROGRESS"
        assert payload["state"] == "timed_out_worker_running"
        with pytest.raises(ToolError):
            await task

    asyncio.run(run())
    assert executions == [1]


def test_powershell_replay_uses_linked_job_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate-level chain behind A(2)+A(3): the sync PowerShell body
    registers a jobs-registry record linked by call id; a duplicate
    submission replays that record's output instead of re-executing."""
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "5")
    gate = ControlToolGate(_DisabledController(), _FakeNotifier())
    executions: list[int] = []

    async def ps_body(context):
        def run() -> str:
            from agentic_windows_mcp.desktop.control_context import current_call_id
            from agentic_windows_mcp.infrastructure.call_ledger import link_call_job
            from agentic_windows_mcp.powershell.jobs import JOBS

            executions.append(1)
            call_id = current_call_id.get()
            record = JOBS.register_sync("Write-Output hello", call_id=call_id)
            link_call_job(call_id, record.id)
            JOBS.complete_sync(record.id, "hello\n", "", 0)
            return "Response: hello\n\nStatus Code: 0"

        return await asyncio.to_thread(run)

    async def run() -> None:
        first = await gate.on_call_tool(
            _context(arguments={"idempotency_key": "ps-replay-42"}), ps_body
        )
        assert "hello" in first
        second = await gate.on_call_tool(
            _context(arguments={"idempotency_key": "ps-replay-42"}), ps_body
        )
        text = second.content[0].text
        assert "not executed again" in text
        assert "hello" in text
        assert "Status Code: 0" in text

    asyncio.run(run())
    assert executions == [1]


@pytest.mark.asyncio
async def test_idempotent_replay_through_real_mcp_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The synthetic replay result must survive FastMCP's real
    middleware pipeline, not just the direct gate call."""
    monkeypatch.setenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "5")
    from fastmcp import Client, FastMCP

    from agentic_windows_mcp.tools.control_notifications import ControlNotifier

    controller = _FakeController()
    mcp = FastMCP("ledger-test")
    mcp.add_middleware(ControlToolGate(controller, ControlNotifier(controller)))
    executions: list[str] = []

    @mcp.tool(name="PowerShell")
    def powershell(command: str, idempotency_key: str | None = None):
        executions.append(command)
        return "ran: " + command

    async with Client(mcp) as client:
        first = await client.call_tool(
            "PowerShell", {"command": "x", "idempotency_key": "pipe-42"}
        )
        assert "ran: x" in str(first.content)
        second = await client.call_tool(
            "PowerShell", {"command": "x", "idempotency_key": "pipe-42"}
        )
        assert "IDEMPOTENT_REPLAY" in str(second.content)
    assert executions == ["x"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
