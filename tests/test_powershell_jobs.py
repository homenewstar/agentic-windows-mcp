"""Issue #2 — background PowerShell jobs: job handles, incremental output,
process-state independent of the output pipes."""

import sys
import time

import pytest

from agentic_windows_mcp.powershell.jobs import JOBS, StreamBuffer


class TestStreamBuffer:
    def test_offsets_page_incrementally(self):
        buf = StreamBuffer()
        buf.append("hello ")
        text, nxt, dropped = buf.read(0)
        assert (text, nxt, dropped) == ("hello ", 6, 0)
        buf.append("world")
        text, nxt, dropped = buf.read(nxt)
        assert (text, nxt, dropped) == ("world", 11, 0)
        text, nxt, _ = buf.read(nxt)
        assert text == "" and nxt == 11

    def test_cap_drops_oldest_and_reports_it(self):
        buf = StreamBuffer(cap=10)
        buf.append("0123456789ABCDEF")
        text, nxt, dropped = buf.read(0)
        assert dropped == 6
        assert text == "6789ABCDEF"
        assert nxt == 16

    def test_offset_inside_dropped_region_reads_from_oldest_retained(self):
        buf = StreamBuffer(cap=5)
        buf.append("0123456789")
        text, _, dropped = buf.read(2)
        assert dropped == 5
        assert text == "56789"


@pytest.mark.skipif(sys.platform != "win32", reason="jobs launch Windows PowerShell")
class TestBackgroundJobs:
    def _wait_exited(self, job_id: str, timeout: float = 20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = JOBS.get(job_id)
            if not job.running:
                return job
            time.sleep(0.2)
        raise AssertionError(f"job {job_id} did not exit within {timeout}s")

    def test_job_returns_immediately_and_output_is_retrievable(self):
        from agentic_windows_mcp.powershell import PowerShellExecutor

        started = time.monotonic()
        job = PowerShellExecutor.start_background(
            "Write-Output 'job-hello'; Start-Sleep -Seconds 2"
        )
        assert time.monotonic() - started < 5, "background start must not block"
        self._wait_exited(job.id)
        status = job.status()
        assert status["state"] == "exited"
        assert status["exit_code"] == 0
        out = JOBS.output(job.id, "stdout", 0)
        assert "job-hello" in out["stdout"]

    def test_job_output_is_incremental(self):
        from agentic_windows_mcp.powershell import PowerShellExecutor

        job = PowerShellExecutor.start_background(
            "Write-Output 'line-one'; Start-Sleep -Seconds 3; Write-Output 'line-two'"
        )
        first = ""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and "line-one" not in first:
            first = JOBS.output(job.id, "stdout", 0)["stdout"]
            time.sleep(0.2)
        assert "line-one" in first
        assert "line-two" not in first
        self._wait_exited(job.id)
        # Give the pump thread a moment to flush the tail.
        tail = ""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "line-two" not in tail:
            tail = JOBS.output(job.id, "stdout", first and len(first) or 0)["stdout"]
            time.sleep(0.2)
        assert "line-two" in tail
        assert "line-one" not in tail

    def test_detached_child_does_not_block_job_state(self):
        """The issue's canonical case: the shell exits while a detached child
        keeps the output pipe open. The job must report exited promptly, and
        the child's later output must still land in the buffer."""
        from agentic_windows_mcp.powershell import PowerShellExecutor

        job = PowerShellExecutor.start_background(
            "Start-Process -NoNewWindow powershell -ArgumentList "
            "'-NoProfile','-Command','Start-Sleep -Seconds 2; Write-Output late-child'; "
            "Write-Output 'parent-done'"
        )
        finished = self._wait_exited(job.id, timeout=10)
        assert finished.exit_code == 0
        # Child writes ~2s in; poll the buffer for it.
        seen = ""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and "late-child" not in seen:
            seen = JOBS.output(job.id, "stdout", 0)["stdout"]
            time.sleep(0.25)
        assert "parent-done" in seen
        assert "late-child" in seen

    def test_job_stop_terminates_tree(self):
        from agentic_windows_mcp.powershell import PowerShellExecutor

        job = PowerShellExecutor.start_background("Start-Sleep -Seconds 120")
        time.sleep(0.5)
        assert job.running
        status = JOBS.stop(job.id)
        assert status["state"] == "exited"
        assert status["stopped"] is True

    def test_unknown_job_id_raises(self):
        with pytest.raises(KeyError):
            JOBS.get("job-999999")
