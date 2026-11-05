"""tools subpackage - registers all MCP tool definitions on a FastMCP instance."""

from agentic_windows_mcp.tools import (
    app,
    batch,
    browser_devtools,
    clipboard,
    control_status,
    display,
    elements,
    filesystem,
    guide,
    input,
    multi,
    notification,
    process,
    query,
    registry,
    scrape,
    session,
    shell,
    snapshot,
)

_MODULES = [
    control_status,
    app,
    display,
    shell,
    filesystem,
    snapshot,
    elements,
    query,
    guide,
    session,
    batch,
    input,
    scrape,
    multi,
    clipboard,
    process,
    notification,
    registry,
    browser_devtools,
]


def register_all(mcp, *, get_desktop, get_analytics):
    """Register every tool module on mcp."""
    for mod in _MODULES:
        mod.register(mcp, get_desktop=get_desktop, get_analytics=get_analytics)
