from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Request,
    Response,
    async_playwright,
)

logger = logging.getLogger(__name__)

DEFAULT_CDP_URL = "http://localhost:9222"
MAX_BUFFER_SIZE = 500


class BrowserDevToolsManager:
    """Manages Playwright lifecycle on an isolated Proactor thread on Windows."""

    _instance: Optional[BrowserDevToolsManager] = None

    def __init__(self) -> None:
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.pages: List[Page] = []
        self.active_page_index: int = 0
        self.is_connected_cdp: bool = False
        self.cdp_url: Optional[str] = None

        # Diagnostic ring buffers
        self.console_logs: List[Dict[str, Any]] = []
        self.network_requests: List[Dict[str, Any]] = []
        self.network_details: Dict[str, Dict[str, Any]] = {}

        # Dedicated worker loop running ProactorEventLoop (required for Windows subprocesses)
        self._worker_loop: Optional[asyncio.AbstractEventLoop] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._start_worker_thread()

    def _start_worker_thread(self) -> None:
        def runner() -> None:
            loop = asyncio.WindowsProactorEventLoopPolicy().new_event_loop()
            asyncio.set_event_loop(loop)
            self._worker_loop = loop
            self._ready_event.set()
            loop.run_forever()

        self._worker_thread = threading.Thread(target=runner, daemon=True, name="PlaywrightProactorThread")
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

    # Internal worker methods (executed inside Proactor loop)
    async def _init_playwright(self) -> None:
        if self.playwright is None:
            self.playwright = await async_playwright().start()

    def _attach_page_listeners(self, page: Page) -> None:
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

    async def _connect_or_launch_internal(
        self,
        cdp_url: str = DEFAULT_CDP_URL,
        headless: bool = False,
        browser_type: str = "chromium",
    ) -> Dict[str, Any]:
        await self._init_playwright()

        if self.browser and self.browser.is_connected():
            return {
                "status": "connected",
                "mode": "cdp" if self.is_connected_cdp else "standalone",
                "active_pages": len(self.pages),
                "cdp_url": self.cdp_url,
            }

        # 1. Attempt CDP
        if cdp_url:
            try:
                self.browser = await asyncio.wait_for(
                    self.playwright.chromium.connect_over_cdp(cdp_url),
                    timeout=2.0,
                )
                self.is_connected_cdp = True
                self.cdp_url = cdp_url
                contexts = self.browser.contexts
                if contexts:
                    self.context = contexts[0]
                    self.pages = list(self.context.pages)
                else:
                    self.context = await self.browser.new_context()
                    self.pages = []

                for page in self.pages:
                    self._attach_page_listeners(page)

                if not self.pages:
                    new_page = await self.context.new_page()
                    self._attach_page_listeners(new_page)
                    self.pages.append(new_page)

                self.active_page_index = 0
                return {
                    "status": "connected",
                    "mode": "cdp",
                    "cdp_url": cdp_url,
                    "open_pages_count": len(self.pages),
                }
            except Exception as e:
                logger.info("CDP attach to %s skipped (%s). Fallback to standalone launch.", cdp_url, e)

        # 2. Standalone launch
        self.is_connected_cdp = False
        self.cdp_url = None
        launcher = getattr(self.playwright, browser_type, self.playwright.chromium)
        self.browser = await launcher.launch(headless=headless)
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        )
        page = await self.context.new_page()
        self._attach_page_listeners(page)
        self.pages = [page]
        self.active_page_index = 0

        return {
            "status": "connected",
            "mode": "standalone",
            "browser_type": browser_type,
            "headless": headless,
            "open_pages_count": 1,
        }

    async def _get_active_page_internal(self) -> Page:
        if not self.browser or not self.browser.is_connected() or not self.pages:
            await self._connect_or_launch_internal()

        self.pages = [p for p in self.pages if not p.is_closed()]
        if not self.pages:
            if not self.context:
                self.context = await self.browser.new_context()
            page = await self.context.new_page()
            self._attach_page_listeners(page)
            self.pages = [page]
            self.active_page_index = 0

        if self.active_page_index >= len(self.pages):
            self.active_page_index = len(self.pages) - 1

        return self.pages[self.active_page_index]

    async def _safe_evaluate(self, page: Page, script: str, arg: Any = None) -> Any:
        """Evaluates script with retry if execution context was destroyed by in-flight navigation."""
        try:
            if arg is not None:
                return await page.evaluate(script, arg)
            return await page.evaluate(script)
        except Exception as e:
            if "Execution context was destroyed" in str(e) or "Navigating frame was detached" in str(e):
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(0.1)
                if arg is not None:
                    return await page.evaluate(script, arg)
                return await page.evaluate(script)
            raise

    async def _navigate_internal(self, url: str, wait_until: str, timeout_ms: int) -> Dict[str, Any]:
        page = await self._get_active_page_internal()
        res = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        return {
            "url": page.url,
            "title": await page.title(),
            "status": res.status if res else 200,
        }

    async def _get_page_snapshot_internal(self, selector: Optional[str], format_type: str, max_length: int) -> Dict[str, Any]:
        page = await self._get_active_page_internal()
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
            return {"url": page.url, "format": "text", "content": text[:max_length]}

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

    async def _get_css_styles_internal(self, selector: str, properties: Optional[List[str]]) -> Dict[str, Any]:
        page = await self._get_active_page_internal()
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
            raise ValueError(f"Element matching selector '{selector}' was not found.")
        return result

    async def _evaluate_script_internal(self, script: str, arg: Any) -> Any:
        page = await self._get_active_page_internal()
        return await self._safe_evaluate(page, script, arg)

    async def _query_elements_internal(self, selector: str, attributes: Optional[List[str]], limit: int) -> List[Dict[str, Any]]:
        page = await self._get_active_page_internal()
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

    async def _click_internal(self, selector: str, button: str, click_count: int) -> Dict[str, Any]:
        page = await self._get_active_page_internal()
        await page.click(selector, button=button, click_count=click_count)
        return {"action": "click", "selector": selector, "status": "success"}

    async def _fill_internal(self, selector: str, value: str) -> Dict[str, Any]:
        page = await self._get_active_page_internal()
        await page.fill(selector, value)
        return {"action": "fill", "selector": selector, "value_length": len(value), "status": "success"}

    async def _hover_internal(self, selector: str) -> Dict[str, Any]:
        page = await self._get_active_page_internal()
        await page.hover(selector)
        return {"action": "hover", "selector": selector, "status": "success"}

    async def _press_key_internal(self, key: str) -> Dict[str, Any]:
        page = await self._get_active_page_internal()
        await page.keyboard.press(key)
        return {"action": "press_key", "key": key, "status": "success"}

    async def _wait_for_internal(self, selector: Optional[str], state: str, timeout_ms: int) -> Dict[str, Any]:
        page = await self._get_active_page_internal()
        if selector:
            await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
            return {"waited_for": "selector", "selector": selector, "state": state, "status": "resolved"}
        else:
            await page.wait_for_load_state(state if state in ["load", "domcontentloaded", "networkidle"] else "load", timeout=timeout_ms)
            return {"waited_for": "load_state", "state": state, "status": "resolved"}

    async def _take_screenshot_internal(self, selector: Optional[str], full_page: bool, path: Optional[str]) -> Dict[str, Any]:
        page = await self._get_active_page_internal()
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
        page_info = []
        for i, p in enumerate(self.pages):
            if not p.is_closed():
                page_info.append({
                    "page_id": i,
                    "title": await p.title() if not p.is_closed() else "Closed",
                    "url": p.url,
                    "is_active": (i == self.active_page_index),
                })
        return page_info

    async def _select_page_internal(self, page_id: int) -> Dict[str, Any]:
        if 0 <= page_id < len(self.pages) and not self.pages[page_id].is_closed():
            self.active_page_index = page_id
            page = self.pages[page_id]
            await page.bring_to_front()
            return {"page_id": page_id, "url": page.url, "title": await page.title()}
        raise ValueError(f"Invalid or closed page_id: {page_id}")

    async def _new_page_internal(self, url: str) -> Dict[str, Any]:
        await self._get_active_page_internal()
        page = await self.context.new_page()
        self._attach_page_listeners(page)
        self.pages.append(page)
        self.active_page_index = len(self.pages) - 1
        if url and url != "about:blank":
            await page.goto(url, wait_until="domcontentloaded")
        return {"page_id": self.active_page_index, "url": page.url, "title": await page.title()}

    async def _close_page_internal(self, page_id: Optional[int]) -> Dict[str, Any]:
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
        if self.browser:
            await self.browser.close()
            self.browser = None
            self.context = None
            self.pages.clear()
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    # Public asynchronous API (safely bridges whatever caller event loop to the Proactor worker)
    async def connect_or_launch(self, cdp_url: str = DEFAULT_CDP_URL, headless: bool = False, browser_type: str = "chromium") -> Dict[str, Any]:
        return await self._run_in_worker(self._connect_or_launch_internal(cdp_url, headless, browser_type))

    async def navigate(self, url: str, wait_until: str = "load", timeout_ms: int = 30000) -> Dict[str, Any]:
        return await self._run_in_worker(self._navigate_internal(url, wait_until, timeout_ms))

    async def get_page_snapshot(self, selector: Optional[str] = None, format_type: str = "clean_html", max_length: int = 15000) -> Dict[str, Any]:
        return await self._run_in_worker(self._get_page_snapshot_internal(selector, format_type, max_length))

    async def get_css_styles(self, selector: str, properties: Optional[List[str]] = None) -> Dict[str, Any]:
        return await self._run_in_worker(self._get_css_styles_internal(selector, properties))

    async def evaluate_script(self, script: str, arg: Any = None) -> Any:
        return await self._run_in_worker(self._evaluate_script_internal(script, arg))

    async def query_elements(self, selector: str, attributes: Optional[List[str]] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return await self._run_in_worker(self._query_elements_internal(selector, attributes, limit))

    async def click(self, selector: str, button: str = "left", click_count: int = 1) -> Dict[str, Any]:
        return await self._run_in_worker(self._click_internal(selector, button, click_count))

    async def fill(self, selector: str, value: str) -> Dict[str, Any]:
        return await self._run_in_worker(self._fill_internal(selector, value))

    async def hover(self, selector: str) -> Dict[str, Any]:
        return await self._run_in_worker(self._hover_internal(selector))

    async def press_key(self, key: str) -> Dict[str, Any]:
        return await self._run_in_worker(self._press_key_internal(key))

    async def wait_for(self, selector: Optional[str] = None, state: str = "visible", timeout_ms: int = 10000) -> Dict[str, Any]:
        return await self._run_in_worker(self._wait_for_internal(selector, state, timeout_ms))

    async def take_screenshot(self, selector: Optional[str] = None, full_page: bool = False, path: Optional[str] = None) -> Dict[str, Any]:
        return await self._run_in_worker(self._take_screenshot_internal(selector, full_page, path))

    async def list_pages(self) -> List[Dict[str, Any]]:
        return await self._run_in_worker(self._list_pages_internal())

    async def select_page(self, page_id: int) -> Dict[str, Any]:
        return await self._run_in_worker(self._select_page_internal(page_id))

    async def new_page(self, url: str = "about:blank") -> Dict[str, Any]:
        return await self._run_in_worker(self._new_page_internal(url))

    async def close_page(self, page_id: Optional[int] = None) -> Dict[str, Any]:
        return await self._run_in_worker(self._close_page_internal(page_id))

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
