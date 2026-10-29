"""Read-only MCP control ownership status tool."""

from mcp.types import ToolAnnotations

from agentic_windows_mcp.desktop.control import get_controller


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="ControlStatus",
        description="Get desktop control owner, enabled state, gate policy, configured idle timeout and remaining user idle wait in seconds. The health block reports in-flight/last call timing, watchdog liveness, monitor heartbeat age and a wedged verdict for the control channel; when health_recovery is enabled in config (off by default), a wedged verdict triggers the scoped, rate-limited recovery and the attempt is listed under health.recovery_events. Settings are read-only and changes require a server restart.",
        annotations=ToolAnnotations(
            title="ControlStatus",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def control_status_tool() -> dict:
        return get_controller().status()
