"""Issue #42 — synchronous PowerShell executions are tracked in the
jobs registry, so their outcome survives a gate TOOL_TIMEOUT.

The registry half of these tests is platform-independent (registering
and completing a record spawns no process). The final test runs the
real PowerShell tool function end-to-end and is Windows-only.
"""

from __future__ import annotations

import sys

import pytest

from agentic_windows_mcp.powershell.jobs import JobManager


class TestSyncJobRecords:
    def test_register_then_complete_lifecycle(self) -> None:
        manager = JobManager()
        record = manager.register_sync("Write-Output hi", call_id="call-7")
        status = record.status()
        assert status["kind"] == "sync"
        assert status["state"] == "running"
        assert status["pid"] is None
        assert status["call_id"] == "call-7"
        assert status["exit_code"] is None

        manager.complete_sync(record.id, "hi\n", "", 0)
        status = manager.get(record.id).status()
        assert status["state"] == "exited"
        assert status["exit_code"] == 0
        assert status["stdout_chars"] == 3

        out = manager.output(record.id, "both", 0)
        assert out["stdout"] == "hi\n"
        assert out["state"] == "exited"

    def test_sync_and_background_share_one_id_sequence(self) -> None:
        manager = JobManager()
        first = manager.register_sync("one")
        second = manager.register_sync("two")
        assert first.id != second.id
        assert int(second.id.split("-")[1]) == int(first.id.split("-")[1]) + 1

    def test_running_sync_job_cannot_be_stopped_but_exited_can(self) -> None:
        manager = JobManager()
        record = manager.register_sync("long")
        with pytest.raises(ValueError, match="synchronous execution record"):
            manager.stop(record.id)
        manager.complete_sync(record.id, "", "", 3)
        status = manager.stop(record.id)  # no-op once exited
        assert status["state"] == "exited"
        assert status["exit_code"] == 3

    def test_complete_sync_rejects_background_job_ids(self) -> None:
        manager = JobManager()
        record = manager.register_sync("x")
        with pytest.raises(KeyError):
            manager.complete_sync("job-999", "", "", 0)
        assert record.running  # untouched


@pytest.mark.skipif(sys.platform != "win32", reason="runs Windows PowerShell")
class TestPowerShellToolSyncRecord:
    def test_tool_registers_and_completes_a_linked_sync_record(self) -> None:
        from agentic_windows_mcp.desktop.control_context import current_call_id
        from agentic_windows_mcp.infrastructure.call_ledger import get_call_ledger
        from agentic_windows_mcp.powershell.jobs import JOBS
        from agentic_windows_mcp.tools import shell

        captured: dict = {}

        class _FakeMcp:
            def tool(self, name=None, **_kwargs):
                def deco(fn):
                    captured[name] = fn
                    return fn

                return deco

        shell.register(
            _FakeMcp(), get_desktop=lambda: None, get_analytics=lambda: None
        )
        powershell_tool = captured["PowerShell"]

        ledger = get_call_ledger()
        call = ledger.start("PowerShell")
        marker = current_call_id.set(call.id)
        try:
            # The analytics wrapper dispatches the sync body to a thread
            # and is itself async; the call-id contextvar must survive
            # both hops for the link to form.
            import asyncio

            result = asyncio.run(powershell_tool("Write-Output 42"))
        finally:
            current_call_id.reset(marker)
        assert "42" in result

        record = ledger.get(call.id)
        assert record is not None and record.job_id is not None
        job = JOBS.get(record.job_id)
        status = job.status()
        assert status["kind"] == "sync"
        assert status["call_id"] == call.id
        assert status["state"] == "exited"
        assert status["exit_code"] == 0
        out = JOBS.output(record.job_id, "stdout", 0)
        assert "42" in out["stdout"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
