"""Durable per-tool usage metrics (issue #36).

The agent's own operating history showed which tools actually get used
and which fail — but only by mining session transcripts after the fact.
This module keeps the aggregate server-side: per tool name, call and
success counts, error counts by #13 typed code, and a latency
histogram coarse enough for p50/p90. NOTHING else is recorded: no
arguments, no results, no error text — names, outcomes and durations
only.

Persistence: a versioned JSON document (``usage-stats.json``) in the
config dir, flushed atomically (tmp file + os.replace) every so often
and on shutdown, loaded at startup. A missing or corrupt file never
blocks startup — metrics start fresh and the reason is logged.

The singleton accessors never raise into the tool path: a metrics bug
must not be able to break a tool call.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STATS_FILENAME = "usage-stats.json"

# Latency histogram bucket edges in milliseconds; a sample lands in the
# first bucket whose edge is >= the sample, or in the final overflow
# bucket. Percentiles read back as the edge of the bucket where the
# cumulative count crosses the quantile — an estimate by design (the
# store keeps counts, never raw samples).
HISTOGRAM_EDGES_MS = (
    5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 300000,
)

_FLUSH_EVERY_RECORDS = 25
_FLUSH_MIN_INTERVAL_SECONDS = 30.0


def default_stats_path() -> Path:
    # Lazy import, mirroring trace.default_trace_dir: CONFIG_DIR lives
    # in infrastructure.config, imported here to keep this module
    # importable without the config chain.
    from agentic_windows_mcp.infrastructure.config import CONFIG_DIR

    return Path(CONFIG_DIR) / STATS_FILENAME


def _percentile(hist: list, q: float) -> Optional[int]:
    total = sum(hist)
    if total == 0:
        return None
    target = q * total
    cumulative = 0
    for idx, count in enumerate(hist):
        cumulative += count
        if cumulative >= target:
            if idx < len(HISTOGRAM_EDGES_MS):
                return HISTOGRAM_EDGES_MS[idx]
            return HISTOGRAM_EDGES_MS[-1]
    return HISTOGRAM_EDGES_MS[-1]


class _ToolStats:
    __slots__ = ("calls", "successes", "errors", "hist")

    def __init__(self) -> None:
        self.calls = 0
        self.successes = 0
        self.errors: Dict[str, int] = {}
        self.hist = [0] * (len(HISTOGRAM_EDGES_MS) + 1)

    def record(self, ok: bool, duration_ms: float, error_code: Optional[str]) -> None:
        self.calls += 1
        if ok:
            self.successes += 1
        elif error_code:
            self.errors[error_code] = self.errors.get(error_code, 0) + 1
        for idx, edge in enumerate(HISTOGRAM_EDGES_MS):
            if duration_ms <= edge:
                self.hist[idx] += 1
                break
        else:
            self.hist[-1] += 1

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "successes": self.successes,
            "errors": dict(self.errors),
            "hist": list(self.hist),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "_ToolStats":
        stats = cls()
        stats.calls = int(data.get("calls", 0))
        stats.successes = int(data.get("successes", 0))
        stats.errors = {str(k): int(v) for k, v in dict(data.get("errors", {})).items()}
        hist = [int(x) for x in list(data.get("hist", []))]
        if len(hist) == len(stats.hist):
            stats.hist = hist
        return stats

    def summary(self) -> dict:
        errors_total = sum(self.errors.values())
        return {
            "calls": self.calls,
            "successes": self.successes,
            "errors": dict(self.errors),
            "error_rate": round(errors_total / self.calls, 4) if self.calls else 0.0,
            "latency_ms": {
                "p50": _percentile(self.hist, 0.50),
                "p90": _percentile(self.hist, 0.90),
            },
        }


class UsageMetrics:
    """Thread-safe aggregate store with atomic JSON persistence."""

    def __init__(self, path: Optional[Path] = None, since: Optional[float] = None) -> None:
        self._path = Path(path) if path is not None else default_stats_path()
        self._since = float(since) if since is not None else time.time()
        self._tools: Dict[str, _ToolStats] = {}
        self._lock = threading.Lock()
        self._records_since_flush = 0
        self._last_flush = time.monotonic()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def since(self) -> float:
        return self._since

    # -- recording ------------------------------------------------------

    def record(
        self,
        tool: str,
        *,
        ok: bool,
        duration_ms: float,
        error_code: Optional[str] = None,
    ) -> None:
        with self._lock:
            stats = self._tools.get(tool)
            if stats is None:
                stats = self._tools[tool] = _ToolStats()
            stats.record(ok, duration_ms, error_code)
            self._records_since_flush += 1
            due = self._records_since_flush >= _FLUSH_EVERY_RECORDS and (
                time.monotonic() - self._last_flush >= _FLUSH_MIN_INTERVAL_SECONDS
            )
        if due:
            self.flush()

    # -- persistence ----------------------------------------------------

    def _document(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "since": self._since,
            "tools": {name: stats.to_dict() for name, stats in self._tools.items()},
        }

    def flush(self) -> None:
        try:
            with self._lock:
                document = self._document()
                self._records_since_flush = 0
                self._last_flush = time.monotonic()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(document, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception as exc:  # persistence must never break the server
            logger.warning("usage metrics flush failed: %r", exc)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "UsageMetrics":
        """Load persisted stats; any problem means a fresh store + a log
        line — startup is never blocked by a metrics file."""
        target = Path(path) if path is not None else default_stats_path()
        try:
            raw = target.read_text(encoding="utf-8")
            document = json.loads(raw)
            if not isinstance(document, dict) or document.get("schema") != SCHEMA_VERSION:
                logger.warning(
                    "usage stats at %s have an unknown schema; starting fresh", target
                )
                return cls(path=target)
            metrics = cls(path=target, since=float(document.get("since") or time.time()))
            for name, data in dict(document.get("tools", {})).items():
                metrics._tools[str(name)] = _ToolStats.from_dict(dict(data))
            return metrics
        except FileNotFoundError:
            return cls(path=target)
        except Exception as exc:
            logger.warning(
                "usage stats at %s are unreadable (%r); starting fresh", target, exc
            )
            return cls(path=target)

    def reset(self) -> None:
        """Drop all counters and remove the persisted file (the documented
        reset path, also exposed as the usage-reset CLI subcommand)."""
        with self._lock:
            self._tools = {}
            self._since = time.time()
            self._records_since_flush = 0
        try:
            self._path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("usage metrics reset could not remove %s: %r", self._path, exc)

    # -- reporting --------------------------------------------------------

    def summary(self) -> dict:
        with self._lock:
            tools = {name: stats.summary() for name, stats in self._tools.items()}
            since = self._since
        total_calls = sum(t["calls"] for t in tools.values())
        total_errors = sum(sum(t["errors"].values()) for t in tools.values())
        return {
            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since)),
            "window_seconds": int(time.time() - since),
            "tools": tools,
            "totals": {
                "calls": total_calls,
                "errors": total_errors,
                "error_rate": round(total_errors / total_calls, 4) if total_calls else 0.0,
            },
        }


# ---------------------------------------------------------------------------
# Process-wide singleton + fail-soft accessors
# ---------------------------------------------------------------------------

_metrics: Optional[UsageMetrics] = None


def init_usage_metrics(path: Optional[Path] = None) -> UsageMetrics:
    """Load (or start) the process-wide store. Called from the server
    lifespan; safe to call again — returns the existing store."""
    global _metrics
    if _metrics is None:
        _metrics = UsageMetrics.load(path)
    return _metrics


def get_usage_metrics() -> UsageMetrics:
    global _metrics
    if _metrics is None:
        _metrics = UsageMetrics()
    return _metrics


def record_usage(
    tool: str,
    *,
    ok: bool,
    duration_ms: float,
    error_code: Optional[str] = None,
) -> None:
    """Record one tool call. Never raises: metrics sit on the tool path
    of every call, so a metrics failure must be invisible to the tool."""
    try:
        get_usage_metrics().record(
            tool, ok=ok, duration_ms=duration_ms, error_code=error_code
        )
    except Exception as exc:
        logger.debug("record_usage failed: %r", exc)


def usage_summary() -> dict:
    """Summary for ControlStatus. Never raises."""
    try:
        return get_usage_metrics().summary()
    except Exception as exc:
        return {"error": f"usage metrics unavailable: {exc!r}"}


def flush_usage_metrics() -> None:
    try:
        if _metrics is not None:
            _metrics.flush()
    except Exception as exc:
        logger.debug("flush_usage_metrics failed: %r", exc)
