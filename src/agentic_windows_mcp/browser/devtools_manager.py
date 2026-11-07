from __future__ import annotations

import asyncio
import http.client
import logging
import os
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import json as _json
from bs4 import BeautifulSoup
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Request,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from agentic_windows_mcp.browser.consent import (
    ConsentDialogMonitor,
    detect_consent_dialog,
    should_retry_attach,
)
from agentic_windows_mcp.browser.errors import (
    ConsentPendingError,
    DriverDeadError,
    ElementNotActionableError,
    ElementNotFoundError,
    NotDebuggableError,
    is_driver_death,
)

logger = logging.getLogger(__name__)

MAX_BUFFER_SIZE = 500

# A renderer blocked by a native dialog (e.g. a file picker) never answers
# CDP Runtime.evaluate; without a deadline one stuck tab wedges every later
# browser call behind it, so evaluates stay bounded.
EVALUATE_TIMEOUT_SECONDS = 30.0
VISIBILITY_TIMEOUT_SECONDS = 2.0

# CDP deep-inspection limits (raw DevTools domains, beyond Playwright's API).
MAX_BODY_CHARS_DEFAULT = 20000
MAX_WS_FRAMES_PER_REQUEST = 50
MAX_WS_PAYLOAD_CHARS = 2000
MAX_INDEXEDDB_RECORDS_PER_STORE = 20
MAX_STORAGE_VALUE_CHARS = 4000


class CdpNetworkTracker:
    """Pure-data tracker fed by CDP domain events for a single page.

    Playwright's own events only expose request/response metadata, so the
    deep DevTools data (request payloads, per-phase timing, transfer sizes,
    redirect chains, failures, WebSocket frames, parsed scripts and
    stylesheets) is collected here from raw CDP events. Kept free of any
    Playwright objects so it can be unit-tested with synthetic events.
    """

    def __init__(self) -> None:
        self.requests: Dict[str, Dict[str, Any]] = {}
        self.order: List[str] = []
        self.scripts: Dict[str, Dict[str, Any]] = {}
        self.stylesheets: Dict[str, Dict[str, Any]] = {}

    def _entry(self, request_id: str) -> Dict[str, Any]:
        entry = self.requests.get(request_id)
        if entry is None:
            entry = {"cdp_request_id": request_id}
            self.requests[request_id] = entry
            self.order.append(request_id)
            if len(self.order) > MAX_BUFFER_SIZE:
                self.requests.pop(self.order.pop(0), None)
        return entry

    def on_request_will_be_sent(self, params: Dict[str, Any]) -> None:
        entry = self._entry(str(params.get("requestId", "")))
        request = params.get("request", {})
        # This event fires once per redirect hop for the same requestId:
        # the URL/method advance to the current hop, but the payload and
        # initiator belong to the first hop and must not be overwritten
        # by later hops that omit them.
        if entry.get("first_url") is None:
            entry["first_url"] = request.get("url")
        entry["url"] = request.get("url")
        entry["method"] = request.get("method")
        if request.get("headers"):
            entry["request_headers"] = request["headers"]
        if request.get("postData") is not None:
            entry["post_data"] = request.get("postData")
            entry["has_post_data"] = request.get("hasPostData")
        if params.get("type") is not None:
            entry["resource_type"] = params["type"]
        if params.get("initiator") is not None:
            entry["initiator"] = params["initiator"]
        if params.get("wallTime") is not None:
            entry["wall_time"] = params["wallTime"]
        if params.get("frameId") is not None:
            entry["frame_id"] = params["frameId"]
        redirect = params.get("redirectResponse")
        if redirect:
            entry.setdefault("redirect_chain", []).append(
                {
                    "url": redirect.get("url"),
                    "status": redirect.get("status"),
                    "status_text": redirect.get("statusText"),
                }
            )

    def on_response_received(self, params: Dict[str, Any]) -> None:
        entry = self._entry(str(params.get("requestId", "")))
        response = params.get("response", {})
        entry.update(
            {
                "url": response.get("url", entry.get("url")),
                "status": response.get("status"),
                "status_text": response.get("statusText"),
                "response_headers": response.get("headers", {}),
                "mime_type": response.get("mimeType"),
                "remote_ip": response.get("remoteIPAddress"),
                "remote_port": response.get("remotePort"),
                "protocol": response.get("protocol"),
                "timing": response.get("timing"),
                "from_disk_cache": response.get("fromDiskCache"),
                "from_service_worker": response.get("fromServiceWorker"),
                "security_state": response.get("securityState"),
                "security_details": response.get("securityDetails"),
            }
        )

    def on_loading_finished(self, params: Dict[str, Any]) -> None:
        entry = self._entry(str(params.get("requestId", "")))
        entry["encoded_data_length"] = params.get("encodedDataLength")
        entry["finished"] = True

    def on_loading_failed(self, params: Dict[str, Any]) -> None:
        entry = self._entry(str(params.get("requestId", "")))
        entry["failed"] = True
        entry["error_text"] = params.get("errorText")
        entry["canceled"] = params.get("canceled")
        entry["blocked_reason"] = params.get("blockedReason")

    def on_websocket_created(self, params: Dict[str, Any]) -> None:
        entry = self._entry(str(params.get("requestId", "")))
        entry["is_websocket"] = True
        entry["url"] = params.get("url", entry.get("url"))
        entry.setdefault("websocket_frames", [])

    def on_websocket_frame(self, params: Dict[str, Any], *, sent: bool) -> None:
        entry = self._entry(str(params.get("requestId", "")))
        entry["is_websocket"] = True
        frames = entry.setdefault("websocket_frames", [])
        entry["websocket_frame_count"] = entry.get("websocket_frame_count", 0) + 1
        if len(frames) < MAX_WS_FRAMES_PER_REQUEST:
            response = params.get("response", {})
            frames.append(
                {
                    "direction": "sent" if sent else "received",
                    "opcode": response.get("opcode"),
                    "payload": (response.get("payloadData") or "")[:MAX_WS_PAYLOAD_CHARS],
                }
            )

    def on_script_parsed(self, params: Dict[str, Any]) -> None:
        script_id = params.get("scriptId")
        if script_id is None:
            return
        self.scripts[str(script_id)] = {
            "script_id": str(script_id),
            "url": params.get("url"),
            "start_line": params.get("startLine"),
            "end_line": params.get("endLine"),
            "length": params.get("length"),
            "hash": params.get("hash"),
            "source_map_url": params.get("sourceMapURL"),
            "execution_context_id": params.get("executionContextId"),
        }

    def on_stylesheet_added(self, params: Dict[str, Any]) -> None:
        header = params.get("header", {})
        stylesheet_id = header.get("styleSheetId")
        if stylesheet_id is None:
            return
        self.stylesheets[str(stylesheet_id)] = {
            "stylesheet_id": str(stylesheet_id),
            "source_url": header.get("sourceURL"),
            "origin": header.get("origin"),
            "title": header.get("title"),
            "disabled": header.get("disabled"),
        }

    def find_by_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self.requests.get(request_id)

    def find_by_url(self, needle: str) -> Optional[Dict[str, Any]]:
        lowered = needle.lower()
        for request_id in reversed(self.order):
            entry = self.requests[request_id]
            if entry.get("url") and lowered in entry["url"].lower():
                return entry
        return None

    def find_by_exact_url(self, url: str) -> Optional[Dict[str, Any]]:
        for request_id in reversed(self.order):
            entry = self.requests[request_id]
            if entry.get("url") == url:
                return entry
        return None


