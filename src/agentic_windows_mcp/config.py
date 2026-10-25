import os


def is_debug() -> bool:
    """Return True if debug mode is enabled via the AGENTIC_WINDOWS_MCP_DEBUG environment variable."""
    return os.getenv("AGENTIC_WINDOWS_MCP_DEBUG", "false").lower() in ("1", "true", "yes", "on")


def enable_debug() -> None:
    """Enable debug mode by setting the AGENTIC_WINDOWS_MCP_DEBUG environment variable."""
    os.environ["AGENTIC_WINDOWS_MCP_DEBUG"] = "true"
