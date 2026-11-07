"""Issue #36: durable per-tool usage metrics.

Pinned here:
- aggregation correctness (calls / successes / errors by typed code /
  error rate / latency percentiles from the histogram);
- persistence round-trip (flush -> load keeps counters and `since`);
- corrupt or unknown-schema files start fresh and never raise;
- the no-content guarantee: the on-disk document contains only the
  schema keys — tool names, counts, error codes, histogram buckets —
  and nothing that could be argument or result content;
- the with_analytics hook feeds the store with typed error codes;
- ControlStatus exposes the `usage` block;
- the usage-reset CLI removes the stats file.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from agentic_windows_mcp import __main__ as cli
from agentic_windows_mcp.infrastructure import config as config_module
from agentic_windows_mcp.infrastructure import usage_metrics
from agentic_windows_mcp.infrastructure.analytics import with_analytics
from agentic_windows_mcp.infrastructure.usage_metrics import UsageMetrics
from agentic_windows_mcp.tools import control_status


@pytest.fixture
def store(tmp_path):
    return UsageMetrics(path=tmp_path / "usage-stats.json")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_aggregation_counts_and_error_codes(store):
    for _ in range(3):
        store.record("Click-Tool", ok=True, duration_ms=12)
    store.record("Click-Tool", ok=False, duration_ms=30, error_code="STALE_REF")
    store.record("Click-Tool", ok=False, duration_ms=30, error_code="STALE_REF")
    store.record("Click-Tool", ok=False, duration_ms=30, error_code="TIMEOUT")
    store.record("State-Tool", ok=True, duration_ms=3)

    summary = store.summary()
    click = summary["tools"]["Click-Tool"]
    assert click["calls"] == 6
    assert click["successes"] == 3
    assert click["errors"] == {"STALE_REF": 2, "TIMEOUT": 1}
    assert click["error_rate"] == 0.5
    assert summary["tools"]["State-Tool"]["calls"] == 1
    assert summary["totals"] == {"calls": 7, "errors": 3, "error_rate": round(3 / 7, 4)}


def test_latency_percentiles_from_histogram(store):
    for _ in range(10):
        store.record("Fast-Tool", ok=True, duration_ms=10)
    fast = store.summary()["tools"]["Fast-Tool"]["latency_ms"]
    assert fast["p50"] == 10
    assert fast["p90"] == 10

    for _ in range(4):
        store.record("Slow-Tool", ok=True, duration_ms=400_000)
    slow = store.summary()["tools"]["Slow-Tool"]["latency_ms"]
    # Overflow bucket reads back as the highest edge — an estimate,
    # never a fabricated exact value.
    assert slow["p50"] == 300_000
    assert slow["p90"] == 300_000


def test_summary_reports_window(store):
    summary = store.summary()
    assert summary["since"].endswith("Z")
    assert summary["window_seconds"] >= 0
    assert summary["totals"]["calls"] == 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "usage-stats.json"
    store = UsageMetrics(path=path, since=1_700_000_000.0)
    store.record("Click-Tool", ok=True, duration_ms=12)
    store.record("Click-Tool", ok=False, duration_ms=99, error_code="TIMEOUT")
    store.flush()
    assert path.exists()

    loaded = UsageMetrics.load(path)
    assert loaded.since == 1_700_000_000.0
    click = loaded.summary()["tools"]["Click-Tool"]
    assert click["calls"] == 2
    assert click["successes"] == 1
    assert click["errors"] == {"TIMEOUT": 1}


def test_missing_file_starts_fresh(tmp_path):
    loaded = UsageMetrics.load(tmp_path / "nope.json")
    assert loaded.summary()["totals"]["calls"] == 0


def test_corrupt_file_starts_fresh_without_raising(tmp_path):
    path = tmp_path / "usage-stats.json"
    path.write_text("{ this is not json", encoding="utf-8")
    loaded = UsageMetrics.load(path)
    assert loaded.summary()["totals"]["calls"] == 0


def test_unknown_schema_starts_fresh(tmp_path):
    path = tmp_path / "usage-stats.json"
    path.write_text(json.dumps({"schema": 999, "tools": {"X": {}}}), encoding="utf-8")
    loaded = UsageMetrics.load(path)
    assert loaded.summary()["totals"]["calls"] == 0


def test_no_content_guarantee(tmp_path):
    path = tmp_path / "usage-stats.json"
    store = UsageMetrics(path=path)
    store.record("Click-Tool", ok=True, duration_ms=12)
    store.record("Click-Tool", ok=False, duration_ms=12, error_code="STALE_REF")
    store.flush()

    text = path.read_text(encoding="utf-8")
    document = json.loads(text)
    # The document shape is closed: nothing else can be in there.
    assert set(document.keys()) == {"schema", "since", "tools"}
    for tool_stats in document["tools"].values():
        assert set(tool_stats.keys()) <= {"calls", "successes", "errors", "hist"}
    # Error entries are typed codes, never message text.
    assert document["tools"]["Click-Tool"]["errors"] == {"STALE_REF": 1}


def test_reset_clears_counters_and_file(store, tmp_path):
    store.record("Click-Tool", ok=True, duration_ms=5)
    store.flush()
    assert store.path.exists()
    store.reset()
    assert store.summary()["totals"]["calls"] == 0
    assert not store.path.exists()


# ---------------------------------------------------------------------------
# Wrapper hook + ControlStatus + CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def singleton(monkeypatch, tmp_path):
    metrics = UsageMetrics(path=tmp_path / "usage-stats.json")
    monkeypatch.setattr(usage_metrics, "_metrics", metrics)
    return metrics


async def test_with_analytics_hook_records_typed_errors(singleton):
    @with_analytics(None, "Fake-Tool")
    async def ok_tool():
        return "fine"

    @with_analytics(None, "Fake-Tool")
    async def bad_tool():
        raise ValueError("STALE_REF: ref @e1 no longer resolves")

    assert await ok_tool() == "fine"
    with pytest.raises(ValueError):
        await bad_tool()

    fake = singleton.summary()["tools"]["Fake-Tool"]
    assert fake["calls"] == 2
    assert fake["successes"] == 1
    assert fake["errors"] == {"STALE_REF": 1}


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *, name, **kwargs):
        def decorator(func):
            self.tools[name] = func
            return func

        return decorator


class _FakeController:
    def status(self):
        return {"state": "ready", "policy": "strict"}


def test_control_status_exposes_usage_block(monkeypatch, singleton):
    monkeypatch.setattr(control_status, "get_controller", lambda: _FakeController())
    singleton.record("Click-Tool", ok=True, duration_ms=7)
    mcp = _FakeMCP()
    control_status.register(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    result = mcp.tools["ControlStatus"]()
    assert result["state"] == "ready"
    usage = result["usage"]
    assert usage["tools"]["Click-Tool"]["calls"] == 1
    assert "since" in usage and "totals" in usage


def test_usage_reset_cli_removes_stats_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    stats = tmp_path / "usage-stats.json"
    stats.write_text('{"schema": 1, "since": 1.0, "tools": {}}', encoding="utf-8")
    result = CliRunner().invoke(cli.main, ["usage-reset"])
    assert result.exit_code == 0, result.output
    assert not stats.exists()
