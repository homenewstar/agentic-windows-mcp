"""Global MCP ownership gate and best-effort session state notifications."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger(__name__)


def tool_timeout_seconds() -> float:
    """Per-call deadline for tools serialized behind the control gate.

    One hung call (a browser evaluate against a renderer blocked by a native
    dialog, a subprocess whose output pipe is held by a long-lived child)
    otherwise holds the gate lock forever and wedges every later call until
    the server is restarted. Bounded by AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS;
    a value <= 0 disables the deadline.
    """
    raw = os.getenv("AGENTIC_WINDOWS_MCP_TOOL_TIMEOUT_SECONDS", "180").strip()
    try:
        return float(raw)
    except ValueError:
        return 180.0


@dataclass
class _Session:
    session: Any
    loop: asyncio.AbstractEventLoop
    last_seen: float


class ControlNotifier:
    """Send state transitions to live MCP sessions without holding control locks."""

    def __init__(self, controller: Any, *, ttl: float = 60.0) -> None:
        self.controller = controller
        self.ttl = ttl
        self._sessions: dict[int, _Session] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reaper: asyncio.Task | None = None
        self._state = "unavailable"
        self._generation = -1
        self._active = False
        self._subscribed = False
        self.push_enabled = True
        self._pending: list[dict] = []
        self._flush_handle: asyncio.TimerHandle | None = None
        self._send_tasks: set[asyncio.Task] = set()

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._active = True
        initial = self.controller.status()
        self._state = initial["state"]
        self._generation = initial["generation"]
        if not self._subscribed:
            self.controller.subscribe(self._on_state)
            self._subscribed = True
        self._reaper = self._loop.create_task(self._reap())

    async def close(self) -> None:
        self._active = False
        if self._flush_handle:
            self._flush_handle.cancel()
            self._flush_handle = None
        self._pending.clear()
        if self._reaper:
            self._reaper.cancel()
            try:
                await self._reaper
            except asyncio.CancelledError:
                pass
            self._reaper = None
        tasks = tuple(self._send_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._send_tasks.clear()
        self._sessions.clear()
        self._loop = None

    def remember(self, context: MiddlewareContext) -> None:
        """Refresh only sessions seen in a real request; stateless sessions expire."""
        if not self._active or not self.push_enabled or context.fastmcp_context is None:
            return
        request_loop = asyncio.get_running_loop()
        if request_loop is not self._loop:
            # A session on another event loop cannot be sent or awaited safely
            # by this server's notifier; the next tool response remains the fallback.
            return
        try:
            session = context.fastmcp_context.session
        except RuntimeError:
            return
        self._sessions[id(session)] = _Session(session, request_loop, time.monotonic())

    async def _reap(self) -> None:
        while self._active:
            await asyncio.sleep(10)
            cutoff = time.monotonic() - self.ttl
            for key, value in tuple(self._sessions.items()):
                if value.last_seen < cutoff or value.loop.is_closed():
                    self._sessions.pop(key, None)

    def _on_state(self, status: dict) -> None:
        loop = self._loop
        if not self._active or loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._enqueue, status.copy())
        except RuntimeError:
            # Shutdown can close the event loop between the check and scheduling.
            logger.debug("Control event loop closed before notification dispatch")

    def _enqueue(self, status: dict) -> None:
        if not self._active:
            return
        self._pending.append(status)
        if self._flush_handle is None:
            # State callbacks can race after releasing the coordinator lock.
            # A short batch sorts by generation before notifying clients.
            self._flush_handle = asyncio.get_running_loop().call_later(0.02, self._flush)

    def _flush(self) -> None:
        self._flush_handle = None
        pending, self._pending = self._pending, []
        for status in sorted(pending, key=lambda item: item["generation"]):
            self._dispatch(status)

    def _dispatch(self, status: dict) -> None:
        if not self._active:
            return
        if status["generation"] <= self._generation:
            return  # Cross-thread delivery can reorder old state events.
        self._generation = status["generation"]
        previous, current = self._state, status["state"]
        self._state = current
        event = None
        if current == "user" and previous in ("ready", "ai", "takeover_pending"):
            event = "USER_CONTROL"
        elif current == "ready" and previous == "user":
            event = "AI_CONTROL_AVAILABLE"
        if event is None:
            return
        cutoff = time.monotonic() - self.ttl
        for key, value in tuple(self._sessions.items()):
            if value.last_seen < cutoff or value.loop.is_closed():
                self._sessions.pop(key, None)
                continue
            task = value.loop.create_task(self._send(key, value, event, status))
            self._send_tasks.add(task)
            task.add_done_callback(self._send_tasks.discard)

    async def _send(self, key: int, entry: _Session, event: str, status: dict) -> None:
        if not self._active:
            return
        try:
            await entry.session.send_log_message(
                level="notice",
                data={"event": event, "status": status},
                logger="agentic-windows-mcp.control",
            )
        except Exception:
            self._sessions.pop(key, None)
            logger.debug("MCP control notification failed; session removed", exc_info=True)


class ControlToolGate(Middleware):
    """Apply one configured ownership policy at the MCP boundary."""

    def __init__(self, controller: Any, notifier: ControlNotifier) -> None:
        self.controller = controller
        self.notifier = notifier
        self._call_lock = asyncio.Lock()

    @staticmethod
    def _blocked(exc: Exception) -> ToolError:
        return ToolError(
            json.dumps(
                {"code": exc.code, "status": exc.status},
                ensure_ascii=False,
            )
        )

    async def _begin_call(self, name: str) -> int:
        """Wait for the visible lease off-loop, retaining ownership on cancel."""
        worker = asyncio.create_task(asyncio.to_thread(self.controller.begin_call, name))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # The worker cannot be stopped safely mid-ack. Keep the gate lock
            # until it finishes, then balance a lease created after cancellation.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                try:
                    token = worker.result()
                except Exception:
                    pass  # A failed begin_call never created a usable lease.
                else:
                    self.controller.end_call(token)
            raise

    async def _call_with_deadline(
        self, context: MiddlewareContext, call_next: Callable, record: Any = None
    ) -> Any:
        """Run a gated call under the per-call deadline.

        The deadline stops the *wait*, not necessarily the work: the
        handler runs in its own task, shielded from the deadline, so on
        timeout the caller's finally still releases the lease and the
        gate lock (and the request-closed marker set by that finally
        blocks the worker's next desktop input step) — but the worker
        keeps running to its own completion, exactly as a synchronous
        worker always could. What changed with the #42 call ledger is
        that the continuation is no longer silent: the record moves to
        ``timed_out_worker_running`` and settles into a terminal state
        when the worker actually finishes, and the TOOL_TIMEOUT payload
        carries the call id so the caller can look the outcome up via
        ControlStatus before deciding whether to retry.
        """
        from agentic_windows_mcp.desktop.control_context import current_call_id
        from agentic_windows_mcp.infrastructure.call_ledger import get_call_ledger

        ledger = get_call_ledger()
        if record is not None:
            ledger.mark_running(record)
        call_marker = current_call_id.set(record.id if record is not None else None)
        try:
            timeout = tool_timeout_seconds()
            if timeout <= 0:
                try:
                    result = await call_next(context)
                except BaseException as exc:
                    if record is not None:
                        ledger.finish(record, ok=False, error=exc)
                    raise
                if record is not None:
                    ledger.finish(record, ok=True)
                return result
            task = asyncio.ensure_future(call_next(context))
            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except asyncio.TimeoutError:
                if task.done() and not task.cancelled():
                    # The handler raised TimeoutError itself, before the
                    # deadline: that is an ordinary handler error, not a
                    # deadline expiry.
                    exc = task.exception()
                    if record is not None:
                        ledger.finish(record, ok=False, error=exc)
                    raise exc
                if record is not None:
                    ledger.mark_timed_out(record)
                    task.add_done_callback(
                        lambda done: self._settle_timed_out_call(ledger, record, done)
                    )
                name = context.message.name
                logger.error(
                    "Tool %s exceeded the %.0fs deadline; releasing the control gate",
                    name,
                    timeout,
                )
                raise ToolError(
                    json.dumps(
                        {
                            "code": "TOOL_TIMEOUT",
                            "tool": name,
                            "timeout_seconds": timeout,
                            "call_id": record.id if record is not None else None,
                            "note": (
                                "The deadline stopped the wait, not necessarily "
                                "the work: execution may continue server-side. "
                                "Look the outcome up via ControlStatus with "
                                "this call_id before retrying — a blind retry "
                                "can execute the command twice."
                            ),
                        },
                        ensure_ascii=False,
                    )
                ) from None
            except asyncio.CancelledError:
                # The caller went away (disconnect / client timeout). The
                # worker is in the same position as after a deadline —
                # still running, outcome knowable later — so hand the
                # record to the same settle callback instead of closing
                # it with a lie.
                if record is not None:
                    if task.done():
                        self._settle_timed_out_call(ledger, record, task)
                    else:
                        ledger.mark_wait_abandoned(record)
                        task.add_done_callback(
                            lambda done: self._settle_timed_out_call(
                                ledger, record, done
                            )
                        )
                raise
            except BaseException as exc:
                if record is not None:
                    ledger.finish(record, ok=False, error=exc)
                raise
            if record is not None:
                ledger.finish(record, ok=True)
            return result
        finally:
            current_call_id.reset(call_marker)

    @staticmethod
    def _settle_timed_out_call(ledger: Any, record: Any, task: asyncio.Task) -> None:
        """Record a post-deadline worker's real terminal state (#42)."""
        try:
            if task.cancelled():
                ledger.finish(record, ok=False, error="worker cancelled")
                return
            exc = task.exception()
            if exc is not None:
                ledger.finish(record, ok=False, error=exc)
            else:
                ledger.finish(record, ok=True)
        except Exception:
            logger.debug("call ledger settle failed", exc_info=True)

    @staticmethod
    def _claim_record(name: str, idem_key: str | None) -> tuple[Any, bool]:
        """Create this call's ledger record (#42), or find the original
        when the idempotency key was already submitted. Returns
        ``(record, proceed)`` — ``proceed`` is False when the key names
        an earlier call and this submission must be answered from that
        record instead of executing. Without a key a fresh record is
        always created. Ledger failures degrade to ``(None, True)``:
        bookkeeping must never break a call."""
        from agentic_windows_mcp.infrastructure.call_ledger import (
            begin_call_record,
            start_or_find_call,
        )

        if idem_key:
            record, created = start_or_find_call(name, idem_key)
            return record, created or record is None
        return begin_call_record(name), True

    @staticmethod
    def _idempotent_replay(record: Any) -> Any:
        """Answer a duplicate submission from the original call's ledger
        record instead of executing the command a second time (#42)."""
        from mcp.types import CallToolResult, TextContent

        payload = record.to_dict()
        if record.terminal:
            payload["code"] = "IDEMPOTENT_REPLAY"
            payload["note"] = (
                "This idempotency key was already submitted; the command was "
                "NOT executed again. This is the original call's recorded "
                "outcome."
            )
        else:
            payload["code"] = "IDEMPOTENT_IN_PROGRESS"
            payload["note"] = (
                "This idempotency key was already submitted and the original "
                "call has not finished; nothing was executed again. Look the "
                "call up via ControlStatus with its call_id."
            )
        text = json.dumps(payload, ensure_ascii=False)
        # A synchronous PowerShell original leaves its output in the jobs
        # registry; replay it in the tool's own shape so the caller gets
        # the same answer the first call produced.
        if record.tool == "PowerShell" and record.job_id:
            try:
                from agentic_windows_mcp.powershell.jobs import JOBS
                from agentic_windows_mcp.powershell.utils import strip_clixml

                status = JOBS.get(record.job_id).status()
                if status["state"] == "exited":
                    out = JOBS.output(record.job_id, "both", 0)
                    text = (
                        f"[idempotent replay of {record.id}; the command was "
                        "not executed again]\n"
                        f"Response: {strip_clixml(out.get('stdout', ''))}\n"
                        f"Status Code: {status['exit_code']}"
                    )
                    if out.get("stderr"):
                        text += f"\nStderr: {strip_clixml(out['stderr'])}"
            except Exception:
                logger.debug(
                    "idempotent replay via jobs registry failed", exc_info=True
                )
        return CallToolResult(
            content=[TextContent(type="text", text=text)], isError=False
        )

    async def on_call_tool(self, context: MiddlewareContext, call_next: Callable) -> Any:
        self.notifier.remember(context)
        name = context.message.name
        if name == "ControlStatus":
            return await call_next(context)
        arguments = getattr(context.message, "arguments", None)
        idem_key: str | None = None
        if isinstance(arguments, dict):
            candidate = arguments.get("idempotency_key")
            if isinstance(candidate, str) and candidate:
                idem_key = candidate
        if not self.controller.enabled:
            # Explicit opt-out only: a failed enabled controller still fails closed.
            # Keep shared desktop operations serialized, without creating a lease.
            record, proceed = self._claim_record(name, idem_key)
            if not proceed:
                return self._idempotent_replay(record)
            try:
                async with self._call_lock:
                    return await self._call_with_deadline(context, call_next, record)
            except BaseException as exc:
                from agentic_windows_mcp.infrastructure.call_ledger import (
                    abandon_call_record,
                )

                abandon_call_record(record, exc)
                raise
        from agentic_windows_mcp.tools.control_policy import requires_control

        if not requires_control(self.controller.policy, name, arguments):
            # Background work neither waits for the desktop lock nor creates a lease.
            # Resource-specific serialization (such as the UIA worker) still applies.
            return await call_next(context)
        record, proceed = self._claim_record(name, idem_key)
        if not proceed:
            return self._idempotent_replay(record)
        # Waiting behind an already running external command must not hide a
        # user takeover. Poll ownership while waiting, then recheck under lock.
        from agentic_windows_mcp.desktop.control import ControlBlocked

        def reject_user_state() -> None:
            status = self.controller.status()
            if status["state"] not in ("ready", "ai"):
                raise self._blocked(
                    ControlBlocked(
                        {"user": "USER_CONTROL", "takeover_pending": "TAKEOVER_PENDING"}.get(
                            status["state"], "CONTROL_UNAVAILABLE"
                        ),
                        status,
                    )
                )

        try:
            reject_user_state()
            while True:
                try:
                    await asyncio.wait_for(self._call_lock.acquire(), timeout=0.1)
                    break
                except asyncio.TimeoutError:
                    reject_user_state()
            try:
                try:
                    token = await self._begin_call(name)
                except Exception as exc:
                    from agentic_windows_mcp.desktop.control import ControlBlocked

                    if not isinstance(exc, ControlBlocked):
                        raise
                    raise self._blocked(exc) from exc
                from agentic_windows_mcp.desktop.control_context import (
                    current_token,
                    current_steps,
                    current_request_closed,
                    StepCounter,
                    get_step_count,
                )

                context_token = current_token.set(token)
                steps_token = current_steps.set(StepCounter())
                # A worker may outlive this await. Revoke only this request, not the
                # shared ownership generation used by subsequent calls.
                request_closed = threading.Event()
                request_marker = current_request_closed.set(request_closed)
                try:
                    # A synchronous indicator failure can invalidate the lease
                    # during begin_call's state notification.
                    self.controller.checkpoint(token)
                    return await self._call_with_deadline(context, call_next, record)
                except Exception as exc:
                    from agentic_windows_mcp.desktop.control import ControlBlocked
                    from agentic_windows_mcp.desktop.control_ledger import InputUnavailable

                    if isinstance(exc, ControlBlocked):
                        raise self._blocked(exc) from exc
                    if isinstance(exc, InputUnavailable):
                        status = self.controller.status()
                        status["executed_steps"] = get_step_count()
                        code = (
                            "CONTROL_UNAVAILABLE"
                            if status["state"] == "unavailable"
                            else "CONTROL_PREEMPTED"
                        )
                        raise self._blocked(ControlBlocked(code, status)) from exc
                    raise
                finally:
                    request_closed.set()
                    current_request_closed.reset(request_marker)
                    current_steps.reset(steps_token)
                    current_token.reset(context_token)
                    self.controller.end_call(token)
            finally:
                self._call_lock.release()
        except BaseException as exc:
            # Settle the ledger record if this call never reached a
            # terminal state (rejected while queued, lease refused,
            # caller cancelled). abandon() leaves terminal records and
            # timed-out records — owned by the worker's settle callback —
            # untouched.
            from agentic_windows_mcp.infrastructure.call_ledger import (
                abandon_call_record,
            )

            abandon_call_record(record, exc)
            raise
