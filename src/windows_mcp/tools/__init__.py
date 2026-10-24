"""tools subpackage - registers all MCP tool definitions on a FastMCP instance."""

from windows_mcp.tools import (
    app,
    browser_devtools,
    clipboard,
    control_status,
    display,
    filesystem,
    input,
    multi,
    notification,
    process,
    registry,
    scrape,
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
