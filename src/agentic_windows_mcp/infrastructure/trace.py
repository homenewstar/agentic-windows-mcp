"""Append-only JSONL trace for the MCP server (#20).

One segment per server process under ``<config dir>/traces/``; the
first line of each segment is a meta record (schema version, server
version, pid, start time). Events are tool calls (name, outcome,
duration — never arguments, so no secure-field value or credential
can land here), gate/recovery actions, session lifecycle, and ref
registrations/resolution failures.

The trace is opt-in. Since #29 the switch lives in config.toml
(``[trace] enabled = true``, optional ``directory`` and
``capture_screenshots``) so it works under the Scheduled Task launch;
``AGENTIC_WINDOWS_MCP_TRACE`` remains as an override for the enabled
flag only — it can never turn screenshot capture on. A session can
also request tracing explicitly (Session tool, ``trace=True``), which
starts the writer for that session even when both switches are off.
When off, ``get_trace()`` returns None and every record site is a
no-op check. Recording must never break the tool path — a failing
trace write is swallowed. Schema evolution is additive only: readers
ignore fields they do not know, and tolerate a truncated/corrupt tail
(counted as warnings) because a killed process leaves exactly that
behind.

Replay screenshots (#29): when ``capture_screenshots`` is on (config
``[trace] capture_screenshots = true``, or a session started with
``artifacts=True``), the tool-call wrapper stores a frame from just
before and just after each action call beside the segment. The frames
are **unredacted screen content** — anything visible on the desktop
lands in them, including other apps' windows. Treat the files as
sensitive; the flag is off by default and no environment variable can
enable it.
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
    """The env-var switch alone (kept for compatibility)."""
    return os.environ.get(_ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def resolve_trace_settings(config=None) -> dict:
    """Effective trace settings from config + environment (#29).

    The env var, when set to anything non-empty, overrides the config's
    ``enabled`` flag in either direction (so an operator can force the
    trace off without editing the file). ``directory`` and
    ``capture_screenshots`` come from the config only — in particular
    the environment can never enable screenshot capture.
    """
    enabled = False
    directory: Path | None = None
    capture_screenshots = False
    if config is not None:
        enabled = bool(getattr(config, "enabled", False))
        raw_dir = str(getattr(config, "directory", "") or "").strip()
        if raw_dir:
            directory = Path(raw_dir).expanduser()
        capture_screenshots = bool(getattr(config, "capture_screenshots", False))
    env = os.environ.get(_ENV_FLAG, "").strip().lower()
    if env:
        enabled = env in ("1", "true", "yes", "on")
    return {
        "enabled": enabled,
        "directory": directory,
        "capture_screenshots": capture_screenshots,
    }


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

# Registered by the server bootstrap (it owns the desktop); the
# tool-call wrapper asks it for replay frames. Kept as a plain callable
# so this module never imports the desktop stack.
_screenshot_provider = None


def set_screenshot_provider(provider) -> None:
    global _screenshot_provider
    _screenshot_provider = provider


def capture_frame() -> bytes | None:
    """One replay frame from the registered provider, or None.

    Returns None unless a writer is active with capture_screenshots on,
    so callers never pay for a capture nobody will store. Any provider
    failure degrades to no frame — artifacts must never break a call.
    """
    writer = _writer
    if writer is None or not writer.capture_screenshots:
        return None
    provider = _screenshot_provider
    if provider is None:
        return None
    try:
        frame = provider()
    except Exception:
        logger.debug("trace screenshot provider failed", exc_info=True)
        return None
    return frame if isinstance(frame, (bytes, bytearray)) else None


def _resolve_server_version(server_version: str | None) -> str:
    if server_version is not None:
        return server_version
    try:
        from importlib import metadata

        return metadata.version("agentic-windows-mcp")
    except Exception:
        return "unknown"


def init_trace(server_version: str | None = None, config=None) -> TraceWriter | None:
    """Start the process segment when tracing is enabled (idempotent).

    ``config`` is the ``[trace]`` section of the server config (#29);
    without one, only the env var can enable the trace (pre-#29
    behaviour).
    """
    global _writer
    settings = resolve_trace_settings(config)
    if not settings["enabled"]:
        return None
    server_version = _resolve_server_version(server_version)
    with _writer_lock:
        if _writer is None:
            try:
                _writer = TraceWriter(
                    settings["directory"],
                    server_version=server_version,
                    capture_screenshots=settings["capture_screenshots"],
                )
            except Exception:
                logger.warning("trace init failed; continuing without trace", exc_info=True)
                return None
        return _writer


def ensure_trace(
    *, capture_screenshots: bool = False, server_version: str | None = None
) -> TraceWriter | None:
    """Start the writer on explicit request (Session tool, #29).

    Unlike init_trace this ignores the enabled switches — the request
    itself is the opt-in. On an already-running writer it can only turn
    screenshot capture *on*; it never turns an existing setting off.
    """
    global _writer
    server_version = _resolve_server_version(server_version)
    with _writer_lock:
        if _writer is None:
            try:
                _writer = TraceWriter(
                    server_version=server_version,
                    capture_screenshots=capture_screenshots,
                )
            except Exception:
                logger.warning("trace start failed", exc_info=True)
                return None
        elif capture_screenshots:
            _writer.capture_screenshots = True
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
