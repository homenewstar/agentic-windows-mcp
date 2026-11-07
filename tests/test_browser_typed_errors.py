"""Issue #37: the Browser cluster's typed errors, discovery, and recovery.

Everything here runs on fakes — no real browser, no real Playwright
driver. What is pinned:

- D1: the five browser prefixes classify into the #13 taxonomy with the
  intended dispositions, the legacy CONSENT_PENDING alias still maps,
  Playwright's (non-builtin) TimeoutError classifies as TIMEOUT, and
  the tool layer appends the envelope to errors while leaving JSON
  success payloads byte-identical.
- D2: discovery probes before trusting a DevToolsActivePort file, falls
  through on refusal, and prefers the live webSocketDebuggerUrl over a
  stale recorded ws path; the failure report names every candidate.
- D3: a failed discovery is negative-cached (fail fast inside the TTL),
  expiry re-runs discovery, success clears the cache, consent-pending
  is never cached, and an explicit endpoint bypasses the cache.
- D4: click/fill/hover honour timeout_ms end to end, pre-check presence
  (ELEMENT_NOT_FOUND), and map action timeouts to
  ELEMENT_NOT_ACTIONABLE.
- D5: a driver-death error triggers exactly one reset + retry; a second
  death surfaces as DRIVER_DEAD; other errors never reset.
- D6: Browser_Interact's action parameter is a Literal of the ten
  supported actions, and timeout_ms reaches the manager.
"""

from __future__ import annotations

import inspect
import time
from typing import get_args

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from agentic_windows_mcp.browser import devtools_manager as dm
from agentic_windows_mcp.browser.devtools_manager import BrowserDevToolsManager
from agentic_windows_mcp.browser.errors import (
    ConsentPendingError,
    DriverDeadError,
    ElementNotActionableError,
    ElementNotFoundError,
    NotDebuggableError,
    is_driver_death,
)
from agentic_windows_mcp.infrastructure.errors import classify_error, enveloped
from agentic_windows_mcp.tools import browser_devtools


# ---------------------------------------------------------------------------
# D1 — taxonomy mapping
# ---------------------------------------------------------------------------


def test_not_debuggable_maps_to_manual_retry():
    code, disposition, _ = classify_error(NotDebuggableError("nothing answered"))
    assert code == "BROWSER_NOT_DEBUGGABLE"
    assert disposition.delivery == "not_delivered"
    assert disposition.retry == "manual"


def test_consent_pending_maps_with_recovery():
    code, disposition, recovery = classify_error(ConsentPendingError("dialog up"))
    assert code == "BROWSER_CONSENT_PENDING"
    assert disposition.delivery == "not_delivered"
    assert recovery == "ask_user_to_allow_remote_debugging_then_retry"


def test_legacy_consent_pending_alias_still_maps():
    code, _, _ = classify_error(RuntimeError("CONSENT_PENDING: dialog up"))
    assert code == "BROWSER_CONSENT_PENDING"


def test_element_errors_map_to_not_delivered():
    for exc in (ElementNotFoundError("nope"), ElementNotActionableError("stuck")):
        code, disposition, recovery = classify_error(exc)
        assert code in ("ELEMENT_NOT_FOUND", "ELEMENT_NOT_ACTIONABLE")
        assert disposition.delivery == "not_delivered"
        assert recovery == "refresh_snapshot_then_retry_original"


def test_driver_dead_maps_to_delivery_uncertain():
    code, disposition, _ = classify_error(DriverDeadError("gone"))
    assert code == "DRIVER_DEAD"
    assert disposition.delivery == "delivery_uncertain"
    assert disposition.retry == "unsafe"


class TimeoutError(Exception):
    """Stands in for Playwright's TimeoutError: same class name, not the
    builtin — classify_error must catch it by duck-typing."""


def test_foreign_timeout_error_classifies_as_timeout():
    code, _, _ = classify_error(TimeoutError("Locator.wait_for: Timeout"))
    assert code == "TIMEOUT"


def test_enveloped_keeps_single_prefix():
    exc = enveloped(NotDebuggableError("no endpoint answered"))
    text = str(exc)
    assert text.count("BROWSER_NOT_DEBUGGABLE:") == 1
    assert "code=BROWSER_NOT_DEBUGGABLE" in text
    assert "retry=manual" in text