# ---------------------------------------------------------------------------
# CDP endpoint discovery (#37, defects D2/D3)
#
# A DevToolsActivePort file is only a *hint*: it outlives the browser that
# wrote it, and the ws path it names dies with that browser session (a stale
# GUID was trusted and refused on the old test PC). Discovery therefore
# probes every candidate with a short /json/version request before trusting
# it, prefers the LIVE webSocketDebuggerUrl the probe returns over the
# file's recorded path, and falls through to the next candidate on any
# refusal. The report lines say what discovery actually found, so the
# BROWSER_NOT_DEBUGGABLE error can distinguish "no browser", "browser
# without debugging" and "stale file" instead of failing blind.
DEVTOOLS_PORT_FILES: Tuple[Tuple[str, str], ...] = (
    ("Chrome", r"%LOCALAPPDATA%\Google\Chrome\User Data\DevToolsActivePort"),
    ("Edge", r"%LOCALAPPDATA%\Microsoft\Edge\User Data\DevToolsActivePort"),
    ("Brave", r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data\DevToolsActivePort"),
    ("Chrome SxS", r"%LOCALAPPDATA%\Google\Chrome SxS\User Data\DevToolsActivePort"),
)
FALLBACK_CDP_HTTP = "http://127.0.0.1:9222"
FALLBACK_CDP_PORT = 9222
PROBE_TIMEOUT_SECONDS = 1.5
# A failed discovery is remembered for this long: repeated Browser_* calls
# inside the window fail fast with the same typed error instead of paying
# a full attach attempt each (the census recorded 14 identical refusals
# across 3 tools in 2 days).
DISCOVERY_NEGATIVE_CACHE_TTL_SECONDS = 45.0
DEFAULT_INTERACT_TIMEOUT_MS = 10_000


def _read_devtools_port_file(path: str) -> Optional[Tuple[int, str]]:
    """Parse a DevToolsActivePort file into (port, ws_path), or None."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            port = f.readline().strip()
            ws_path = f.readline().strip()
        if port and ws_path:
            return int(port), ws_path
    except Exception as e:
        logger.debug("Failed reading %s: %s", path, e)
    return None


def probe_devtools_version(
    port: int, timeout: float = PROBE_TIMEOUT_SECONDS
) -> Tuple[str, Optional[dict]]:
    """Probe ``GET /json/version`` on 127.0.0.1:<port> (sync; call via to_thread).

    Returns (outcome, payload): outcome is "ok", "refused", "timeout",
    "invalid-json", "http-<status>" or "error-<type>". A live DevTools
    HTTP endpoint answers on the same port the ws endpoint lives on, so
    one probe validates both reachability and freshness.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/json/version")
        resp = conn.getresponse()
        if resp.status != 200:
            return (f"http-{resp.status}", None)
        try:
            payload = _json.loads(resp.read().decode("utf-8", "replace"))
        except ValueError:
            return ("invalid-json", None)
        return ("ok", payload if isinstance(payload, dict) else {})
    except ConnectionRefusedError:
        return ("refused", None)
    except TimeoutError:
        return ("timeout", None)
    except OSError as e:
        return (f"error-{type(e).__name__}", None)
    finally:
        conn.close()


async def discover_user_browser_endpoint(
    probe: Callable[..., Tuple[str, Optional[dict]]] = probe_devtools_version,
) -> Tuple[Optional[str], List[str]]:
    """Probe the discovery candidates in order.

    Returns (endpoint, report): the first endpoint whose probe answered,
    or (None, report) when nothing did. The report has one human line
    per candidate tried — it is attached verbatim to the
    BROWSER_NOT_DEBUGGABLE error so the failure is diagnosable.
    """
    report: List[str] = []
    for name, raw_path in DEVTOOLS_PORT_FILES:
        path = os.path.expandvars(raw_path)
        parsed = _read_devtools_port_file(path) if os.path.isfile(path) else None
        if parsed is None:
            report.append(f"{name}: no DevToolsActivePort file")
            continue
        port, ws_path = parsed
        outcome, payload = await asyncio.to_thread(probe, port)
        if outcome == "ok":
            file_endpoint = f"ws://127.0.0.1:{port}{ws_path}"
            live_endpoint = (payload or {}).get("webSocketDebuggerUrl")
            if live_endpoint and live_endpoint != file_endpoint:
                report.append(
                    f"{name}: port {port} answered /json/version; the file's "
                    "ws path was stale, using the live endpoint"
                )
                return live_endpoint, report
            report.append(f"{name}: port {port} answered /json/version")
            return file_endpoint, report
        report.append(
            f"{name}: DevToolsActivePort names port {port} but the probe "
            f"outcome was {outcome} (stale file, or debugging is off)"
        )
    outcome, _payload = await asyncio.to_thread(probe, FALLBACK_CDP_PORT)
    if outcome == "ok":
        report.append(f"fallback {FALLBACK_CDP_HTTP} answered /json/version")
        return FALLBACK_CDP_HTTP, report
    report.append(f"fallback {FALLBACK_CDP_HTTP} probe outcome was {outcome}")
    return None, report


def resolve_user_browser_endpoint() -> Optional[str]:
    """Best *unprobed* candidate: first port-file endpoint, else the 9222
    fallback. Kept for compatibility — the connect path uses
    discover_user_browser_endpoint(), which probes before trusting (#37)."""
    for _name, raw_path in DEVTOOLS_PORT_FILES:
        path = os.path.expandvars(raw_path)
        if os.path.isfile(path):
            parsed = _read_devtools_port_file(path)
            if parsed is not None:
                port, ws_path = parsed
                endpoint = f"ws://127.0.0.1:{port}{ws_path}"
                logger.info("Found active browser DevTools endpoint from %s: %s", path, endpoint)
                return endpoint
    return FALLBACK_CDP_HTTP


def find_system_browser_executable() -> Optional[str]:
    """Find the user's primary installed browser executable (Chrome or Edge)."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


class BrowserDevToolsManager:
    """Manages direct CDP connection to the user's currently active desktop browser on an isolated Proactor thread."""

    _instance: Optional[BrowserDevToolsManager] = None

    def __init__(self) -> None:
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.pages: List[Page] = []
        self.active_page_index: int = 0
        self.connected_endpoint: Optional[str] = None

        # #37 (D3): negative cache for a failed discovery — the detail
        # text of the last BROWSER_NOT_DEBUGGABLE failure and when it
        # happened. Repeat auto-connects inside the TTL re-raise it
        # immediately instead of re-running discovery + attach.
        self._neg_cache_at: float = 0.0
        self._neg_cache_detail: Optional[str] = None

        # Diagnostic ring buffers
        self.console_logs: List[Dict[str, Any]] = []
        self.network_requests: List[Dict[str, Any]] = []
        self.network_details: Dict[str, Dict[str, Any]] = {}
        self._monitored_page_ids: set[int] = set()

        # Raw CDP sessions per page: id(page) -> (page, session, tracker).
        self._cdp_sessions: Dict[int, Any] = {}

        # Dedicated worker loop running ProactorEventLoop
        self._worker_loop: Optional[asyncio.AbstractEventLoop] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._start_worker_thread()

        # While Chrome's remote-debugging consent dialog is up, CDP calls
        # stall -- and input suppression would stop the user from clicking
        # Allow without a takeover chord. The monitor detects the dialog
        # and hands input back to the user until it closes; approving the
        # dialog stays the user's own click.
        self._consent_monitor = ConsentDialogMonitor()
        self._consent_monitor.start()

    def _start_worker_thread(self) -> None:
        def runner() -> None:
            loop = asyncio.WindowsProactorEventLoopPolicy().new_event_loop()
            asyncio.set_event_loop(loop)
            self._worker_loop = loop
            self._ready_event.set()
            loop.run_forever()

        self._worker_thread = threading.Thread(target=runner, daemon=True, name="PlaywrightUserBrowserThread")
        self._worker_thread.start()
        self._ready_event.wait()

    def _run_in_worker(self, coro: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._worker_loop)
        return asyncio.wrap_future(future)

    @classmethod
    def get_instance(cls) -> BrowserDevToolsManager:
        if cls._instance is None:
            cls._instance = BrowserDevToolsManager()
        return cls._instance

    async def _init_playwright(self) -> None:
        if self.playwright is None:
            self.playwright = await async_playwright().start()

    async def _reset_driver_internal(self) -> None:
        """Drop every driver reference so _init_playwright restarts it.

        Runs on the worker loop. The old driver is stopped best-effort:
        it may already be half-dead, and stop() itself can hang — bound
        it and swallow it either way.
        """
        old = self.playwright
        self.playwright = None
        self.browser = None
        self.context = None
        self.pages = []
        self.active_page_index = 0
        self.connected_endpoint = None
        self._cdp_sessions = {}
        self._monitored_page_ids = set()
        if old is not None:
            try:
                await asyncio.wait_for(old.stop(), timeout=5.0)
            except Exception as exc:
                logger.warning("stopping the dead driver raised %r; continuing reset", exc)

    async def _invoke(self, factory: Callable[[], Any]) -> Any:
        """Run one public operation with a single driver reset on death (#37 D5).

        *factory* is a zero-arg callable returning the coroutine to run,
        so the retry gets a fresh coroutine. A driver-death error resets
        the manager and retries exactly once; a second death surfaces as
        the typed DRIVER_DEAD. Any other error propagates untouched.
        """
        try:
            return await self._run_in_worker(factory())
        except Exception as exc:
            if not is_driver_death(exc):
                raise
            logger.warning("Playwright driver died (%s); resetting once", exc)
        try:
            await self._run_in_worker(self._reset_driver_internal())
            return await self._run_in_worker(factory())
        except Exception as exc:
            if is_driver_death(exc):
                raise DriverDeadError(
                    "the Playwright driver died and the automatic reset "
                    f"did not revive it ({exc})"
                ) from exc
            raise

    def _attach_page_listeners(self, page: Page) -> None:
        pid = id(page)
        if pid in self._monitored_page_ids:
            return
        self._monitored_page_ids.add(pid)

        def handle_console(msg: Any) -> None:
            entry = {
                "timestamp": time.time(),
                "time_str": time.strftime("%H:%M:%S"),
                "type": msg.type,
                "text": msg.text,
                "location": msg.location,
            }
            self.console_logs.append(entry)
            if len(self.console_logs) > MAX_BUFFER_SIZE:
                self.console_logs.pop(0)

        def handle_pageerror(err: Any) -> None:
            entry = {
                "timestamp": time.time(),
                "time_str": time.strftime("%H:%M:%S"),
                "type": "error",
                "text": f"Uncaught Exception: {err}",
                "location": {},
            }
            self.console_logs.append(entry)
            if len(self.console_logs) > MAX_BUFFER_SIZE:
                self.console_logs.pop(0)

        def handle_request(req: Request) -> None:
            req_id = f"req_{int(time.time() * 1000)}_{len(self.network_requests)}"
            entry = {
                "id": req_id,
                "timestamp": time.time(),
                "time_str": time.strftime("%H:%M:%S"),
                "method": req.method,
                "url": req.url,
                "resource_type": req.resource_type,
                "status": None,
                "status_text": None,
                "duration_ms": None,
                "headers": dict(req.headers) if hasattr(req, "headers") else {},
                "_start_time": time.perf_counter(),
            }
            self.network_requests.append(entry)
            self.network_details[req_id] = entry
            if len(self.network_requests) > MAX_BUFFER_SIZE:
                old = self.network_requests.pop(0)
                self.network_details.pop(old["id"], None)

        def handle_response(res: Response) -> None:
            for entry in reversed(self.network_requests):
                if entry["url"] == res.url and entry["status"] is None:
                    entry["status"] = res.status
                    entry["status_text"] = res.status_text
                    entry["duration_ms"] = round((time.perf_counter() - entry.get("_start_time", 0)) * 1000, 2)
                    entry["response_headers"] = dict(res.headers) if hasattr(res, "headers") else {}
                    break

        page.on("console", handle_console)
        page.on("pageerror", handle_pageerror)
        page.on("request", handle_request)
        page.on("response", handle_response)

        # Deep DevTools data needs raw CDP domains; set the session up on
        # the worker loop without blocking listener attachment.
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is not None:
            running_loop.create_task(self._setup_cdp_session(page))

    async def _setup_cdp_session(self, page: Page) -> None:
        """Open a raw CDP session for a page and enable the deep domains."""
        pid = id(page)
        existing = self._cdp_sessions.get(pid)
        if existing is not None and existing[0] is page:
            return
        try:
            session = await page.context.new_cdp_session(page)
        except Exception as exc:
            logger.debug("CDP session unavailable for page: %s", exc)
            return
        tracker = CdpNetworkTracker()
        self._cdp_sessions[pid] = (page, session, tracker)
        session.on("Network.requestWillBeSent", tracker.on_request_will_be_sent)
        session.on("Network.responseReceived", tracker.on_response_received)
        session.on("Network.loadingFinished", tracker.on_loading_finished)
        session.on("Network.loadingFailed", tracker.on_loading_failed)
        session.on("Network.webSocketCreated", tracker.on_websocket_created)
        session.on(
            "Network.webSocketFrameSent",
            lambda params: tracker.on_websocket_frame(params, sent=True),
        )
        session.on(
            "Network.webSocketFrameReceived",
            lambda params: tracker.on_websocket_frame(params, sent=False),
        )
        session.on("Debugger.scriptParsed", tracker.on_script_parsed)
        session.on("CSS.styleSheetAdded", tracker.on_stylesheet_added)
        for method in (
            "Network.enable",
            "Debugger.enable",
            "CSS.enable",
            "DOM.enable",
            "Accessibility.enable",
            "Runtime.enable",
        ):
            try:
                await session.send(method)
            except Exception as exc:
                logger.debug("CDP %s failed: %s", method, exc)

    async def _get_cdp(self, page: Page):
        """Return (session, tracker) for a page, setting them up if needed."""
        pid = id(page)
        existing = self._cdp_sessions.get(pid)
        if existing is None or existing[0] is not page:
            await self._setup_cdp_session(page)
            existing = self._cdp_sessions.get(pid)
        if existing is None:
            raise RuntimeError("CDP session unavailable for this page")
        return existing[1], existing[2]

    async def _ensure_domains(self, session: Any, *methods: str) -> None:
        """Best-effort (re-)enable of CDP domains right before use.

        Domain enables sent once at session setup can be lost (session
        re-creation, attach timing), and CSS/DOM queries fail with
        'agent was not enabled' when they are. Re-enabling is idempotent.
        """
        for method in methods:
            try:
                await session.send(method)
            except Exception:
                pass

    def _negative_cache_hit(self) -> Optional[str]:
        """The cached not-debuggable detail, while it is still fresh (#37 D3)."""
        if self._neg_cache_detail is None:
            return None
        if time.monotonic() - self._neg_cache_at < DISCOVERY_NEGATIVE_CACHE_TTL_SECONDS:
            return self._neg_cache_detail
        return None

    def _remember_discovery_failure(self, detail: str) -> None:
        self._neg_cache_at = time.monotonic()
        self._neg_cache_detail = detail

    async def _discover_endpoint(self) -> Tuple[Optional[str], List[str]]:
        """Discovery seam (module-level probe + report); tests replace it."""
        return await discover_user_browser_endpoint()

    async def _connect_to_user_browser_internal(self, custom_endpoint: Optional[str] = None) -> Dict[str, Any]:
        await self._init_playwright()

        if self.browser and self.browser.is_connected() and self.context:
            await self._refresh_pages()
            return {
                "status": "connected",
                "mode": "user_active_browser",
                "endpoint": self.connected_endpoint,
                "open_tabs_count": len(self.pages),
                "active_tab": await self._get_active_tab_summary(),
            }

        if custom_endpoint:
            # The caller named the endpoint: discovery and its negative
            # cache do not apply.
            return await self._attach(custom_endpoint, ["explicit endpoint (discovery skipped)"])

        cached = self._negative_cache_hit()
        if cached is not None:
            raise NotDebuggableError(cached)

        endpoint, report = await self._discover_endpoint()
        if endpoint is None:
            detail = (
                "no debuggable browser endpoint answered. Discovery: "
                + "; ".join(report)
                + ". Remote debugging must be enabled in the user's browser "
                "(chrome://inspect/#remote-debugging); no browser was launched "
                "(attach-only by design)."
            )
            self._remember_discovery_failure(detail)
            raise NotDebuggableError(detail)

        try:
            result = await self._attach(endpoint, report)
        except NotDebuggableError as exc:
            self._remember_discovery_failure(exc.detail)
            raise
        self._neg_cache_at = 0.0
        self._neg_cache_detail = None
        return result

    async def _attach(self, endpoint: str, report: List[str]) -> Dict[str, Any]:
        logger.info("Connecting to user browser at %s", endpoint)
        # While Chrome's remote-debugging consent dialog is on screen the
        # handshake stalls until the user clicks Allow (the dialog monitor
        # releases the input for them, held 10s per sighting). Retry up to
        # 3 attempts while the dialog explains the stall; if the user
        # still has not approved, surface BROWSER_CONSENT_PENDING so the
        # agent can ask them in chat and wait for their confirmation there.
        saw_dialog = False
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                self.browser = await asyncio.wait_for(
                    self.playwright.chromium.connect_over_cdp(endpoint),
                    timeout=30.0,
                )
                self.connected_endpoint = endpoint
                last_error = None
                break
            except Exception as e:
                if is_driver_death(e):
                    # Not an attach refusal: the driver itself is gone.
                    # Let the caller's reset-once recovery (#37 D5) see
                    # it instead of masking it as not-debuggable.
                    raise
                last_error = e
                await asyncio.sleep(2)
                present = detect_consent_dialog()
                saw_dialog = saw_dialog or present
                if not should_retry_attach(attempt, 3, present):
                    break
                logger.info(
                    "Consent dialog is up (attach attempt %d/3); "
                    "waiting for the user to approve it",
                    attempt,
                )
                await asyncio.sleep(3)
        if last_error is not None:
            if saw_dialog:
                raise ConsentPendingError(
                    "Chrome's remote-debugging consent dialog is on screen "
                    "and the user has not approved it after 3 attach "
                    "attempts. Ask the user to click 'Allow' on the "
                    "dialog, then retry."
                ) from last_error
            # Owner's rule: never launch a browser -- only attach to the
            # one the user already has open. Report and stop instead.
            raise NotDebuggableError(
                f"the discovered endpoint {endpoint} refused the CDP "
                f"attach ({last_error}). Discovery: "
                + "; ".join(report)
                + ". Remote debugging must be enabled in that browser "
                "instance (chrome://inspect/#remote-debugging); no "
                "browser was launched."
            ) from last_error

        contexts = self.browser.contexts
        if contexts:
            self.context = contexts[0]
        else:
            raise RuntimeError("Connected to user browser but no active browser context was found.")

        await self._refresh_pages()
        return {
            "status": "connected",
            "mode": "user_active_browser",
            "endpoint": self.connected_endpoint,
            "open_tabs_count": len(self.pages),
            "active_tab": await self._get_active_tab_summary(),
        }

    async def _refresh_pages(self) -> None:
        """Syncs open pages directly with the user's active browser tabs."""
        if not self.context:
            return
        current_pages = [p for p in self.context.pages if not p.is_closed()]
        self.pages = current_pages

        for p in self.pages:
            self._attach_page_listeners(p)

        # Detect which tab the user is actively viewing on screen (document.visibilityState == 'visible')
        visible_idx = None
        for i, p in enumerate(self.pages):
            try:
                vis = await asyncio.wait_for(
                    p.evaluate("document.visibilityState"),
                    timeout=VISIBILITY_TIMEOUT_SECONDS,
                )
                if vis == "visible":
                    visible_idx = i
                    break
            except Exception:
                pass

        if visible_idx is not None:
            self.active_page_index = visible_idx
        elif self.active_page_index >= len(self.pages):
            self.active_page_index = max(0, len(self.pages) - 1)

    async def _get_active_tab_summary(self) -> Dict[str, Any]:
        if not self.pages or self.active_page_index >= len(self.pages):
            return {"title": "None", "url": "None"}
        p = self.pages[self.active_page_index]
        try:
            return {
                "page_id": self.active_page_index,
                "title": await p.title(),
                "url": p.url,
            }
        except Exception:
            return {"page_id": self.active_page_index, "url": getattr(p, "url", "unknown")}

    async def _get_page_by_id_or_active(self, page_id: Optional[int] = None) -> Page:
        if not self.browser or not self.browser.is_connected() or not self.pages:
            await self._connect_to_user_browser_internal()

        await self._refresh_pages()
        if not self.pages:
            page = await self.context.new_page()
            self._attach_page_listeners(page)
            self.pages = [page]
            self.active_page_index = 0
            return page

        if page_id is not None:
            if 0 <= page_id < len(self.pages) and not self.pages[page_id].is_closed():
                return self.pages[page_id]
            raise ValueError(f"Invalid or closed page_id: {page_id}. Available tabs: 0 to {len(self.pages) - 1}")

        return self.pages[self.active_page_index]

    async def _safe_evaluate(self, page: Page, script: str, arg: Any = None) -> Any:
        async def _eval() -> Any:
            if arg is not None:
                return await page.evaluate(script, arg)
            return await page.evaluate(script)

        try:
            return await asyncio.wait_for(_eval(), timeout=EVALUATE_TIMEOUT_SECONDS)
        except Exception as e:
            if "Execution context was destroyed" in str(e) or "Navigating frame was detached" in str(e):
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(0.1)
                return await asyncio.wait_for(_eval(), timeout=EVALUATE_TIMEOUT_SECONDS)
            raise

    async def _navigate_internal(self, url: str, wait_until: str, timeout_ms: int, page_id: Optional[int] = None) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        res = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        return {
            "page_id": self.pages.index(page) if page in self.pages else self.active_page_index,
            "url": page.url,
            "title": await page.title(),
            "status": res.status if res else 200,
            "target": "user_active_browser_tab",
        }

    async def _get_page_snapshot_internal(self, selector: Optional[str], format_type: str, max_length: int, page_id: Optional[int] = None) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        if selector:
            element = await page.wait_for_selector(selector, state="attached", timeout=5000)
            if not element:
                raise ValueError(f"Element not found for selector: {selector}")
            raw_html = await element.inner_html()
        else:
            raw_html = await page.content()

        if format_type == "text":
            soup = BeautifulSoup(raw_html, "html.parser")
            text = soup.get_text(separator="\n", strip=True)
            return {"url": page.url, "title": await page.title(), "format": "text", "content": text[:max_length]}

        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src.startswith("data:image"):
                img["src"] = "[base64 image data omitted]"

        for svg in soup.find_all("svg"):
            svg.string = "[svg icon]"

        clean_str = soup.prettify()
        truncated = len(clean_str) > max_length
        return {
            "url": page.url,
            "title": await page.title(),
            "format": "clean_html",
            "content": clean_str[:max_length],
            "is_truncated": truncated,
            "total_length": len(clean_str),
        }

    async def _get_css_styles_internal(self, selector: str, properties: Optional[List[str]], page_id: Optional[int] = None) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        await page.wait_for_selector(selector, state="attached", timeout=5000)
        default_props = [
            "display", "position", "flex-direction", "grid-template-columns",
            "width", "height", "margin", "padding", "border",
            "color", "background-color", "font-family", "font-size", "font-weight",
            "z-index", "visibility", "opacity", "box-sizing", "overflow",
        ]
        target_props = properties if properties else default_props

        script = """
        ({ selector, props }) => {
            const el = document.querySelector(selector);
            if (!el) return null;
            const computed = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            const styles = {};
            for (const prop of props) {
                styles[prop] = computed.getPropertyValue(prop);
            }
            return {
                tagName: el.tagName.toLowerCase(),
                id: el.id,
                className: el.className,
                boundingBox: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    top: Math.round(rect.top),
                    right: Math.round(rect.right),
                    bottom: Math.round(rect.bottom),
                    left: Math.round(rect.left),
                },
                computedStyles: styles,
                isVisible: (rect.width > 0 && rect.height > 0 && computed.visibility !== 'hidden' && computed.display !== 'none'),
            };
        }
        """
        result = await self._safe_evaluate(page, script, {"selector": selector, "props": target_props})
        if not result:
            raise ElementNotFoundError(f"no element matching selector '{selector}'")
        return result

    async def _get_advanced_computed_styles_internal(
        self,
        selector: str,
        include_pseudo: bool = True,
        include_animations: bool = True,
        page_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Deep dissection: gets element styles, ::before, ::after pseudo-elements, and exports CSS snippet."""
        page = await self._get_page_by_id_or_active(page_id)
        await page.wait_for_selector(selector, state="attached", timeout=5000)

        script = """
        ({ selector, includePseudo, includeAnimations }) => {
            const el = document.querySelector(selector);
            if (!el) return null;

            const extractKeyStyles = (computed) => {
                const props = [
                    'display', 'position', 'top', 'right', 'bottom', 'left',
                    'flex-direction', 'justify-content', 'align-items', 'gap',
                    'grid-template-columns', 'grid-template-rows',
                    'width', 'min-width', 'max-width', 'height', 'min-height', 'max-height',
                    'box-sizing', 'margin', 'padding', 'border', 'border-radius',
                    'background', 'background-color', 'color',
                    'font-family', 'font-size', 'font-weight', 'line-height', 'letter-spacing', 'text-align',
                    'box-shadow', 'opacity', 'visibility', 'z-index', 'overflow',
                    'transform', 'transition', 'animation'
                ];
                const res = {};
                for (const p of props) {
                    const val = computed.getPropertyValue(p);
                    if (val && val !== 'none' && val !== 'normal' && val !== 'auto' && val !== 'rgba(0, 0, 0, 0)') {
                        res[p] = val;
                    }
                }
                return res;
            };

            const mainComputed = window.getComputedStyle(el);
            const mainStyles = extractKeyStyles(mainComputed);
            const rect = el.getBoundingClientRect();

            const result = {
                selector: selector,
                tagName: el.tagName.toLowerCase(),
                id: el.id || null,
                className: el.className || null,
                boundingBox: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                },
                computedStyles: mainStyles,
                pseudoElements: {},
                animations: {},
            };

            if (includePseudo) {
                for (const pseudo of ['::before', '::after']) {
                    const pComputed = window.getComputedStyle(el, pseudo);
                    const content = pComputed.getPropertyValue('content');
                    if (content && content !== 'none' && content !== 'normal' && content !== '""') {
                        result.pseudoElements[pseudo] = {
                            content: content,
                            styles: extractKeyStyles(pComputed),
                        };
                    }
                }
            }

            if (includeAnimations) {
                const animName = mainComputed.getPropertyValue('animation-name');
                const transition = mainComputed.getPropertyValue('transition');
                if (animName && animName !== 'none') {
                    result.animations['animation'] = {
                        name: animName,
                        duration: mainComputed.getPropertyValue('animation-duration'),
                        timingFunction: mainComputed.getPropertyValue('animation-timing-function'),
                        delay: mainComputed.getPropertyValue('animation-delay'),
                        iterationCount: mainComputed.getPropertyValue('animation-iteration-count'),
                    };
                }
                if (transition && transition !== 'none' && transition !== 'all 0s ease 0s') {
                    result.animations['transition'] = transition;
                }
            }

            // Generate clean CSS Snippet
            let cssSnippet = `${selector} {\\n`;
            for (const [k, v] of Object.entries(mainStyles)) {
                cssSnippet += `  ${k}: ${v};\\n`;
            }
            cssSnippet += `}\\n`;

            for (const [pseudo, data] of Object.entries(result.pseudoElements)) {
                cssSnippet += `\\n${selector}${pseudo} {\\n  content: ${data.content};\\n`;
                for (const [k, v] of Object.entries(data.styles)) {
                    if (k !== 'content') cssSnippet += `  ${k}: ${v};\\n`;
                }
                cssSnippet += `}\\n`;
            }
            result.cssSnippet = cssSnippet;

            return result;
        }
        """
        result = await self._safe_evaluate(page, script, {
            "selector": selector,
            "includePseudo": include_pseudo,
            "includeAnimations": include_animations,
        })
        if not result:
            raise ElementNotFoundError(f"no element matching selector '{selector}'")
        return result

    async def _highlight_element_internal(
        self,
        selector: str,
        color: str = "#ef4444",
        duration_ms: int = 5000,
        scroll_into_view: bool = True,
        page_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Highlights an element on the user's screen with a vibrant border for visual debugging."""
        page = await self._get_page_by_id_or_active(page_id)
        await page.wait_for_selector(selector, state="attached", timeout=5000)

        script = """
        ({ selector, color, durationMs, scrollIntoView }) => {
            const el = document.querySelector(selector);
            if (!el) return null;

            if (scrollIntoView) {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }

            const originalOutline = el.style.outline;
            const originalBoxShadow = el.style.boxShadow;
            const originalTransition = el.style.transition;

            el.style.transition = 'outline 0.2s ease, box-shadow 0.2s ease';
            el.style.outline = `3px solid ${color}`;
            el.style.boxShadow = `0 0 12px ${color}`;

            if (durationMs > 0) {
                setTimeout(() => {
                    el.style.outline = originalOutline;
                    el.style.boxShadow = originalBoxShadow;
                    el.style.transition = originalTransition;
                }, durationMs);
            }

            const rect = el.getBoundingClientRect();
            return {
                status: 'highlighted',
                selector: selector,
                color: color,
                duration_ms: durationMs,
                boundingBox: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                },
                isVisible: (rect.width > 0 && rect.height > 0),
            };
        }
        """
        result = await self._safe_evaluate(page, script, {
            "selector": selector,
            "color": color,
            "durationMs": duration_ms,
            "scrollIntoView": scroll_into_view,
        })
        if not result:
            raise ElementNotFoundError(f"no element matching selector '{selector}'")
        return result

    async def _evaluate_script_internal(self, script: str, arg: Any = None, page_id: Optional[int] = None) -> Any:
        page = await self._get_page_by_id_or_active(page_id)
        return await self._safe_evaluate(page, script, arg)

    async def _query_elements_internal(self, selector: str, attributes: Optional[List[str]], limit: int, page_id: Optional[int] = None) -> List[Dict[str, Any]]:
        page = await self._get_page_by_id_or_active(page_id)
        await page.wait_for_selector(selector, state="attached", timeout=5000)
        attr_list = attributes or ["id", "class", "href", "src", "type", "value", "name", "role", "aria-label"]
        script = """
        ({ selector, attrList, limit }) => {
            const els = Array.from(document.querySelectorAll(selector)).slice(0, limit);
            return els.map((el, index) => {
                const rect = el.getBoundingClientRect();
                const attrs = {};
                for (const a of attrList) {
                    if (el.hasAttribute(a)) {
                        attrs[a] = el.getAttribute(a);
                    }
                }
                return {
                    index,
                    tagName: el.tagName.toLowerCase(),
                    text: el.innerText ? el.innerText.trim().slice(0, 200) : '',
                    attributes: attrs,
                    boundingBox: {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                    },
                    isVisible: (rect.width > 0 && rect.height > 0),
                };
            });
        }
        """
        return await self._safe_evaluate(page, script, {"selector": selector, "attrList": attr_list, "limit": limit})

    async def _await_attached(self, page: Page, selector: str, timeout_ms: int):
        """Bounded presence pre-check (#37 D4).

        Returns the first matching locator once it is attached to the
        DOM; a locator that never appears within timeout_ms raises the
        typed ELEMENT_NOT_FOUND instead of surfacing Playwright's bare
        30s timeout.
        """
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="attached", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ElementNotFoundError(
                f"no element matching selector '{selector}' appeared "
                f"within {timeout_ms}ms"
            ) from exc
        return locator

    async def _click_internal(self, selector: str, button: str = "left", click_count: int = 1, page_id: Optional[int] = None, timeout_ms: int = DEFAULT_INTERACT_TIMEOUT_MS) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        locator = await self._await_attached(page, selector, timeout_ms)
        try:
            await locator.click(button=button, click_count=click_count, timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ElementNotActionableError(
                f"element matching selector '{selector}' was attached but "
                f"not clickable within {timeout_ms}ms"
            ) from exc
        return {
            "action": "click",
            "button": button,
            "click_count": click_count,
            "selector": selector,
            "status": "success",
            "target": "user_active_browser_tab",
        }

    async def _fill_internal(self, selector: str, value: str, page_id: Optional[int] = None, timeout_ms: int = DEFAULT_INTERACT_TIMEOUT_MS) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        locator = await self._await_attached(page, selector, timeout_ms)
        try:
            await locator.fill(value, timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ElementNotActionableError(
                f"element matching selector '{selector}' was attached but "
                f"not fillable within {timeout_ms}ms"
            ) from exc
        return {"action": "fill", "selector": selector, "value_length": len(value), "status": "success"}

    async def _hover_internal(self, selector: str, delay_ms: int = 0, page_id: Optional[int] = None, timeout_ms: int = DEFAULT_INTERACT_TIMEOUT_MS) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        locator = await self._await_attached(page, selector, timeout_ms)
        try:
            await locator.hover(timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ElementNotActionableError(
                f"element matching selector '{selector}' was attached but "
                f"not hoverable within {timeout_ms}ms"
            ) from exc
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)
        return {"action": "hover", "selector": selector, "waited_ms": delay_ms, "status": "success"}

    async def _resolve_point(self, page, selector: Optional[str], start_x: Optional[float], start_y: Optional[float], waypoints: Optional[List] = None):
        """Resolve a start point: element center, explicit coords, or first waypoint."""
        if selector:
            box = await page.locator(selector).first.bounding_box()
            if not box:
                raise ValueError(f"Element not found or has no bounding box: {selector}")
            return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        if start_x is not None and start_y is not None:
            return float(start_x), float(start_y)
        if waypoints:
            return float(waypoints[0][0]), float(waypoints[0][1])
        raise ValueError("A selector, start_x/start_y, or waypoints is required")

    async def _drag_internal(self, selector: Optional[str] = None, target_x: Optional[float] = None, target_y: Optional[float] = None, steps: int = 20, step_delay_ms: int = 0, hold_ms: int = 0, page_id: Optional[int] = None, waypoints: Optional[List] = None, start_x: Optional[float] = None, start_y: Optional[float] = None) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        sx, sy = await self._resolve_point(page, selector, start_x, start_y, waypoints)
        # Points to visit while the button stays held down. 'waypoints' is a
        # list of [x, y] absolute page coords visited in order (multi-segment
        # path: swipes, zig-zag scratching, drawing). Without waypoints the
        # drag is a single straight segment to target_x/target_y.
        if waypoints:
            pts = [(float(p[0]), float(p[1])) for p in waypoints]
        elif target_x is not None and target_y is not None:
            pts = [(float(target_x), float(target_y))]
        else:
            pts = []
        await page.mouse.move(sx, sy)
        await page.mouse.down()
        try:
            if hold_ms > 0:
                await asyncio.sleep(hold_ms / 1000.0)
            # Move in manual steps with real time gaps so rAF-driven drag
            # handlers (panel resize, sliders, scratch cards) observe
            # intermediate positions. A single instant move completes before
            # any animation frame runs.
            cx, cy = sx, sy
            for tx, ty in pts:
                for i in range(1, steps + 1):
                    x = cx + (tx - cx) * i / steps
                    y = cy + (ty - cy) * i / steps
                    await page.mouse.move(x, y)
                    if step_delay_ms > 0:
                        await asyncio.sleep(step_delay_ms / 1000.0)
                cx, cy = tx, ty
        finally:
            await page.mouse.up()
        return {
            "action": "drag",
            "selector": selector,
            "from": {"x": sx, "y": sy},
            "waypoints": [{"x": x, "y": y} for x, y in pts],
            "steps_per_segment": steps,
            "step_delay_ms": step_delay_ms,
            "hold_ms": hold_ms,
            "status": "success",
            "target": "user_active_browser_tab",
        }

    async def _hold_internal(self, selector: Optional[str] = None, hold_ms: int = 1000, page_id: Optional[int] = None, start_x: Optional[float] = None, start_y: Optional[float] = None) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        x, y = await self._resolve_point(page, selector, start_x, start_y)
        await page.mouse.move(x, y)
        await page.mouse.down()
        try:
            await asyncio.sleep(max(hold_ms, 0) / 1000.0)
        finally:
            await page.mouse.up()
        return {
            "action": "hold",
            "selector": selector,
            "at": {"x": x, "y": y},
            "held_ms": hold_ms,
            "status": "success",
            "target": "user_active_browser_tab",
        }

    async def _press_key_internal(self, key: str, page_id: Optional[int] = None) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        await page.keyboard.press(key)
        return {"action": "press_key", "key": key, "status": "success"}

    async def _wait_for_internal(self, selector: Optional[str], state: str, timeout_ms: int, page_id: Optional[int] = None) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        if selector:
            await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
            return {"waited_for": "selector", "selector": selector, "state": state, "status": "resolved"}
        else:
            await page.wait_for_load_state(state if state in ["load", "domcontentloaded", "networkidle"] else "load", timeout=timeout_ms)
            return {"waited_for": "load_state", "state": state, "status": "resolved"}

    async def _take_screenshot_internal(self, selector: Optional[str], full_page: bool, path: Optional[str], page_id: Optional[int] = None) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        if selector:
            el = await page.wait_for_selector(selector, state="attached", timeout=5000)
            if not el:
                raise ValueError(f"Element '{selector}' not found.")
            data = await el.screenshot(path=path)
        else:
            data = await page.screenshot(full_page=full_page, path=path)

        import base64
        b64 = base64.b64encode(data).decode("ascii")
        return {
            "format": "png",
            "path": path,
            "size_bytes": len(data),
            "base64_preview": f"data:image/png;base64,{b64[:100]}...",
            "base64": b64,
        }

    async def _list_pages_internal(self) -> List[Dict[str, Any]]:
        await self._refresh_pages()
        page_info = []
        for i, p in enumerate(self.pages):
            if not p.is_closed():
                try:
                    title = await p.title()
                except Exception:
                    title = "Unknown"
                page_info.append({
                    "page_id": i,
                    "title": title,
                    "url": p.url,
                    "is_active": (i == self.active_page_index),
                })
        return page_info

    async def _select_page_internal(self, page_id: int) -> Dict[str, Any]:
        await self._refresh_pages()
        if 0 <= page_id < len(self.pages) and not self.pages[page_id].is_closed():
            self.active_page_index = page_id
            page = self.pages[page_id]
            await page.bring_to_front()
            return {"page_id": page_id, "url": page.url, "title": await page.title(), "status": "selected"}
        raise ValueError(f"Invalid or closed page_id: {page_id}")

    async def _new_page_internal(self, url: str) -> Dict[str, Any]:
        await self._get_page_by_id_or_active()
        page = await self.context.new_page()
        self._attach_page_listeners(page)
        self.pages.append(page)
        self.active_page_index = len(self.pages) - 1
        if url and url != "about:blank":
            await page.goto(url, wait_until="domcontentloaded")
        return {"page_id": self.active_page_index, "url": page.url, "title": await page.title(), "status": "created_in_user_browser"}

    async def _close_page_internal(self, page_id: Optional[int]) -> Dict[str, Any]:
        await self._refresh_pages()
        target_idx = self.active_page_index if page_id is None else page_id
        if 0 <= target_idx < len(self.pages):
            page = self.pages[target_idx]
            if not page.is_closed():
                await page.close()
            self.pages.pop(target_idx)
            self.active_page_index = max(0, min(self.active_page_index, len(self.pages) - 1))
            return {"closed_index": target_idx, "remaining_pages": len(self.pages)}
        raise ValueError(f"Invalid page_id: {target_idx}")

    async def _close_internal(self) -> None:
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception as exc:
                # A half-dead driver must not make close() itself fail.
                logger.warning("stopping the driver during close raised %r; ignored", exc)
            self.playwright = None
            self.browser = None
            self.context = None
            self.pages.clear()

    # Public asynchronous API (bridged to Proactor worker). Every entry
    # point routes through _invoke so a dead Playwright driver is reset
    # and retried once per incident (#37 D5); close() is the exception —
    # it talks to the possibly-dead driver directly and tolerates it.
    async def connect_or_launch(self, cdp_url: Optional[str] = None, headless: bool = False, browser_type: str = "chromium") -> Dict[str, Any]:
        return await self._invoke(lambda: self._connect_to_user_browser_internal(cdp_url))

    async def navigate(self, url: str, wait_until: str = "load", timeout_ms: int = 30000, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._navigate_internal(url, wait_until, timeout_ms, page_id))

    async def get_page_snapshot(self, selector: Optional[str] = None, format_type: str = "clean_html", max_length: int = 15000, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._get_page_snapshot_internal(selector, format_type, max_length, page_id))

    async def get_css_styles(self, selector: str, properties: Optional[List[str]] = None, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._get_css_styles_internal(selector, properties, page_id))

    async def get_advanced_computed_styles(self, selector: str, include_pseudo: bool = True, include_animations: bool = True, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._get_advanced_computed_styles_internal(selector, include_pseudo, include_animations, page_id))

    async def highlight_element(self, selector: str, color: str = "#ef4444", duration_ms: int = 5000, scroll_into_view: bool = True, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._highlight_element_internal(selector, color, duration_ms, scroll_into_view, page_id))

    async def evaluate_script(self, script: str, arg: Any = None, page_id: Optional[int] = None) -> Any:
        return await self._invoke(lambda: self._evaluate_script_internal(script, arg, page_id))

    async def query_elements(self, selector: str, attributes: Optional[List[str]] = None, limit: int = 50, page_id: Optional[int] = None) -> List[Dict[str, Any]]:
        return await self._invoke(lambda: self._query_elements_internal(selector, attributes, limit, page_id))

    async def click(self, selector: str, button: str = "left", click_count: int = 1, page_id: Optional[int] = None, timeout_ms: int = DEFAULT_INTERACT_TIMEOUT_MS) -> Dict[str, Any]:
        return await self._invoke(lambda: self._click_internal(selector, button, click_count, page_id, timeout_ms))

    async def fill(self, selector: str, value: str, page_id: Optional[int] = None, timeout_ms: int = DEFAULT_INTERACT_TIMEOUT_MS) -> Dict[str, Any]:
        return await self._invoke(lambda: self._fill_internal(selector, value, page_id, timeout_ms))

    async def hover(self, selector: str, delay_ms: int = 0, page_id: Optional[int] = None, timeout_ms: int = DEFAULT_INTERACT_TIMEOUT_MS) -> Dict[str, Any]:
        return await self._invoke(lambda: self._hover_internal(selector, delay_ms, page_id, timeout_ms))

    async def drag(self, selector: Optional[str] = None, target_x: Optional[float] = None, target_y: Optional[float] = None, steps: int = 20, step_delay_ms: int = 0, hold_ms: int = 0, page_id: Optional[int] = None, waypoints: Optional[List] = None, start_x: Optional[float] = None, start_y: Optional[float] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._drag_internal(selector=selector, target_x=target_x, target_y=target_y, steps=steps, step_delay_ms=step_delay_ms, hold_ms=hold_ms, page_id=page_id, waypoints=waypoints, start_x=start_x, start_y=start_y))

    async def hold(self, selector: Optional[str] = None, hold_ms: int = 1000, page_id: Optional[int] = None, start_x: Optional[float] = None, start_y: Optional[float] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._hold_internal(selector=selector, hold_ms=hold_ms, page_id=page_id, start_x=start_x, start_y=start_y))

    async def press_key(self, key: str, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._press_key_internal(key, page_id))

    async def wait_for(self, selector: Optional[str] = None, state: str = "visible", timeout_ms: int = 10000, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._wait_for_internal(selector, state, timeout_ms, page_id))

    async def take_screenshot(self, selector: Optional[str] = None, full_page: bool = False, path: Optional[str] = None, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._take_screenshot_internal(selector, full_page, path, page_id))

    async def list_pages(self) -> List[Dict[str, Any]]:
        return await self._invoke(lambda: self._list_pages_internal())

    async def select_page(self, page_id: int) -> Dict[str, Any]:
        return await self._invoke(lambda: self._select_page_internal(page_id))

    async def new_page(self, url: str = "about:blank") -> Dict[str, Any]:
        return await self._invoke(lambda: self._new_page_internal(url))

    async def close_page(self, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._invoke(lambda: self._close_page_internal(page_id))

    async def close(self) -> None:
        return await self._run_in_worker(self._close_internal())

    def list_console_logs(self, level: Optional[str] = None, limit: int = 50, clear: bool = False) -> List[Dict[str, Any]]:
        logs = self.console_logs
        if level:
            logs = [entry for entry in logs if entry.get("type", "").lower() == level.lower()]
        result = logs[-limit:]
        if clear:
            self.console_logs.clear()
        return result

    def list_network_requests(
        self,
        filter_url: Optional[str] = None,
        resource_type: Optional[str] = None,
        status_code: Optional[int] = None,
        limit: int = 50,
        clear: bool = False,
    ) -> List[Dict[str, Any]]:
        reqs = self.network_requests
        if filter_url:
            reqs = [r for r in reqs if filter_url.lower() in r["url"].lower()]
        if resource_type:
            reqs = [r for r in reqs if (r.get("resource_type") or "").lower() == resource_type.lower()]
        if status_code is not None:
            reqs = [r for r in reqs if r.get("status") == status_code]

        clean_results = []
        for r in reqs[-limit:]:
            clean_results.append({
                "id": r["id"],
                "time": r.get("time_str"),
                "method": r["method"],
                "url": r["url"][:120],
                "resource_type": r.get("resource_type"),
                "status": r.get("status"),
                "duration_ms": r.get("duration_ms"),
            })

        if clear:
            self.network_requests.clear()
            self.network_details.clear()
        return clean_results

    def get_network_details(self, request_id_or_url: str) -> Optional[Dict[str, Any]]:
        if request_id_or_url in self.network_details:
            return self.network_details[request_id_or_url]
        for r in reversed(self.network_requests):
            if request_id_or_url.lower() in r["url"].lower():
                return r
        return None

    # ------------------------------------------------------------------
    # Deep DevTools inspection via raw CDP domains
    # ------------------------------------------------------------------

    async def _get_network_details_deep_internal(
        self,
        request_id_or_url: str,
        include_body: bool,
        max_body_chars: int,
        page_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        page = await self._get_page_by_id_or_active(page_id)
        base = self.get_network_details(request_id_or_url)
        session = None
        tracker = None
        try:
            session, tracker = await self._get_cdp(page)
        except Exception:
            session, tracker = None, None
        cdp_entry = None
        if tracker is not None:
            cdp_entry = tracker.find_by_id(request_id_or_url)
            # Prefer an exact URL match: a bare origin needle is a prefix of
            # every subresource URL on that origin, so plain substring search
            # would return the latest script instead of the document.
            if cdp_entry is None and base and base.get("url"):
                cdp_entry = tracker.find_by_exact_url(base["url"])
            if cdp_entry is None:
                cdp_entry = tracker.find_by_url(request_id_or_url)
            if cdp_entry is None and base and base.get("url"):
                cdp_entry = tracker.find_by_url(base["url"])
        if base is None and cdp_entry is None:
            return None
        result: Dict[str, Any] = {}
        if base:
            result.update({k: v for k, v in base.items() if k != "_start_time"})
        if cdp_entry:
            for key, value in cdp_entry.items():
                if value is not None:
                    result[key] = value
        if include_body and cdp_entry and session is not None and not cdp_entry.get("failed"):
            try:
                body_result = await session.send(
                    "Network.getResponseBody",
                    {"requestId": cdp_entry["cdp_request_id"]},
                )
                body = body_result.get("body", "")
                result["response_body"] = body[:max_body_chars]
                result["response_body_length"] = len(body)
                result["response_body_truncated"] = len(body) > max_body_chars
                result["response_body_base64_encoded"] = bool(
                    body_result.get("base64Encoded")
                )
            except Exception as exc:
                result["response_body_error"] = str(exc)[:300]
        return result

    async def _get_storage_internal(
        self,
        types: Optional[List[str]],
        page_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        wanted = set(
            types
            or [
                "cookies",
                "local_storage",
                "session_storage",
                "indexeddb",
                "cache_storage",
                "service_workers",
            ]
        )
        result: Dict[str, Any] = {"url": page.url}

        if "cookies" in wanted:
            try:
                host = (urlparse(page.url).hostname or "").lower()
                cookies = []
                for cookie in await self.context.cookies():
                    domain = (cookie.get("domain") or "").lstrip(".").lower()
                    if not host or domain == host or host.endswith("." + domain):
                        cookies.append(cookie)
                result["cookies"] = cookies
                result["cookies_count"] = len(cookies)
            except Exception as exc:
                result["cookies_error"] = str(exc)[:300]

        if "local_storage" in wanted or "session_storage" in wanted:
            try:
                dumped = await self._safe_evaluate(
                    page,
                    """
                    () => {
                        const dump = (store) => {
                            const out = {};
                            for (let i = 0; i < store.length; i++) {
                                const key = store.key(i);
                                const value = String(store.getItem(key));
                                out[key] = value.length > 4000 ? value.slice(0, 4000) + "…[truncated]" : value;
                            }
                            return out;
                        };
                        return { local: dump(window.localStorage), session: dump(window.sessionStorage) };
                    }
                    """,
                )
                if "local_storage" in wanted:
                    result["local_storage"] = dumped.get("local", {})
                if "session_storage" in wanted:
                    result["session_storage"] = dumped.get("session", {})
            except Exception as exc:
                result["web_storage_error"] = str(exc)[:300]

        if "indexeddb" in wanted:
            try:
                result["indexeddb"] = await self._safe_evaluate(
                    page,
                    """
                    async () => {
                        if (!window.indexedDB || !indexedDB.databases) {
                            return { error: "indexedDB.databases() unavailable in this browser" };
                        }
                        const infos = await indexedDB.databases();
                        const databases = [];
                        for (const info of infos) {
                            const entry = { name: info.name, version: info.version, stores: [] };
                            try {
                                const db = await new Promise((resolve, reject) => {
                                    const req = indexedDB.open(info.name);
                                    req.onsuccess = () => resolve(req.result);
                                    req.onerror = () => reject(req.error);
                                });
                                for (const storeName of Array.from(db.objectStoreNames)) {
                                    const storeData = { name: storeName, count: null, records: [] };
                                    try {
                                        const tx = db.transaction(storeName, "readonly");
                                        const store = tx.objectStore(storeName);
                                        storeData.count = await new Promise((resolve) => {
                                            const c = store.count();
                                            c.onsuccess = () => resolve(c.result);
                                            c.onerror = () => resolve(null);
                                        });
                                        storeData.records = await new Promise((resolve) => {
                                            const items = [];
                                            const cursorReq = store.openCursor();
                                            cursorReq.onsuccess = () => {
                                                const cursor = cursorReq.result;
                                                if (cursor && items.length < 20) {
                                                    let serialized = null;
                                                    try { serialized = JSON.stringify(cursor.value); } catch (e) { serialized = null; }
                                                    items.push({
                                                        key: String(cursor.key),
                                                        value: serialized && serialized.length > 4000 ? serialized.slice(0, 4000) + "…[truncated json]" : cursor.value,
                                                    });
                                                    cursor.continue();
                                                } else {
                                                    resolve(items);
                                                }
                                            };
                                            cursorReq.onerror = () => resolve(items);
                                        });
                                    } catch (e) {
                                        storeData.error = String(e);
                                    }
                                    entry.stores.push(storeData);
                                }
                                db.close();
                            } catch (e) {
                                entry.error = String(e);
                            }
                            databases.push(entry);
                        }
                        return { databases };
                    }
                    """,
                )
            except Exception as exc:
                result["indexeddb_error"] = str(exc)[:300]

        if "cache_storage" in wanted:
            try:
                result["cache_storage"] = await self._safe_evaluate(
                    page,
                    """
                    async () => {
                        if (!("caches" in window)) return { error: "Cache Storage unavailable" };
                        const names = await caches.keys();
                        const out = [];
                        for (const name of names) {
                            const cache = await caches.open(name);
                            const requests = await cache.keys();
                            out.push({
                                name,
                                entry_count: requests.length,
                                entries: requests.slice(0, 100).map((r) => r.url),
                            });
                        }
                        return { caches: out };
                    }
                    """,
                )
            except Exception as exc:
                result["cache_storage_error"] = str(exc)[:300]

        if "service_workers" in wanted:
            workers = []
            try:
                workers = [
                    {"url": sw.url} for sw in (self.context.service_workers or [])
                ]
            except Exception:
                workers = []
            registrations = None
            try:
                registrations = await self._safe_evaluate(
                    page,
                    """
                    async () => {
                        if (!("serviceWorker" in navigator)) return [];
                        const regs = await navigator.serviceWorker.getRegistrations();
                        return regs.map((reg) => ({
                            scope: reg.scope,
                            active: reg.active ? reg.active.scriptURL : null,
                            installing: reg.installing ? reg.installing.scriptURL : null,
                            waiting: reg.waiting ? reg.waiting.scriptURL : null,
                        }));
                    }
                    """,
                )
            except Exception as exc:
                registrations = {"error": str(exc)[:300]}
            result["service_workers"] = {
                "context_workers": workers,
                "registrations": registrations,
            }
        return result

    async def _resolve_node_id(self, session: Any, selector: str) -> int:
        """Resolve a CSS selector to a CDP backend nodeId (0 when absent).

        Three independent strategies, in order:
        1. Tag the element with a marker attribute via Runtime.evaluate,
           then walk the DOM.getDocument tree in Python to find that node.
        2. Runtime.evaluate + DOM.requestNode.
        3. DOM.getDocument + DOM.querySelector.
        Diagnostics from every failed strategy are kept on
        ``self._last_resolve_diag`` so callers can surface the real cause.
        """
        diag: List[str] = []
        self._last_resolve_diag = diag

        # Strategy 1: marker attribute + document-tree walk.
        try:
            marked = await session.send(
                "Runtime.evaluate",
                {
                    "expression": (
                        "(() => { const el = document.querySelector("
                        + _json.dumps(selector)
                        + "); if (!el) return false;"
                        " el.setAttribute('data-agentic-target', '1'); return true; })()"
                    ),
                    "returnByValue": True,
                },
            )
            mark_value = (marked.get("result") or {}).get("value")
            diag.append(f"mark={mark_value!r} exc={'exceptionDetails' in marked}")
            if mark_value is True:
                document = await session.send(
                    "DOM.getDocument", {"depth": -1, "pierce": True}
                )
                found_id = 0

                def walk(node: Dict[str, Any]) -> None:
                    nonlocal found_id
                    if found_id:
                        return
                    attrs = node.get("attributes") or []
                    for i in range(0, len(attrs) - 1, 2):
                        if attrs[i] == "data-agentic-target" and attrs[i + 1] == "1":
                            found_id = node.get("nodeId", 0)
                            return
                    for child in node.get("children") or []:
                        walk(child)
                    for child in node.get("shadowRoots") or []:
                        walk(child)
                    content = node.get("contentDocument")
                    if content:
                        walk(content)

                walk(document.get("root") or {})
                diag.append(f"tree_walk found={found_id}")
                try:
                    await session.send(
                        "Runtime.evaluate",
                        {
                            "expression": (
                                "(() => { const el = document.querySelector("
                                "'[data-agentic-target=\"1\"]');"
                                " if (el) el.removeAttribute('data-agentic-target'); })()"
                            ),
                            "returnByValue": True,
                        },
                    )
                except Exception:
                    pass
                if found_id:
                    return found_id
        except Exception as exc:
            diag.append(f"strategy1 error: {exc}")

        # Strategy 2: Runtime object -> DOM.requestNode.
        try:
            evaluated = await session.send(
                "Runtime.evaluate",
                {
                    "expression": f"document.querySelector({_json.dumps(selector)})",
                    "returnByValue": False,
                },
            )
            remote = evaluated.get("result", {})
            object_id = remote.get("objectId")
            diag.append(
                f"eval subtype={remote.get('subtype')!r} objectId={'yes' if object_id else 'no'}"
                f" exc={'exceptionDetails' in evaluated}"
            )
            if object_id and remote.get("subtype") != "null":
                node = await session.send("DOM.requestNode", {"objectId": object_id})
                node_id = node.get("nodeId", 0)
                diag.append(f"requestNode={node_id}")
                if node_id:
                    return node_id
        except Exception as exc:
            diag.append(f"strategy2 error: {exc}")

        # Strategy 3: DOM.querySelector from the document root.
        try:
            document = await session.send("DOM.getDocument", {"depth": -1, "pierce": True})
            root = document.get("root", {})
            root_id = root.get("nodeId", 0)
            diag.append(f"doc root={root_id} name={root.get('nodeName')!r}")
            if root_id:
                found = await session.send(
                    "DOM.querySelector", {"nodeId": root_id, "selector": selector}
                )
                node_id = found.get("nodeId", 0)
                diag.append(f"querySelector={node_id}")
                if node_id:
                    return node_id
        except Exception as exc:
            diag.append(f"strategy3 error: {exc}")
        return 0

    async def _get_matched_styles_internal(
        self, selector: str, page_id: Optional[int] = None
    ) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        session, _tracker = await self._get_cdp(page)
        await self._ensure_domains(session, "CSS.enable", "DOM.enable")
        node_id = await self._resolve_node_id(session, selector)
        if not node_id:
            raise ValueError(
                f"Element not found for selector: {selector} "
                f"[diag: {'; '.join(getattr(self, '_last_resolve_diag', []))}]"
            )
        matched = await session.send(
            "CSS.getMatchedStylesForNode", {"nodeId": node_id}
        )

        def format_style(style: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not style:
                return None
            return {
                "stylesheet_id": style.get("styleSheetId"),
                "css_text": style.get("cssText"),
                "properties": [
                    {
                        "name": prop.get("name"),
                        "value": prop.get("value"),
                        "important": bool(prop.get("important")),
                        "disabled": bool(prop.get("disabled")),
                        "implicit": bool(prop.get("implicit")),
                    }
                    for prop in style.get("cssProperties", [])
                    if prop.get("name")
                ],
            }

        def format_rule(rule_entry: Dict[str, Any]) -> Dict[str, Any]:
            rule = rule_entry.get("rule", {})
            selector_list = rule.get("selectorList", {})
            selectors = selector_list.get("selectors", [])
            texts = [sel.get("text") for sel in selectors]
            matching = [
                texts[idx]
                for idx in rule_entry.get("matchingSelectors", [])
                if isinstance(idx, int) and 0 <= idx < len(texts)
            ]
            return {
                "selector_text": selector_list.get("text"),
                "matching_selectors": matching,
                "origin": rule.get("origin"),
                "style": format_style(rule.get("style")),
            }

        return {
            "selector": selector,
            "node_id": node_id,
            "inline_style": format_style(matched.get("inlineStyle")),
            "attributes_style": format_style(matched.get("attributesStyle")),
            "matched_rules": [
                format_rule(entry) for entry in matched.get("matchedCSSRules", [])
            ],
            "inherited": [
                {
                    "inline_style": format_style(entry.get("inlineStyle")),
                    "matched_rules": [
                        format_rule(rule) for rule in entry.get("matchedCSSRules", [])
                    ],
                }
                for entry in matched.get("inherited", [])
            ],
            "pseudo_elements": [
                {
                    "pseudo_type": entry.get("pseudoType"),
                    "matched_rules": [
                        format_rule(rule) for rule in entry.get("matchedCSSRules", [])
                    ],
                }
                for entry in matched.get("pseudoElements", [])
            ],
        }

    async def _get_accessibility_tree_internal(
        self,
        max_depth: int = 30,
        page_id: Optional[int] = None,
        root_node_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        session, _tracker = await self._get_cdp(page)
        await self._ensure_domains(session, "Accessibility.enable")
        result = await session.send("Accessibility.getFullAXTree")
        nodes = result.get("nodes", [])
        by_id = {node.get("nodeId"): node for node in nodes}

        def ax_value(raw: Any) -> Any:
            if isinstance(raw, dict):
                return raw.get("value")
            return raw

        def build(node: Dict[str, Any], depth: int, seen: set) -> Dict[str, Any]:
            node_id = node.get("nodeId")
            item: Dict[str, Any] = {
                "node_id": node_id,
                "role": ax_value(node.get("role")),
                "name": ax_value(node.get("name")),
                "ignored": bool(node.get("ignored")),
            }
            value = ax_value(node.get("value"))
            if value is not None:
                item["value"] = value
            properties = {}
            for prop in node.get("properties", []):
                properties[prop.get("name")] = ax_value(prop.get("value"))
            if properties:
                item["properties"] = properties
            if depth < max_depth:
                children = []
                for child_id in node.get("childIds", []):
                    child = by_id.get(child_id)
                    if child is not None and child_id not in seen:
                        children.append(build(child, depth + 1, seen | {child_id}))
                if children:
                    item["children"] = children
            return item

        root = next((n for n in nodes if not n.get("parentId")), None)
        result: Dict[str, Any] = {"url": page.url, "node_count": len(nodes)}
        if root_node_id is not None:
            # Drill-down (#28): re-root the same capture at one node, the
            # browser counterpart of Snapshot's root_ref. The node_id
            # values are the ones this same method prints on every node,
            # so a drill always refers to the parent capture's numbering.
            target = by_id.get(root_node_id)
            if target is None:
                raise ValueError(
                    f"root_node_id {root_node_id} is not part of this page's "
                    "accessibility tree. Take a fresh "
                    "Browser_GetAccessibilityTree and pass a node_id it "
                    "printed."
                )
            result["root_node_id"] = root_node_id
            result["tree"] = build(target, 0, {root_node_id})
            return result
        result["tree"] = build(root, 0, {root.get("nodeId")}) if root else None
        return result

    async def _list_page_sources_internal(
        self, filter_text: Optional[str] = None, page_id: Optional[int] = None
    ) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        session, tracker = await self._get_cdp(page)
        await self._ensure_domains(session, "Debugger.enable", "CSS.enable")
        scripts = list(tracker.scripts.values())
        stylesheets = list(tracker.stylesheets.values())
        if filter_text:
            needle = filter_text.lower()
            scripts = [s for s in scripts if needle in (s.get("url") or "").lower()]
            stylesheets = [
                s for s in stylesheets if needle in (s.get("source_url") or "").lower()
            ]
        return {
            "url": page.url,
            "scripts": scripts,
            "stylesheets": stylesheets,
            "note": (
                "Only resources parsed since the CDP session attached are listed; "
                "reload the page to capture everything from the start."
            ),
        }

    async def _get_source_text_internal(
        self,
        source_id: str,
        kind: str,
        max_chars: int,
        page_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        page = await self._get_page_by_id_or_active(page_id)
        session, _tracker = await self._get_cdp(page)
        await self._ensure_domains(session, "Debugger.enable", "CSS.enable")
        if kind == "stylesheet":
            response = await session.send(
                "CSS.getStyleSheetText", {"styleSheetId": source_id}
            )
            text = response.get("text", "")
        else:
            response = await session.send(
                "Debugger.getScriptSource", {"scriptId": source_id}
            )
            text = response.get("scriptSource", "")
        return {
            "kind": kind,
            "id": source_id,
            "length": len(text),
            "truncated": len(text) > max_chars,
            "source": text[:max_chars],
        }

    async def get_network_details_deep(
        self,
        request_id_or_url: str,
        include_body: bool = True,
        max_body_chars: int = MAX_BODY_CHARS_DEFAULT,
        page_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self._invoke(
            lambda: self._get_network_details_deep_internal(
                request_id_or_url, include_body, max_body_chars, page_id
            )
        )

    async def get_storage(
        self, types: Optional[List[str]] = None, page_id: Optional[int] = None
    ) -> Dict[str, Any]:
        return await self._invoke(lambda: self._get_storage_internal(types, page_id))

    async def get_matched_styles(
        self, selector: str, page_id: Optional[int] = None
    ) -> Dict[str, Any]:
        return await self._invoke(
            lambda: self._get_matched_styles_internal(selector, page_id)
        )

    async def get_accessibility_tree(
        self,
        max_depth: int = 30,
        page_id: Optional[int] = None,
        root_node_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await self._invoke(
            lambda: self._get_accessibility_tree_internal(max_depth, page_id, root_node_id)
        )

    async def list_page_sources(
        self, filter_text: Optional[str] = None, page_id: Optional[int] = None
    ) -> Dict[str, Any]:
        return await self._invoke(
            lambda: self._list_page_sources_internal(filter_text, page_id)
        )

    async def get_source_text(
        self,
        source_id: str,
        kind: str = "script",
        max_chars: int = 50000,
        page_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await self._invoke(
            lambda: self._get_source_text_internal(source_id, kind, max_chars, page_id)
        )
