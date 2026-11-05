"""Batch tool — a sequence of actions in one gated call (issue #19).

Multi-step chores (focus a field, set two values, invoke OK) otherwise
cost one MCP round-trip and one control-gate cycle per step. Batch runs
the whole sequence inside a single tool call: the gate middleware
acquires the desktop lease once for the Batch call itself, and every
step goes through the same Desktop service methods the individual
tools use — so their per-step checkpoints keep the takeover chord and
strict policy semantics identical to single calls, mid-batch included.

Steps are validated up front (a malformed batch fails before anything
runs); execution stops at the first failed step unless
continue_on_error is set, and steps that never ran are reported as
"not run" — never silently skipped. Each step's result carries its
message or, on failure, the typed error envelope from #13.
"""

import logging
import time

from mcp.types import ToolAnnotations
from agentic_windows_mcp.desktop.control import get_controller
from agentic_windows_mcp.desktop.service import AmbiguousRefError, StaleRefError
from agentic_windows_mcp.infrastructure import with_analytics
from agentic_windows_mcp.infrastructure.errors import (
    DELIVERED_UNVERIFIED,
    DELIVERED_VERIFIED,
    classify_error,
)
from fastmcp import Context

from agentic_windows_mcp.tools._snapshot_helpers import _as_bool
from agentic_windows_mcp.tools.elements import parse_ref
from agentic_windows_mcp.tools.input import _as_loc, _resolve_label

logger = logging.getLogger(__name__)

_STEP_TOOLS = (
    "Click", "Type", "Invoke", "SetValue", "Toggle",
    "Shortcut", "Scroll", "Move", "Wait",
)
_MAX_STEPS = 50


