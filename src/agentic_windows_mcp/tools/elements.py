"""Element-reference tools — Invoke, SetValue, Toggle (issue #3).

Snapshot output tags every actionable element with a ref (``@e12`` for
interactive elements, ``@s3`` for scrollable ones). These tools act on the
element *by ref* through UI Automation patterns instead of screen
coordinates: the element is re-acquired by its UIA RuntimeId inside its
window, so the action lands even when the window moved after the snapshot,
and any drift (element gone, renamed, snapshot superseded) returns a clear
STALE_REF error instead of a blind click in the wrong place.
"""

import re

from mcp.types import ToolAnnotations
from agentic_windows_mcp.desktop.service import StaleRefError
from agentic_windows_mcp.infrastructure import with_analytics
from fastmcp import Context

_REF_RE = re.compile(r"^@?[es]?(\d+)$", re.IGNORECASE)


def parse_ref(ref) -> int:
    """Parse a ref ('@e12', '@s12', '12', 12) into its numeric label."""
    if isinstance(ref, bool):
        raise ValueError(f"Invalid element ref: {ref!r}")
    if isinstance(ref, int):
        if ref < 0:
            raise ValueError(f"Invalid element ref: {ref!r}")
        return ref
    if isinstance(ref, str):
        match = _REF_RE.match(ref.strip())
        if match:
            return int(match.group(1))
    raise ValueError(
        f"Invalid element ref: {ref!r}. Use a ref printed by Snapshot, e.g. '@e12'."
    )


def _stale(e: StaleRefError) -> ValueError:
    # Surface staleness as a tool error whose text starts with STALE_REF so
    # agents can branch on it reliably.
    return ValueError(str(e))


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Invoke",
        description="Invoke a UI element by its Snapshot ref (e.g. '@e12') using the UIA InvokePattern — the pattern behind buttons, menu items and links. Unlike Click it uses no coordinates, so it still lands if the window moved after the snapshot. Pass snapshot=<Snapshot ID> to guard against acting on a superseded snapshot. Returns STALE_REF when the element is gone or changed (take a new Snapshot), and UNSUPPORTED_PATTERN when the element has no InvokePattern (fall back to Click).",
        annotations=ToolAnnotations(
            title="Invoke",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Invoke-Tool", run_sync_in_thread=False)
    def invoke_tool(ref: str | int, snapshot: int | None = None, ctx: Context = None) -> str:
        label = parse_ref(ref)
        try:
            element = get_desktop().invoke_element(label, snapshot)
        except StaleRefError as e:
            raise _stale(e) from e
        return f"Invoked @e{label}: {element.control_type} \"{element.name}\"."

    @mcp.tool(
        name="SetValue",
        description="Set a UI element's value by its Snapshot ref (e.g. '@e7') using the UIA ValuePattern (LegacyIAccessible as fallback) — writes the value directly with no keystroke simulation, so existing content is replaced atomically and focus quirks do not apply. Works on standard Win32/WPF/WinForms controls and web inputs exposed through accessibility. Pass snapshot=<Snapshot ID> to guard against a superseded snapshot. Returns STALE_REF when the element is gone or changed, UNSUPPORTED_PATTERN when it exposes no value pattern (fall back to the Type tool).",
        annotations=ToolAnnotations(
            title="Set Value",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "SetValue-Tool", run_sync_in_thread=False)
    def set_value_tool(
        ref: str | int, value: str, snapshot: int | None = None, ctx: Context = None
    ) -> str:
        label = parse_ref(ref)
        try:
            element = get_desktop().set_element_value(label, value, snapshot)
        except StaleRefError as e:
            raise _stale(e) from e
        return f"Set @e{label} ({element.control_type} \"{element.name}\") to {value!r}."

    @mcp.tool(
        name="Toggle",
        description="Toggle a UI element by its Snapshot ref (e.g. '@e9') using the UIA TogglePattern — checkboxes and toggle switches — without coordinates. Reports the resulting toggle state. Pass snapshot=<Snapshot ID> to guard against a superseded snapshot. Returns STALE_REF when the element is gone or changed, UNSUPPORTED_PATTERN when it has no TogglePattern (fall back to Click).",
        annotations=ToolAnnotations(
            title="Toggle",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Toggle-Tool", run_sync_in_thread=False)
    def toggle_tool(ref: str | int, snapshot: int | None = None, ctx: Context = None) -> str:
        label = parse_ref(ref)
        try:
            element, state = get_desktop().toggle_element(label, snapshot)
        except StaleRefError as e:
            raise _stale(e) from e
        state_text = {0: "Off", 1: "On", 2: "Indeterminate"}.get(state, str(state))
        return f"Toggled @e{label}: {element.control_type} \"{element.name}\" — now {state_text}."
