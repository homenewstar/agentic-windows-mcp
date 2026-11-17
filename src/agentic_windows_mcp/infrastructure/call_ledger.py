"""Per-call execution ledger (issue #42).

Before this module, a gated tool call that hit the server-side deadline
returned ``TOOL_TIMEOUT`` and vanished: the gate released its lease and
lock, the synchronous worker thread kept running to its own completion,
its result was discarded, and there was no record anywhere that the call
had ever existed — no id, no state, no outcome. A caller deciding
whether to retry a state-changing command had to guess, and a retry
could silently execute the command a second time.

The ledger gives every gated call a server-issued id (``call-<n>``) and
a bounded in-memory record of its life:

    queued -> running -> completed | error
                      -> timed_out_worker_running -> completed | error

``timed_out_worker_running`` is the state the old design made invisible:
the deadline stopped the *wait*, not the work. The record stays in that
state until the worker actually finishes, then settles into a terminal
state with ``timed_out`` still set — so a caller can learn, before
retrying, whether the original call ran, is still running, or finished,
and how it ended.

Each record also carries the phase split the #42 investigation asked
for: ``gate_wait_seconds`` (gate entry -> handler start: lock queue +
lease arm) versus ``handler_seconds`` (handler start -> terminal state),
so latency attribution no longer has to guess where a call spent its
time. This complements the #36 aggregate metrics, which keep only
per-tool counts and bucketed durations; the ledger is per-call and
short-lived.

Storage is deliberately in-memory only, mirroring the background-jobs
registry (``powershell/jobs.py``): execution handles are an
intra-incident surface (minutes), not an audit log, and both die with
the server process. In line with the #36 discipline the ledger records
NO arguments and NO result bodies — tool name, timings, outcome code
and a truncated error string only. (A synchronous PowerShell call's
output stays retrievable through its linked jobs-registry record, which
is the jobs system's own documented contract.)

The accessors never raise into the tool path: a ledger bug must not be
able to break a tool call, so the gate wraps its bookkeeping in the
fail-soft helpers below, exactly like ``usage_metrics``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_CALLS = 64

STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_TIMED_OUT = "timed_out_worker_running"
STATE_COMPLETED = "completed"
STATE_ERROR = "error"
TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_ERROR})

_ERROR_TEXT_LIMIT = 300


def _wall(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _error_text(error: BaseException | str | None) -> Optional[str]:
    if error is None:
        return None
    if isinstance(error, str):
        text = error
    else:
        text = f"{type(error).__name__}: {error}"
    return text[:_ERROR_TEXT_LIMIT]


@dataclass
class CallRecord:
    """One gated call's life. Mutated only through :class:`CallLedger`."""

    id: str
    tool: str
    idempotency_key: Optional[str] = None
    state: str = STATE_QUEUED
    timed_out: bool = False
    job_id: Optional[str] = None
    outcome_error: Optional[str] = None
    queued_wall: float = field(default_factory=time.time)
    _queued_mono: float = field(default_factory=time.monotonic, repr=False)
    _handler_mono: Optional[float] = field(default=None, repr=False)
    _finished_mono: Optional[float] = field(default=None, repr=False)
    gate_wait_seconds: Optional[float] = None
    handler_seconds: Optional[float] = None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict:
        handler = self.handler_seconds
        if handler is None and self._handler_mono is not None:
            end = self._finished_mono or time.monotonic()
            handler = round(max(0.0, end - self._handler_mono), 3)
        return {
            "call_id": self.id,
            "tool": self.tool,
            "state": self.state,
            "timed_out": self.timed_out,
            "idempotency_key": self.idempotency_key,
            "job_id": self.job_id,
            "started_at": _wall(self.queued_wall),
            "gate_wait_seconds": self.gate_wait_seconds,
            "handler_seconds": handler,
            "error": self.outcome_error,
        }


