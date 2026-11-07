"""Guide tool (#21): the agent playbook, bundled with the package.

The guidance ships as Markdown inside the package and is served by the
server itself, so it always matches the version in use. The version is
injected into the header at serve time — the Markdown files carry no
version string, so a version bump can never leave a stale copy behind.
"""

from importlib import metadata, resources

from fastmcp import Context
from mcp.types import ToolAnnotations

from agentic_windows_mcp.infrastructure import with_analytics

TOPICS = ("core", "windows", "operations", "browser")


def server_version() -> str:
    try:
        return metadata.version("agentic-windows-mcp")
    except metadata.PackageNotFoundError:
        return "unknown"


def guide_text(topic: str, version: str | None = None) -> str:
    """Return one topic's Markdown with the running version stamped in."""
    if version is None:
        version = server_version()
    body = (
        resources.files("agentic_windows_mcp")
        .joinpath("guides", f"{topic}.md")
        .read_text(encoding="utf-8")
    )
    return f"<!-- agentic-windows-mcp {version} · guide topic: {topic} -->\n\n{body}"


def register(mcp, *, get_desktop, get_analytics) -> None:
    @mcp.tool(
        name="Guide",
        description=(
            "Fetch the server's own agent playbook — guidance that ships with "
            "this exact server version, so it cannot drift from the shipped "
            "behavior. Topics: 'core' (the observe → act → verify loop, ref "
            "rules, retry-only-when-safe discipline, stale-ref recovery), "
            "'windows' (PowerShell/encoding pitfalls, integrity levels, "
            "Chromium tree settle times, screenshot scaling), 'operations' "
            "(control-gate semantics, the takeover chord, reading the "
            "ControlStatus health block, wedge recovery), 'browser' (the "
            "Browser_* attach precondition, consent-dialog handling, the "
            "ask-before-relaunching rule, typed browser errors). Fetch "
            "'core' before your first action in a session."
        ),
        annotations=ToolAnnotations(
            title="Guide",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Guide-Tool", run_sync_in_thread=False)
    def guide_tool(topic: str = "core", ctx: Context = None) -> str:
        topic = topic.strip().lower()
        if topic not in TOPICS:
            return (
                f"Unknown guide topic {topic!r}. "
                f"Available topics: {', '.join(TOPICS)}."
            )
        return guide_text(topic)
