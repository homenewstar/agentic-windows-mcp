"""Read-only MCP control ownership status tool."""

from mcp.types import ToolAnnotations

from agentic_windows_mcp.desktop.control import get_controller


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="ControlStatus",
        description="Get desktop control owner, enabled state, gate policy, configured idle timeout and remaining user idle wait in seconds. The health block reports in-flight/last call timing, watchdog liveness, monitor heartbeat age and a wedged verdict for the control channel; when health_recovery is enabled in config (off by default), a wedged verdict triggers the scoped, rate-limited recovery and the attempt is listed under health.recovery_events. The calls block is the #42 call ledger: recent gated calls with their state (queued / running / timed_out_worker_running / completed / error), gate-wait vs handler phase split, and linked job id — after a TOOL_TIMEOUT, pass that error's call_id as the call_id argument to learn whether the command is still running or how it ended, before deciding whether to retry. Settings are read-only and changes require a server restart.",
        annotations=ToolAnnotations(
            title="ControlStatus",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def control_status_tool(call_id: str | None = None) -> dict:
        result = get_controller().status()
        # Durable per-tool usage metrics (#36): aggregate counts,
        # error codes and latency percentiles since the stats window
        # opened — names/outcomes/durations only, never arguments or
        # result content. Imported lazily and fail-soft by design.
        from agentic_windows_mcp.infrastructure.usage_metrics import usage_summary

        result["usage"] = usage_summary()
        # Per-call execution ledger (#42): recent gated calls and their
        # outcomes, so a timed-out call is a lookup, not a guess. Also
        # lazy and fail-soft.
        from agentic_windows_mcp.infrastructure.call_ledger import (
            call_ledger_lookup,
            call_ledger_summary,
        )

        result["calls"] = call_ledger_summary()
        if call_id:
            result["call"] = call_ledger_lookup(call_id)
        return result
