"""Request-local desktop control token and cross-thread input-step counter."""

from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass


@dataclass
class StepCounter:
    # AnyIO copies context variables into worker threads. The value object is
    # shared, so a synchronous tool's executed steps remain visible to middleware.
    value: int = 0


current_token: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "agentic_windows_mcp_control_token", default=None
)
current_steps: contextvars.ContextVar[StepCounter | None] = contextvars.ContextVar(
    "agentic_windows_mcp_control_steps", default=None
)
# Copied contexts share this event: cancelling the await cannot stop a running
# worker, but closing its request must prevent its next desktop input step.
current_request_closed: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "agentic_windows_mcp_control_request_closed", default=None
)
# The #42 call-ledger id of the gated call whose handler is running in
# this context. Set by the control gate just before dispatch; copied
# into worker threads like the other request context, so a tool (the
# synchronous PowerShell path) can link its jobs-registry record to the
# call that owns it.
current_call_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agentic_windows_mcp_call_id", default=None
)


def get_step_count() -> int:
    counter = current_steps.get()
    return 0 if counter is None else counter.value
