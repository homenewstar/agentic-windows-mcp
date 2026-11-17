"""PowerShell tool — shell/command execution, plus background job tools."""

from mcp.types import ToolAnnotations
from agentic_windows_mcp.infrastructure import with_analytics
from agentic_windows_mcp.infrastructure.errors import with_disposition
from agentic_windows_mcp.powershell import PowerShellExecutor
from agentic_windows_mcp.powershell.jobs import JOBS
from fastmcp import Context


def _format_job_status(status: dict) -> str:
    pid_line = (
        f"PID: {status['pid']}"
        if status["pid"] is not None
        else "PID: n/a (synchronous execution — the executor owns the process)"
    )
    lines = [
        f"Job: {status['job_id']}",
        f"State: {status['state']}",
        pid_line,
        f"Elapsed: {status['elapsed_seconds']}s",
        f"Started: {status['started_at']}",
        f"Command: {status['command']}",
        f"Output buffered: stdout {status['stdout_chars']} chars, stderr {status['stderr_chars']} chars",
    ]
    if status.get("kind") == "sync":
        lines.insert(
            1,
            "Kind: sync (a synchronous PowerShell execution recorded for "
            f"outcome lookup; owning call: {status.get('call_id')})",
        )
    if status["exit_code"] is not None:
        lines.insert(2, f"Exit Code: {status['exit_code']}")
    if status["stopped"]:
        lines.append("Stopped: yes (tree killed via JobStop)")
    return "\n".join(lines)


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="PowerShell",
        description="Shell/command execution. Keywords: shell, run, execute, cmd, terminal, command line, script. A comprehensive system tool for executing any PowerShell commands. Use it to navigate the file system, manage files and processes, and execute system-level operations. Capable of accessing web content (e.g., via Invoke-WebRequest), interacting with network resources, and performing complex administrative tasks. This tool provides full access to the underlying operating system capabilities, making it the primary interface for system automation, scripting, and deep system interaction. Under relaxed control policy this tool is not desktop-gated. Prefer background execution; coordinate with the user before commands known to interfere with their desktop, reusing existing authorization. Set background=True to start the command as a background job instead: the call returns a job id immediately (use JobStatus/JobOutput/JobStop to follow it) — the right choice for long-running commands and for starting servers, which otherwise hold the output pipes open and look like a timeout even when they succeeded. Synchronous executions are recorded in the same jobs registry (kind=sync) and linked from the call ledger: if a call answers TOOL_TIMEOUT, look its call_id up via ControlStatus and follow the record's job_id with JobStatus/JobOutput to learn what the command actually did before retrying. Pass idempotency_key (any string you choose) on a state-changing command to make retries safe: resubmitting the same key returns the original call's recorded outcome instead of executing twice.",
        annotations=ToolAnnotations(
            title="PowerShell",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_analytics(get_analytics(), "Powershell-Tool")
    @with_disposition
    def powershell_tool(
        command: str,
        timeout: int = 30,
        background: bool | str = False,
        idempotency_key: str | None = None,
        ctx: Context = None,
    ) -> str:
        # idempotency_key is consumed by the control gate (it dedups the
        # submission against the #42 call ledger before this function
        # runs); the executor itself does not need the value.
        if background is True or (isinstance(background, str) and background.lower() == "true"):
            job = PowerShellExecutor.start_background(command)
            return (
                f"Job started: {job.id}\nPID: {job.pid}\nCommand: {command}\n"
                "Use JobStatus to check completion, JobOutput to read its output, "
                "JobStop to terminate it."
            )
        # #42: register the synchronous execution in the jobs registry
        # before running it, linked to the gate's call-ledger record, so
        # its outcome stays lookup-able even when the gate deadline
        # answers the caller first and this thread keeps running.
        from agentic_windows_mcp.desktop.control_context import current_call_id
        from agentic_windows_mcp.infrastructure.call_ledger import link_call_job

        call_id = current_call_id.get()
        record = JOBS.register_sync(command, call_id=call_id)
        link_call_job(call_id, record.id)
        try:
            stdout, stderr, status_code = PowerShellExecutor.execute_command_detailed(
                command, timeout
            )
        except Exception:
            JOBS.complete_sync(record.id, "", "", 1)
            raise
        JOBS.complete_sync(record.id, stdout, stderr, status_code)
        result = f"Response: {stdout}\nStatus Code: {status_code}"
        if stderr:
            result += f"\nStderr: {stderr}"
        return result

    @mcp.tool(
        name="JobStatus",
        description="Report a background PowerShell job's state (running/exited), exit code, elapsed time and how much output is buffered. Job ids come from calling the PowerShell tool with background=True.",
        annotations=ToolAnnotations(
            title="Job Status",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "JobStatus-Tool")
    def job_status_tool(job_id: str, ctx: Context = None) -> str:
        try:
            job = JOBS.get(job_id)
        except KeyError:
            known = ", ".join(JOBS.list_ids()) or "none"
            return f"Error: unknown job id {job_id!r}. Known jobs: {known}."
        return _format_job_status(job.status())

    @mcp.tool(
        name="JobOutput",
        description="Read a background PowerShell job's buffered output incrementally. Pass offset (a character offset from a previous call's Next Offset) to read only what arrived since. stream selects stdout, stderr, or both. Output is also stripped of CLIXML noise.",
        annotations=ToolAnnotations(
            title="Job Output",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "JobOutput-Tool")
    def job_output_tool(
        job_id: str,
        offset: int = 0,
        stream: str = "stdout",
        ctx: Context = None,
    ) -> str:
        from agentic_windows_mcp.powershell.utils import strip_clixml

        try:
            result = JOBS.output(job_id, stream, offset)
        except KeyError:
            known = ", ".join(JOBS.list_ids()) or "none"
            return f"Error: unknown job id {job_id!r}. Known jobs: {known}."
        parts = [f"Job: {result['job_id']} ({result['state']})"]
        for name in ("stdout", "stderr"):
            if name in result:
                if result[f"{name}_dropped"]:
                    parts.append(
                        f"Note: {result[f'{name}_dropped']} early {name} chars were dropped "
                        "(buffer cap reached)."
                    )
                parts.append(f"{name} (next offset {result[f'{name}_next_offset']}):")
                parts.append(strip_clixml(result[name]))
        return "\n".join(parts)

    @mcp.tool(
        name="JobStop",
        description="Terminate a background PowerShell job and its whole process tree (taskkill /T /F). No-op if the job already exited.",
        annotations=ToolAnnotations(
            title="Job Stop",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "JobStop-Tool")
    def job_stop_tool(job_id: str, ctx: Context = None) -> str:
        try:
            status = JOBS.stop(job_id)
        except KeyError:
            known = ", ".join(JOBS.list_ids()) or "none"
            return f"Error: unknown job id {job_id!r}. Known jobs: {known}."
        return _format_job_status(status)