def test_is_driver_death_markers():
    assert is_driver_death(RuntimeError("Connection closed while reading from the driver"))
    assert is_driver_death(RuntimeError("The driver has been closed"))
    assert not is_driver_death(RuntimeError("ECONNREFUSED 127.0.0.1:9222"))


# ---------------------------------------------------------------------------
# D2 — discovery probe + fall-through
# ---------------------------------------------------------------------------


def _write_port_file(tmp_path, name: str, port: int, ws_path: str) -> str:
    path = tmp_path / name
    path.write_text(f"{port}\n{ws_path}\n", encoding="utf-8")
    return str(path)


def _single_candidate(monkeypatch, tmp_path, port=9333, ws_path="/devtools/browser/abc"):
    path = _write_port_file(tmp_path, "DevToolsActivePort", port, ws_path)
    monkeypatch.setattr(dm, "DEVTOOLS_PORT_FILES", (("FakeBrowser", path),))
    return port


async def test_discovery_refusal_falls_through_to_fallback(monkeypatch, tmp_path):
    port = _single_candidate(monkeypatch, tmp_path)

    def probe(p):
        if p == port:
            return ("refused", None)
        return ("ok", {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/live"})

    endpoint, report = await dm.discover_user_browser_endpoint(probe=probe)
    assert endpoint == dm.FALLBACK_CDP_HTTP
    assert any("refused" in line for line in report)
    assert any("fallback" in line for line in report)


async def test_discovery_prefers_live_ws_url_over_stale_file_path(monkeypatch, tmp_path):
    port = _single_candidate(monkeypatch, tmp_path, ws_path="/devtools/browser/STALE")

    def probe(p):
        return ("ok", {"webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/LIVE"})

    endpoint, report = await dm.discover_user_browser_endpoint(probe=probe)
    assert endpoint == "ws://127.0.0.1:9333/devtools/browser/LIVE"
    assert any("stale" in line for line in report)


async def test_discovery_all_fail_reports_every_candidate(monkeypatch, tmp_path):
    port = _single_candidate(monkeypatch, tmp_path)

    def probe(p):
        return ("refused", None)

    endpoint, report = await dm.discover_user_browser_endpoint(probe=probe)
    assert endpoint is None
    joined = " ".join(report)
    assert "FakeBrowser" in joined and "refused" in joined and "fallback" in joined


async def test_discovery_missing_file_is_reported(monkeypatch, tmp_path):
    missing = str(tmp_path / "DevToolsActivePort")
    monkeypatch.setattr(dm, "DEVTOOLS_PORT_FILES", (("Ghost", missing),))

    def probe(p):
        return ("refused", None)

    endpoint, report = await dm.discover_user_browser_endpoint(probe=probe)
    assert endpoint is None
    assert any("no DevToolsActivePort file" in line for line in report)


# ---------------------------------------------------------------------------
# Manager scaffolding for D3/D5 (bypass __init__: no worker thread, no UIA)
# ---------------------------------------------------------------------------


def _bare_manager() -> BrowserDevToolsManager:
    m = BrowserDevToolsManager.__new__(BrowserDevToolsManager)
    m.playwright = object()  # non-None: _init_playwright becomes a no-op
    m.browser = None
    m.context = None
    m.pages = []
    m.active_page_index = 0
    m.connected_endpoint = None
    m._cdp_sessions = {}
    m._monitored_page_ids = set()
    m._neg_cache_at = 0.0
    m._neg_cache_detail = None
    return m


# ---------------------------------------------------------------------------
# D3 — negative cache
# ---------------------------------------------------------------------------


async def test_negative_cache_fails_fast_then_expires():
    m = _bare_manager()
    calls = {"discover": 0}

    async def fake_discover():
        calls["discover"] += 1
        return None, ["FakeBrowser: probe outcome was refused"]

    m._discover_endpoint = fake_discover

    with pytest.raises(NotDebuggableError) as first:
        await m._connect_to_user_browser_internal()
    with pytest.raises(NotDebuggableError) as second:
        await m._connect_to_user_browser_internal()
    assert calls["discover"] == 1  # second call served from the cache
    assert str(first.value) == str(second.value)
    assert "probe outcome was refused" in str(second.value)

    # After the TTL the discovery runs again.
    m._neg_cache_at = time.monotonic() - (dm.DISCOVERY_NEGATIVE_CACHE_TTL_SECONDS + 1)
    with pytest.raises(NotDebuggableError):
        await m._connect_to_user_browser_internal()
    assert calls["discover"] == 2


async def test_successful_connect_clears_negative_cache():
    m = _bare_manager()
    m._neg_cache_detail = "BROWSER_NOT_DEBUGGABLE: earlier failure"
    m._neg_cache_at = time.monotonic() - (dm.DISCOVERY_NEGATIVE_CACHE_TTL_SECONDS + 1)

    async def fake_discover():
        return "ws://127.0.0.1:9333/devtools/browser/x", ["ok"]

    async def fake_attach(endpoint, report):
        return {"status": "connected", "endpoint": endpoint}

    m._discover_endpoint = fake_discover
    m._attach = fake_attach
    result = await m._connect_to_user_browser_internal()
    assert result["status"] == "connected"
    assert m._neg_cache_detail is None


async def test_consent_pending_is_not_cached():
    m = _bare_manager()

    async def fake_discover():
        return "ws://127.0.0.1:9333/devtools/browser/x", ["ok"]

    async def fake_attach(endpoint, report):
        raise ConsentPendingError("dialog is up")

    m._discover_endpoint = fake_discover
    m._attach = fake_attach
    with pytest.raises(ConsentPendingError):
        await m._connect_to_user_browser_internal()
    assert m._neg_cache_detail is None


async def test_attach_refusal_is_cached_but_explicit_endpoint_bypasses():
    m = _bare_manager()
    calls = {"attach": 0}

    async def fake_discover():
        return "ws://127.0.0.1:9333/devtools/browser/x", ["ok"]

    async def fake_attach(endpoint, report):
        calls["attach"] += 1
        raise NotDebuggableError("endpoint refused the attach")

    m._discover_endpoint = fake_discover
    m._attach = fake_attach
    with pytest.raises(NotDebuggableError):
        await m._connect_to_user_browser_internal()
    with pytest.raises(NotDebuggableError):
        await m._connect_to_user_browser_internal()
    assert calls["attach"] == 1  # second auto-connect hit the cache

    # An explicit endpoint ignores the cache entirely.
    async def ok_attach(endpoint, report):
        return {"status": "connected", "endpoint": endpoint}

    m._attach = ok_attach
    result = await m._connect_to_user_browser_internal("ws://explicit")
    assert result["endpoint"] == "ws://explicit"


# ---------------------------------------------------------------------------
# D4 — interact timeouts with fake page/locator
# ---------------------------------------------------------------------------


class _FakeLocator:
    def __init__(self, page: "_FakePage"):
        self._page = page
        self.first = self

    async def wait_for(self, state=None, timeout=None):
        self._page.calls.append(("wait_for", state, timeout))
        if self._page.missing:
            raise PlaywrightTimeoutError("locator not attached")

    async def click(self, **kwargs):
        self._page.calls.append(("click", kwargs))
        if self._page.unactionable:
            raise PlaywrightTimeoutError("locator not actionable")

    async def fill(self, value, **kwargs):
        self._page.calls.append(("fill", value, kwargs))
        if self._page.unactionable:
            raise PlaywrightTimeoutError("locator not editable")

    async def hover(self, **kwargs):
        self._page.calls.append(("hover", kwargs))
        if self._page.unactionable:
            raise PlaywrightTimeoutError("locator not hoverable")


class _FakePage:
    def __init__(self, missing=False, unactionable=False):
        self.missing = missing
        self.unactionable = unactionable
        self.calls = []

    def locator(self, selector):
        return _FakeLocator(self)


def _manager_with_page(page) -> BrowserDevToolsManager:
    m = _bare_manager()

    async def fake_get_page(page_id=None):
        return page

    m._get_page_by_id_or_active = fake_get_page
    return m


async def test_click_propagates_timeout_and_succeeds():
    page = _FakePage()
    m = _manager_with_page(page)
    result = await m._click_internal("#btn", timeout_ms=4321)
    assert result["status"] == "success"
    assert ("wait_for", "attached", 4321) in page.calls
    click_kwargs = next(c[1] for c in page.calls if c[0] == "click")
    assert click_kwargs["timeout"] == 4321


async def test_missing_element_is_element_not_found():
    page = _FakePage(missing=True)
    m = _manager_with_page(page)
    with pytest.raises(ElementNotFoundError) as excinfo:
        await m._click_internal("#ghost", timeout_ms=100)
    assert str(excinfo.value).startswith("ELEMENT_NOT_FOUND:")
    code, disposition, _ = classify_error(excinfo.value)
    assert code == "ELEMENT_NOT_FOUND"
    assert disposition.delivery == "not_delivered"


async def test_unactionable_element_is_element_not_actionable():
    page = _FakePage(unactionable=True)
    m = _manager_with_page(page)
    with pytest.raises(ElementNotActionableError):
        await m._fill_internal("#in", "text", timeout_ms=100)
    with pytest.raises(ElementNotActionableError):
        await m._hover_internal("#in", timeout_ms=100)


# ---------------------------------------------------------------------------
# D5 — driver reset-once
# ---------------------------------------------------------------------------


class _FakeDriver:
    def __init__(self):
        self.stop_calls = 0

    async def stop(self):
        self.stop_calls += 1


def _driver_manager() -> BrowserDevToolsManager:
    m = _bare_manager()
    m.playwright = _FakeDriver()
    m._run_in_worker = lambda coro: coro  # run coroutines inline
    return m


async def test_driver_death_resets_once_and_retries():
    m = _driver_manager()
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("Connection closed while reading from the driver")
        return "recovered"

    result = await m._invoke(flaky)
    assert result == "recovered"
    assert attempts["n"] == 2
    assert m.playwright is None  # reset dropped the dead driver


async def test_second_driver_death_surfaces_driver_dead():
    m = _driver_manager()

    async def always_dead():
        raise RuntimeError("Connection closed while reading from the driver")

    with pytest.raises(DriverDeadError) as excinfo:
        await m._invoke(always_dead)
    assert str(excinfo.value).startswith("DRIVER_DEAD:")


async def test_ordinary_error_never_resets():
    m = _driver_manager()

    async def broken():
        raise ValueError("some ordinary failure")

    with pytest.raises(ValueError):
        await m._invoke(broken)
    assert isinstance(m.playwright, _FakeDriver)  # untouched


# ---------------------------------------------------------------------------
# Tool layer — envelope on errors, JSON untouched on success, D6 Literal
# ---------------------------------------------------------------------------


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *, name, **kwargs):
        def decorator(func):
            self.tools[name] = func
            return func

        return decorator


class _FakeManager:
    def __init__(self):
        self.click_kwargs = None

    async def click(self, **kwargs):
        self.click_kwargs = kwargs
        return {"status": "success"}

    async def connect_or_launch(self, cdp_url=None):
        raise NotDebuggableError("no endpoint answered. Discovery: fallback refused")


def _register_tools(monkeypatch, fake_manager):
    monkeypatch.setattr(
        BrowserDevToolsManager, "get_instance", classmethod(lambda cls: fake_manager)
    )
    mcp = _FakeMCP()
    browser_devtools.register(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    return mcp.tools


async def test_tool_error_carries_typed_envelope(monkeypatch):
    tools = _register_tools(monkeypatch, _FakeManager())
    with pytest.raises(NotDebuggableError) as excinfo:
        await tools["Browser_Connect"]()
    text = str(excinfo.value)
    assert "code=BROWSER_NOT_DEBUGGABLE" in text
    assert "retry=manual" in text


async def test_tool_success_payload_is_untouched_json(monkeypatch):
    fake = _FakeManager()
    tools = _register_tools(monkeypatch, fake)
    out = await tools["Browser_Interact"](action="click", selector="#a", timeout_ms=4321)
    assert "disposition" not in out  # JSON payload not suffixed
    assert fake.click_kwargs["timeout_ms"] == 4321


def test_interact_action_is_a_literal(monkeypatch):
    tools = _register_tools(monkeypatch, _FakeManager())
    annotation = inspect.signature(tools["Browser_Interact"]).parameters["action"].annotation
    values = get_args(annotation)
    assert "press_key" in values and "click" in values and len(values) == 10