def _bool_arg(step: dict, key: str) -> bool:
    value = step.get(key, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ValueError(f"step {step.get('tool')}: {key} must be true or false")


def _loc_arg(desktop, step: dict, *, required: bool) -> list | None:
    loc = _as_loc(step.get("loc"))
    label = step.get("label")
    if label is not None:
        if isinstance(label, bool) or not isinstance(label, int):
            raise ValueError(f"step {step.get('tool')}: label must be an integer")
        loc = _resolve_label(desktop, label)
    if loc is None and required:
        raise ValueError(f"step {step.get('tool')}: either loc or label is required")
    if loc is not None and len(loc) != 2:
        raise ValueError(f"step {step.get('tool')}: loc must be [x, y]")
    return loc


def _validate_step(index: int, step) -> dict:
    """Fail the whole batch up front on a malformed step."""
    if not isinstance(step, dict):
        raise ValueError(
            f"Batch step {index + 1} must be an object like "
            '{"tool": "Click", "loc": [x, y]}.'
        )
    tool = step.get("tool")
    if tool not in _STEP_TOOLS:
        raise ValueError(
            f"Batch step {index + 1}: unknown tool {tool!r}. "
            f"Supported steps: {', '.join(_STEP_TOOLS)}."
        )
    if tool in ("Click", "Move"):
        if step.get("loc") is None and step.get("label") is None:
            raise ValueError(f"Batch step {index + 1} ({tool}): loc or label is required.")
    elif tool == "Type":
        if not isinstance(step.get("text"), str):
            raise ValueError(f"Batch step {index + 1} (Type): text (string) is required.")
        if step.get("loc") is None and step.get("label") is None:
            raise ValueError(f"Batch step {index + 1} (Type): loc or label is required.")
    elif tool in ("Invoke", "Toggle"):
        if step.get("ref") is None:
            raise ValueError(f"Batch step {index + 1} ({tool}): ref is required.")
        parse_ref(step["ref"])
    elif tool == "SetValue":
        if step.get("ref") is None or "value" not in step:
            raise ValueError(
                f"Batch step {index + 1} (SetValue): ref and value are required."
            )
        parse_ref(step["ref"])
    elif tool == "Shortcut":
        if not isinstance(step.get("shortcut"), str) or not step["shortcut"]:
            raise ValueError(f"Batch step {index + 1} (Shortcut): shortcut is required.")
    elif tool == "Wait":
        seconds = step.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError(f"Batch step {index + 1} (Wait): seconds (number) is required.")
        if not 0 <= float(seconds) <= 120:
            raise ValueError(f"Batch step {index + 1} (Wait): seconds must be 0–120.")
    return step


def _run_step(desktop, step: dict) -> tuple[str, str | None]:
    """Execute one validated step; returns (message, success_disposition)."""
    tool = step["tool"]
    if tool == "Click":
        loc = _loc_arg(desktop, step, required=True)
        button = step.get("button", "left")
        clicks = int(step.get("clicks", 1))
        desktop.click(loc=loc, button=button, clicks=clicks)
        return f"{button} clicked at ({loc[0]},{loc[1]}).", DELIVERED_UNVERIFIED
    if tool == "Type":
        loc = _loc_arg(desktop, step, required=True)
        verification, secure = desktop.type(
            loc=loc,
            text=step["text"],
            caret_position=step.get("caret_position", "idle"),
            clear=_bool_arg(step, "clear") or _bool_arg(step, "clear_first"),
            press_enter=_bool_arg(step, "press_enter"),
            verify=_bool_arg(step, "verify"),
        )
        if secure:
            # Secure fields (#15): never echo a password from a batch either.
            return (
                f"Typed into the secure (password) field at ({loc[0]},{loc[1]}); "
                "the text is not echoed."
            ), DELIVERED_UNVERIFIED
        message = f"Typed text at ({loc[0]},{loc[1]})."
        if _bool_arg(step, "verify") and verification is not None:
            message += f" Verification: {verification[0]} — {verification[1]}"
        return message, DELIVERED_UNVERIFIED
    if tool == "Invoke":
        label = parse_ref(step["ref"])
        element = desktop.invoke_element(
            label, step.get("snapshot"),
            wait_timeout=float(step.get("wait_timeout", 3.0)),
        )
        return (
            f"Invoked @e{label}: {element.control_type} \"{element.name}\"."
        ), DELIVERED_UNVERIFIED
    if tool == "SetValue":
        label = parse_ref(step["ref"])
        element = desktop.set_element_value(
            label, step["value"], step.get("snapshot"),
            wait_timeout=float(step.get("wait_timeout", 3.0)),
        )
        if getattr(element, "is_password", False):
            return (
                f"Set @e{label} ({element.control_type} \"{element.name}\") — "
                "secure field; the value was set and is not echoed."
            ), DELIVERED_UNVERIFIED
        return (
            f"Set @e{label} ({element.control_type} \"{element.name}\") "
            f"to {step['value']!r}."
        ), DELIVERED_UNVERIFIED
    if tool == "Toggle":
        label = parse_ref(step["ref"])
        element, state = desktop.toggle_element(
            label, step.get("snapshot"),
            wait_timeout=float(step.get("wait_timeout", 3.0)),
        )
        state_text = {0: "Off", 1: "On", 2: "Indeterminate"}.get(state, str(state))
        return (
            f"Toggled @e{label}: {element.control_type} \"{element.name}\" "
            f"— now {state_text}."
        ), DELIVERED_VERIFIED
    if tool == "Shortcut":
        desktop.shortcut(step["shortcut"])
        return f"Pressed {step['shortcut']}.", DELIVERED_UNVERIFIED
    if tool == "Scroll":
        loc = _loc_arg(desktop, step, required=False)
        response = desktop.scroll(
            loc,
            step.get("type", "vertical"),
            step.get("direction", "down"),
            int(step.get("wheel_times", 1)),
        )
        if response:
            return str(response), DELIVERED_UNVERIFIED
        where = f" at ({loc[0]},{loc[1]})" if loc else ""
        return (
            f"Scrolled {step.get('type', 'vertical')} "
            f"{step.get('direction', 'down')} by "
            f"{int(step.get('wheel_times', 1))} wheel times{where}."
        ), DELIVERED_UNVERIFIED
    if tool == "Move":
        loc = _loc_arg(desktop, step, required=True)
        desktop.move(loc)
        return f"Moved to ({loc[0]},{loc[1]}).", DELIVERED_UNVERIFIED
    if tool == "Wait":
        seconds = float(step["seconds"])
        time.sleep(seconds)
        # A takeover during the wait must stop the batch before the
        # next step sends anything.
        get_controller().checkpoint_current()
        return f"Waited {seconds:g}s.", None
    raise ValueError(f"Unsupported batch step: {tool!r}")  # Unreachable post-validation.


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Batch",
        description="Run an ordered sequence of actions in ONE call under a single control-gate lease, instead of one round-trip per action. Each step is an object like {\"tool\": \"SetValue\", \"ref\": \"@e3\", \"value\": \"Ada\"}; supported step tools: Click and Move (loc or label, Click also button/clicks), Type (text, loc or label, clear/clear_first, press_enter, verify, caret_position), Invoke/Toggle (ref, optional snapshot and wait_timeout), SetValue (ref, value), Shortcut (shortcut), Scroll (loc or label optional, type, direction, wheel_times), Wait (seconds). All steps are validated before anything runs. Steps execute in order and stop at the first failure unless continue_on_error=True; the result lists every step as succeeded (with its message and delivery disposition), failed (with the typed error code and disposition from the action envelope), or not run. The batch is one auditable unit and obeys the strict policy and the takeover chord exactly like single calls — a takeover stops it at the next step boundary.",
        annotations=ToolAnnotations(
            title="Batch",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Batch-Tool", run_sync_in_thread=False)
    def batch_tool(
        steps: list[dict],
        continue_on_error: bool | str = False,
        ctx: Context = None,
    ) -> str:
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list of step objects.")
        if len(steps) > _MAX_STEPS:
            raise ValueError(f"A batch holds at most {_MAX_STEPS} steps.")
        validated = [_validate_step(index, step) for index, step in enumerate(steps)]
        keep_going = _as_bool(continue_on_error)
        desktop = get_desktop()
        lines: list[str] = []
        succeeded = 0
        stopped_at: int | None = None
        for index, step in enumerate(validated):
            number = index + 1
            if stopped_at is not None:
                lines.append(f"{number}. {step['tool']}: not run.")
                continue
            try:
                message, disposition = _run_step(desktop, step)
            except (StaleRefError, AmbiguousRefError) as e:
                disposition_obj = classify_error(e)
                lines.append(
                    f"{number}. {step['tool']}: FAILED — {e} "
                    f"[error code={disposition_obj.code} "
                    f"delivery={disposition_obj.delivery} "
                    f"retry={disposition_obj.retry}]"
                )
                if not keep_going:
                    stopped_at = index
            except Exception as e:
                disposition_obj = classify_error(e)
                lines.append(
                    f"{number}. {step['tool']}: FAILED — {type(e).__name__}: {e} "
                    f"[error code={disposition_obj.code} "
                    f"delivery={disposition_obj.delivery} "
                    f"retry={disposition_obj.retry}]"
                )
                if not keep_going:
                    stopped_at = index
            else:
                succeeded += 1
                suffix = f" [delivery={disposition}]" if disposition else ""
                lines.append(f"{number}. {step['tool']}: ok — {message}{suffix}")
        total = len(validated)
        if stopped_at is None:
            header = f"Batch: {succeeded}/{total} steps succeeded."
        else:
            header = (
                f"Batch: {succeeded}/{total} steps succeeded; "
                f"stopped at step {stopped_at + 1} "
                f"({validated[stopped_at]['tool']})."
            )
        logger.info(
            "Batch executed as one gated unit: %s steps=%s",
            header,
            [step["tool"] for step in validated],
        )
        return "\n".join([header, *lines])
