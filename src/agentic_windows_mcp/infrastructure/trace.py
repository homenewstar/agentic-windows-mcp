"""Append-only JSONL trace for the MCP server (#20).

One segment per server process under ``<config dir>/traces/``; the
first line of each segment is a meta record (schema version, server
version, pid, start time). Events are tool calls (name, outcome,
duration — never arguments, so no secure-field value or credential
can land here), gate/recovery actions, session lifecycle, and ref
registrations/resolution failures.

The trace is opt-in: set ``AGENTIC_WINDOWS_MCP_TRACE=1``. When off,
``get_trace()`` returns None and every record site is a no-op check.
Recording must never break the tool path — a failing trace write is
swallowed. Schema evolution is additive only: readers ignore fields
they do not know, and tolerate a truncated/corrupt tail (counted as
warnings) because a killed process leaves exactly that behind.

Replay screenshots are a supported but deliberately unwired option:
``TraceWriter(capture_screenshots=True)`` stores PNGs beside the
segment when a caller passes image bytes. No tool passes any today —
treat those files as sensitive if that ever changes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_ENV_FLAG = "AGENTIC_WINDOWS_MCP_TRACE"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def trace_enabled() -> bool:
    return os.environ.get(_ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def default_trace_dir() -> Path:
    from agentic_windows_mcp.infrastructure.config import CONFIG_DIR

    return CONFIG_DIR / "traces"


class TraceWriter:
    """One process's append-only segment. Thread-safe; never raises."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        server_version: str = "unknown",
        capture_screenshots: bool = False,
    ) -> None:
        self.directory = Path(directory) if directory else default_trace_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        started = datetime.now(timezone.utc)
        self.path = self.directory / f"trace-{os.getpid()}-{started:%Y%m%d%H%M%S}.jsonl"
        self.capture_screenshots = capture_screenshots
        self._lock = threading.Lock()
        self._shots = 0
        self._fh = open(self.path, "a", encoding="utf-8")
        self._write(
            {
                "type": "meta",
                "schema": SCHEMA_VERSION,
                "server_version": server_version,
                "pid": os.getpid(),
                "started_at": started.isoformat(),
                "ts": started.isoformat(),
            }
        )

    def _write(self, record: dict) -> None:
        try:
            with self._lock:
                self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                self._fh.flush()
        except Exception:
            logger.debug("trace write failed", exc_info=True)

    def record(self, event: str, screenshot: bytes | None = None, **fields) -> None:
        """Append one event. Only names/outcomes belong in fields —
        never tool arguments or values."""
        entry = {"ts": _now_iso(), "event": event, **fields}
        if screenshot is not None and self.capture_screenshots:
            try:
                with self._lock:
                    self._shots += 1
                    name = f"{self.path.stem}-{self._shots}.png"
                    (self.directory / name).write_bytes(screenshot)
                entry["screenshot"] = name
            except Exception:
                logger.debug("trace screenshot write failed", exc_info=True)
        self._write(entry)

    def close(self) -> None:
        try:
            with self._lock:
                self._fh.close()
        except Exception:
            pass


_writer: TraceWriter | None = None
_writer_lock = threading.Lock()


def init_trace(server_version: str | None = None) -> TraceWriter | None:
    """Start the process segment when tracing is enabled (idempotent)."""
    global _writer
    if not trace_enabled():
        return None
    if server_version is None:
        try:
            from importlib import metadata

            server_version = metadata.version("agentic-windows-mcp")
        except Exception:
            server_version = "unknown"
    with _writer_lock:
        if _writer is None:
            try:
                _writer = TraceWriter(server_version=server_version)
            except Exception:
                logger.warning("trace init failed; continuing without trace", exc_info=True)
                return None
        return _writer


def get_trace() -> TraceWriter | None:
    return _writer


def record_event(event: str, **fields) -> None:
    """Record through the singleton when active; a no-op otherwise."""
    writer = _writer
    if writer is not None:
        writer.record(event, **fields)


def read_trace(directory: Path | None = None) -> tuple[list[dict], int]:
    """Merge all segments in a trace directory into one timeline.

    Returns (records, warnings): records sorted by timestamp across
    segments; warnings counts unreadable lines (a truncated tail from
    a killed process is expected, not an error).
    """
    directory = Path(directory) if directory else default_trace_dir()
    records: list[dict] = []
    warnings = 0
    if not directory.exists():
        return records, warnings
    for segment in sorted(directory.glob("trace-*.jsonl")):
        try:
            lines = segment.read_text(encoding="utf-8").splitlines()
        except OSError:
            warnings += 1
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                warnings += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                warnings += 1
    records.sort(key=lambda r: str(r.get("ts") or r.get("started_at") or ""))
    return records, warnings
