"""Issue #39: a non-server process must never write usage-stats.json.

On 2026-11-09 the full test suite ran on the server's own PC and its
25-record metrics flush replaced the live `usage-stats.json` (~2 days
of #36 data) with a fresh store: `get_usage_metrics()` built a
persisting store whenever the singleton was unset — only the server
lifespan loads from disk — and nothing isolated the suite from the
production config dir.

Pinned here:
- the implicit fallback store counts in memory only: with its path
  pointed at a seeded "production" file, records through
  `record_usage` and the `with_analytics` wrapper — past the flush
  threshold, with the flush window forced open, plus explicit flush
  and reset calls — leave the file byte-identical and in place;
- `init_usage_metrics()` (the lifespan path) loads the file preserving
  `since` + counters, persists on flush, and replaces a memory-only
  fallback installed earlier;
- the suite itself runs against an isolated config dir
  (tests/conftest.py sets AGENTIC_WINDOWS_MCP_CONFIG_DIR), so a test
  process cannot even resolve the production stats path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentic_windows_mcp.infrastructure import config as config_module
from agentic_windows_mcp.infrastructure import usage_metrics
from agentic_windows_mcp.infrastructure.analytics import with_analytics
from agentic_windows_mcp.infrastructure.usage_metrics import UsageMetrics

SEED_SINCE = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _fresh_singleton(monkeypatch):
    monkeypatch.setattr(usage_metrics, "_metrics", None)


def _seed(path: Path) -> bytes:
    document = {
        "schema": 1,
        "since": SEED_SINCE,
        "tools": {
            "Click-Tool": {
                "calls": 7,
                "successes": 6,
                "errors": {"TIMEOUT": 1},
                "hist": [0] * 15,
            }
        },
    }
    raw = json.dumps(document, indent=1).encode("utf-8")
    path.write_bytes(raw)
    return raw


# ---------------------------------------------------------------------------
# The uninitialised fallback is memory-only
# ---------------------------------------------------------------------------


async def test_uninitialised_process_never_writes_the_stats_file(
    monkeypatch, tmp_path
):
    stats_path = tmp_path / "usage-stats.json"
    raw = _seed(stats_path)
    # Point the (lazily resolved) default path at the seeded
    # "production" file — exactly the configuration that destroyed the
    # live data on the PC.
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)

    @with_analytics(None, "test_tool")
    async def dummy_tool():
        return "ok"

    assert await dummy_tool() == "ok"
    assert await dummy_tool() == "ok"
    for _ in range(30):
        usage_metrics.record_usage("sync_tool", ok=True, duration_ms=3)

    store = usage_metrics.get_usage_metrics()
    assert store.persist is False
    # The fallback resolved the production-shaped path…
    assert store.path == stats_path

    # Force the flush window wide open and cross the threshold again,
    # then flush explicitly through every public path.
    store._last_flush = 0.0
    for _ in range(30):
        usage_metrics.record_usage(
            "async_tool", ok=False, duration_ms=3, error_code="STALE_REF"
        )
    usage_metrics.flush_usage_metrics()
    store.flush()

    # …counting still works in memory…
    assert store.summary()["totals"]["calls"] == 62
    # …but the file is byte-identical, and reset() must not unlink it.
    assert stats_path.read_bytes() == raw
    store.reset()
    assert stats_path.read_bytes() == raw
    assert not (tmp_path / "usage-stats.json.tmp").exists()


# ---------------------------------------------------------------------------
# The lifespan-initialised store still persists
# ---------------------------------------------------------------------------


def test_init_loads_persists_and_preserves_since(tmp_path):
    stats_path = tmp_path / "usage-stats.json"
    _seed(stats_path)

    store = usage_metrics.init_usage_metrics(path=stats_path)
    assert store.persist is True
    assert store.since == SEED_SINCE

    for _ in range(3):
        store.record("Click-Tool", ok=True, duration_ms=5)
    usage_metrics.flush_usage_metrics()

    loaded = UsageMetrics.load(stats_path)
    assert loaded.since == SEED_SINCE
    click = loaded.summary()["tools"]["Click-Tool"]
    assert click["calls"] == 10  # 7 seeded + 3 new
    assert click["successes"] == 9


def test_init_replaces_memory_only_fallback(tmp_path):
    usage_metrics.record_usage("test_tool", ok=True, duration_ms=1)
    fallback = usage_metrics.get_usage_metrics()
    assert fallback.persist is False

    stats_path = tmp_path / "usage-stats.json"
    store = usage_metrics.init_usage_metrics(path=stats_path)
    assert store is not fallback
    assert store.persist is True
    assert store.path == stats_path
    # A second init returns the same persisting store.
    assert usage_metrics.init_usage_metrics(path=stats_path) is store


# ---------------------------------------------------------------------------
# Suite-level isolation
# ---------------------------------------------------------------------------


def test_suite_runs_against_an_isolated_config_dir():
    override = os.environ.get("AGENTIC_WINDOWS_MCP_CONFIG_DIR")
    assert override, "tests/conftest.py must set AGENTIC_WINDOWS_MCP_CONFIG_DIR"
    assert config_module.CONFIG_DIR == Path(override)
    production = Path("~/.agentic-windows-mcp").expanduser()
    assert config_module.CONFIG_DIR != production
    assert (
        usage_metrics.default_stats_path()
        == Path(override) / usage_metrics.STATS_FILENAME
    )
