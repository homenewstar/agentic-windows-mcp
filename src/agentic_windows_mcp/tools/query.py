"""Snapshot query tools — Find, Get, Is (issue #17).

Answering "is the Save button enabled?", "what is this field's value?"
or "is there a dialog titled X?" should not need a whole new Snapshot.
These read-only tools query the registry of the most recent Snapshot:
Find searches it by name/role/AutomationId/window, Get reads one live
property of a ref, and Is evaluates one predicate on a ref. Get and Is
re-resolve the ref through the strict resolver (#12), so a stale ref
fails with STALE_REF instead of returning another element's data, and
any live read that cannot be made is reported "unknown" — never
guessed. All three sit in the same control-policy category as
Snapshot (OTHER).
"""

from mcp.types import ToolAnnotations
from agentic_windows_mcp.desktop.service import AmbiguousRefError, StaleRefError
from agentic_windows_mcp.infrastructure import with_analytics
from fastmcp import Context

from agentic_windows_mcp.tools._snapshot_helpers import _as_bool
from agentic_windows_mcp.tools.elements import _stale, parse_ref

_READ_ONLY = dict(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(str(v) for v in value) + "]"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _criteria_text(text, exact, role, automation_id, window) -> str:
    parts = []
    if text is not None:
        parts.append(f"text{'=' if exact else '~'}{text!r}")
    if role:
        parts.append(f"role={role!r}")
    if automation_id is not None:
        parts.append(f"automation_id={automation_id!r}")
    if window:
        parts.append(f"window~{window!r}")
    return ", ".join(parts)


def _format_find(result, criteria: str) -> str:
    if not result["has_snapshot"]:
        return "No snapshot has been taken yet — call Snapshot first, then Find."
    total = result["total_matches"] + result["total_static"]
    if total == 0:
        return f"No matches in snapshot {result['generation']} for {criteria}."
    lines = [
        f"{total} match(es) in snapshot {result['generation']} "
        f"({result['total_matches']} actionable, {result['total_static']} static):"
    ]
    for match in result["matches"]:
        line = f"@e{match['label']} [{match['control_type']}] \"{match['name']}\""
        if match["automation_id"]:
            line += f" automation_id=\"{match['automation_id']}\""
        if match["window_name"]:
            line += f" — window \"{match['window_name']}\""
        if match["path"]:
            line += f" — path: {match['path']}"
        if match["center"]:
            line += f" — center ({match['center'][0]}, {match['center'][1]})"
        lines.append(line)
    for static_text in result["static_matches"]:
        lines.append(f"(static text, no ref) \"{static_text}\"")
    if result["truncated"]:
        shown = len(result["matches"]) + len(result["static_matches"])
        lines.append(
            f"… truncated: showing {shown} of {total} matches. Narrow the query."
        )
    return "\n".join(lines)


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Find",
        description="Search the most recent Snapshot's elements without taking a new capture. Filter by name text (substring, case-insensitive; exact=True for a full-string case-sensitive match), role (UIA control type such as 'Button' or 'Edit'), automation_id, or a window-title substring; at least one filter is required. Returns matching elements with their snapshot refs (@eN — the same global numbering the paged Snapshot output uses, ready for Invoke/SetValue/Toggle/Click), their window, context path and center, plus static page-text matches which have no ref. Read-only. Call Snapshot first; pass snapshot=<Snapshot ID> to fail with STALE_REF if a newer snapshot has superseded the one you mean.",
        annotations=ToolAnnotations(title="Find", **_READ_ONLY),
    )
    @with_analytics(get_analytics(), "Find-Tool", run_sync_in_thread=False)
    def find_tool(
        text: str | None = None,
        exact: bool | str = False,
        role: str | None = None,
        automation_id: str | None = None,
        window: str | None = None,
        snapshot: int | None = None,
        ctx: Context = None,
    ) -> str:
        if text is None and not role and automation_id is None and not window:
            raise ValueError(
                "Find needs at least one filter: text, role, automation_id or window."
            )
        try:
            result = get_desktop().find_elements(
                text=text,
                exact=_as_bool(exact),
                role=role,
                automation_id=automation_id,
                window=window,
                snapshot=snapshot,
            )
        except (StaleRefError, AmbiguousRefError) as e:
            raise _stale(e) from e
        return _format_find(
            result, _criteria_text(text, _as_bool(exact), role, automation_id, window)
        )

    @mcp.tool(
        name="Get",
        description="Read one live property of a Snapshot ref element without a new capture: name, value, bounds, enabled, offscreen, focused, toggle_state, selected or automation_id. The ref is re-resolved strictly, so a stale or ambiguous ref returns STALE_REF / AMBIGUOUS_TARGET instead of another element's value. A property the element does not expose, or whose live read fails, is reported as 'unknown' — never guessed. A password field's value is always withheld. Read-only.",
        annotations=ToolAnnotations(title="Get", **_READ_ONLY),
    )
    @with_analytics(get_analytics(), "Get-Tool", run_sync_in_thread=False)
    def get_tool(
        ref: str | int,
        property: str,
        snapshot: int | None = None,
        ctx: Context = None,
    ) -> str:
        label = parse_ref(ref)
        key = str(property or "").strip().lower()
        try:
            _element, known, value = get_desktop().get_ref_property(
                label, key, snapshot
            )
        except (StaleRefError, AmbiguousRefError) as e:
            raise _stale(e) from e
        if not known:
            return (
                f"Get @e{label} {key}: unknown "
                "(the live element does not expose it, or the read failed)."
            )
        return f"Get @e{label} {key}: {_format_value(value)}"

    @mcp.tool(
        name="Is",
        description="Evaluate one predicate on a Snapshot ref element, answered from the live element: enabled, visible, focused, checked, expanded, selected, editable or password. Returns 'true', 'false', or 'unknown' when the live read cannot decide (for example 'checked' on an element with no TogglePattern, or an indeterminate toggle) — unknown is never silently reported as false. The ref is re-resolved strictly: a stale or ambiguous ref returns STALE_REF / AMBIGUOUS_TARGET. Read-only.",
        annotations=ToolAnnotations(title="Is", **_READ_ONLY),
    )
    @with_analytics(get_analytics(), "Is-Tool", run_sync_in_thread=False)
    def is_tool(
        ref: str | int,
        predicate: str,
        snapshot: int | None = None,
        ctx: Context = None,
    ) -> str:
        label = parse_ref(ref)
        key = str(predicate or "").strip().lower()
        try:
            _element, verdict = get_desktop().evaluate_ref_predicate(
                label, key, snapshot
            )
        except (StaleRefError, AmbiguousRefError) as e:
            raise _stale(e) from e
        text = {True: "true", False: "false", None: "unknown"}[verdict]
        return f"Is @e{label} {key}: {text}"