class CallLedger:
    """Thread-safe bounded store of recent gated calls."""

    def __init__(self, max_records: int = MAX_CALLS) -> None:
        self._max = max_records
        self._records: Dict[str, CallRecord] = {}
        self._seq = 0
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------

    def start(self, tool: str, idempotency_key: Optional[str] = None) -> CallRecord:
        with self._lock:
            self._seq += 1
            record = CallRecord(
                id=f"call-{self._seq}",
                tool=tool,
                idempotency_key=idempotency_key or None,
            )
            self._records[record.id] = record
            self._evict_locked()
            return record

    def mark_running(self, record: CallRecord) -> None:
        with self._lock:
            if record.state != STATE_QUEUED:
                return
            now = time.monotonic()
            record._handler_mono = now
            record.gate_wait_seconds = round(max(0.0, now - record._queued_mono), 3)
            record.state = STATE_RUNNING

    def mark_timed_out(self, record: CallRecord) -> None:
        with self._lock:
            if record.state != STATE_RUNNING:
                return
            record.timed_out = True
            record.state = STATE_TIMED_OUT

    def mark_wait_abandoned(self, record: CallRecord) -> None:
        """The wait ended without a deadline (the caller disconnected or
        was cancelled) while the worker keeps running. Same observable
        state as a timeout — the outcome is knowable later, not now —
        but the ``timed_out`` flag stays False: no deadline fired."""
        with self._lock:
            if record.state != STATE_RUNNING:
                return
            record.state = STATE_TIMED_OUT

    def finish(
        self,
        record: CallRecord,
        *,
        ok: bool,
        error: BaseException | str | None = None,
    ) -> None:
        """Settle a record into a terminal state.

        Applies from ``running`` and ``timed_out_worker_running`` (the
        post-deadline worker finishing). A record still ``queued`` is
        settled too — its call was rejected before the handler started.
        Finishing an already-terminal record is a no-op, so the several
        code paths that can observe an ending never fight over it.
        """
        with self._lock:
            if record.terminal:
                return
            now = time.monotonic()
            record._finished_mono = now
            if record._handler_mono is not None:
                record.handler_seconds = round(
                    max(0.0, now - record._handler_mono), 3
                )
            record.state = STATE_COMPLETED if ok else STATE_ERROR
            record.outcome_error = None if ok else _error_text(error)

    def abandon(self, record: CallRecord, error: BaseException | str | None) -> None:
        """Settle a record whose call never reached the deadline
        machinery's own terminal handling (rejected while queued, lease
        refused, caller cancelled). A record already in
        ``timed_out_worker_running`` belongs to its worker's completion
        callback and is left alone."""
        with self._lock:
            if record.state not in (STATE_QUEUED, STATE_RUNNING):
                return
        self.finish(record, ok=False, error=error)

    def link_job(self, call_id: str, job_id: str) -> None:
        with self._lock:
            record = self._records.get(call_id)
            if record is not None:
                record.job_id = job_id

    # -- lookup ---------------------------------------------------------

    def get(self, call_id: str) -> Optional[CallRecord]:
        with self._lock:
            return self._records.get(call_id)

    def find_by_idempotency_key(
        self, key: str, tool: Optional[str] = None
    ) -> Optional[CallRecord]:
        if not key:
            return None
        with self._lock:
            return self._find_by_key_locked(key, tool)

    def _find_by_key_locked(
        self, key: str, tool: Optional[str] = None
    ) -> Optional[CallRecord]:
        matches = [
            r
            for r in self._records.values()
            if r.idempotency_key == key and (tool is None or r.tool == tool)
        ]
        if not matches:
            return None
        # Ids are sequential; the newest record for the key wins (a key
        # reused after eviction of the original starts a fresh life).
        return max(matches, key=lambda r: int(r.id.split("-", 1)[1]))

    def start_or_find(
        self, tool: str, idempotency_key: str
    ) -> tuple[CallRecord, bool]:
        """Atomically return the existing record for an idempotency key
        or create a fresh one — two simultaneous first submissions of
        the same key must not both reach the handler. Returns
        ``(record, created)``."""
        with self._lock:
            existing = self._find_by_key_locked(idempotency_key, tool)
            if existing is not None:
                return existing, False
            self._seq += 1
            record = CallRecord(
                id=f"call-{self._seq}",
                tool=tool,
                idempotency_key=idempotency_key,
            )
            self._records[record.id] = record
            self._evict_locked()
            return record, True

    def recent(self, limit: int = 10) -> List[CallRecord]:
        with self._lock:
            records = sorted(
                self._records.values(),
                key=lambda r: int(r.id.split("-", 1)[1]),
                reverse=True,
            )
        return records[:limit]

    def summary(self, limit: int = 10) -> dict:
        with self._lock:
            counts: Dict[str, int] = {}
            for record in self._records.values():
                counts[record.state] = counts.get(record.state, 0) + 1
            total = len(self._records)
        return {
            "total": total,
            "by_state": counts,
            "recent": [r.to_dict() for r in self.recent(limit)],
        }

    # -- internals ------------------------------------------------------

    def _evict_locked(self) -> None:
        """Drop the oldest terminal records once over capacity. Live
        records are never evicted — a running call must stay lookup-able
        no matter how much later traffic arrives."""
        while len(self._records) > self._max:
            terminal = [r for r in self._records.values() if r.terminal]
            if not terminal:
                break
            oldest = min(terminal, key=lambda r: int(r.id.split("-", 1)[1]))
            del self._records[oldest.id]


# ---------------------------------------------------------------------------
# Process-wide singleton + fail-soft accessors
# ---------------------------------------------------------------------------

_ledger: Optional[CallLedger] = None
_ledger_lock = threading.Lock()


def get_call_ledger() -> CallLedger:
    global _ledger
    if _ledger is None:
        with _ledger_lock:
            if _ledger is None:
                _ledger = CallLedger()
    return _ledger


def begin_call_record(
    tool: str, idempotency_key: Optional[str] = None
) -> Optional[CallRecord]:
    """Start a ledger record. Never raises (see module docstring)."""
    try:
        return get_call_ledger().start(tool, idempotency_key)
    except Exception as exc:
        logger.debug("call ledger start failed: %r", exc)
        return None


def find_idempotent_call(
    key: Optional[str], tool: str
) -> Optional[CallRecord]:
    if not key:
        return None
    try:
        return get_call_ledger().find_by_idempotency_key(key, tool)
    except Exception as exc:
        logger.debug("call ledger idempotency lookup failed: %r", exc)
        return None


def start_or_find_call(
    tool: str, idempotency_key: str
) -> tuple[Optional[CallRecord], bool]:
    """Atomic key claim for the gate. Never raises: on a ledger failure
    it returns ``(None, True)`` so the caller proceeds unrecorded rather
    than breaking the call."""
    try:
        return get_call_ledger().start_or_find(tool, idempotency_key)
    except Exception as exc:
        logger.debug("call ledger start_or_find failed: %r", exc)
        return None, True


def abandon_call_record(record: Optional[CallRecord], error: BaseException) -> None:
    if record is None:
        return
    try:
        get_call_ledger().abandon(record, error)
    except Exception as exc:
        logger.debug("call ledger abandon failed: %r", exc)


def link_call_job(call_id: Optional[str], job_id: str) -> None:
    if not call_id:
        return
    try:
        get_call_ledger().link_job(call_id, job_id)
    except Exception as exc:
        logger.debug("call ledger job link failed: %r", exc)


def call_ledger_summary() -> dict:
    """Summary for ControlStatus. Never raises."""
    try:
        return get_call_ledger().summary()
    except Exception as exc:
        return {"error": f"call ledger unavailable: {exc!r}"}


def call_ledger_lookup(call_id: str) -> dict:
    """One call's full record for ControlStatus. Never raises."""
    try:
        ledger = get_call_ledger()
        record = ledger.get(call_id)
        if record is None:
            return {
                "call_id": call_id,
                "error": "unknown call id",
                "known_call_ids": [r.id for r in ledger.recent(20)],
            }
        return record.to_dict()
    except Exception as exc:
        return {"call_id": call_id, "error": f"call ledger unavailable: {exc!r}"}
