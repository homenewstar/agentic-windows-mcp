"""Browser DevTools tools — provides Chrome DevTools Protocol & Playwright automation.

Enables agents to scrape complex web pages, dissect HTML/CSS structures, test UI interactions,
and debug frontend network/console logs like chrome-devtools-mcp.
"""

from typing import Any, List, Optional
import json

from mcp.types import ToolAnnotations
from windows_mcp.browser.devtools_manager import BrowserDevToolsManager


def register(mcp, *, get_desktop, get_analytics):
    manager = BrowserDevToolsManager.get_instance()

    @mcp.tool(
        name="Browser_Connect",
        description=(
            "Connect to Chrome/Edge via Chrome DevTools Protocol (CDP port 9222) or launch a dedicated "
            "automation browser. Supports hybrid mode: attaches to user's running browser if available, "
            "otherwise spins up an isolated Playwright browser."
        ),
        annotations=ToolAnnotations(
            title="Browser Connect",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_connect(
        cdp_url: str = "http://localhost:9222",
        headless: bool = False,
        browser_type: str = "chromium",
    ) -> str:
        res = await manager.connect_or_launch(cdp_url=cdp_url, headless=headless, browser_type=browser_type)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_Navigate",
        description="Navigate the browser to a URL with configurable wait condition (load, domcontentloaded, networkidle).",
        annotations=ToolAnnotations(
            title="Browser Navigate",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def browser_navigate(
        url: str,
        wait_until: str = "load",
        timeout_ms: int = 30000,
    ) -> str:
        res = await manager.navigate(url=url, wait_until=wait_until, timeout_ms=timeout_ms)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_GetPageSnapshot",
        description=(
            "Extract and dissect the page's HTML structure or text content. Cleans noisy elements like "
            "base64 images, large SVGs, and inline scripts to keep LLM context clean. Optionally scoped to a CSS selector."
        ),
        annotations=ToolAnnotations(
            title="Browser Page Snapshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_get_page_snapshot(
        selector: Optional[str] = None,
        format_type: str = "clean_html",
        max_length: int = 15000,
    ) -> str:
        res = await manager.get_page_snapshot(selector=selector, format_type=format_type, max_length=max_length)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_GetCssStyles",
        description=(
            "Dissect CSS styles and box-model layout of elements matching a CSS selector. "
            "Returns computed styles (display, position, flex/grid, colors, typography), bounding box rect, and visibility."
        ),
        annotations=ToolAnnotations(
            title="Browser Get CSS Styles",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_get_css_styles(
        selector: str,
        properties: Optional[List[str]] = None,
    ) -> str:
        res = await manager.get_css_styles(selector=selector, properties=properties)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_EvaluateScript",
        description="Execute arbitrary JavaScript directly in the browser's context (access window, document, localStorage, React/Vue state).",
        annotations=ToolAnnotations(
            title="Browser Evaluate Script",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def browser_evaluate_script(script: str, arg: Optional[Any] = None) -> str:
        res = await manager.evaluate_script(script=script, arg=arg)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_QueryElements",
        description="Query elements matching a CSS selector, returning tag names, inner text, attributes (href, src, class, id), and bounding boxes.",
        annotations=ToolAnnotations(
            title="Browser Query Elements",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_query_elements(
        selector: str,
        attributes: Optional[List[str]] = None,
        limit: int = 50,
    ) -> str:
        res = await manager.query_elements(selector=selector, attributes=attributes, limit=limit)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_Interact",
        description="Interact with web elements for automated UI testing (action: 'click', 'fill', 'hover', 'press_key', 'wait_for').",
        annotations=ToolAnnotations(
            title="Browser Interact",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def browser_interact(
        action: str,
        selector: Optional[str] = None,
        value: Optional[str] = None,
        key: Optional[str] = None,
        timeout_ms: int = 10000,
    ) -> str:
        act = action.lower()
        if act == "click":
            if not selector:
                raise ValueError("Selector required for 'click'")
            res = await manager.click(selector=selector)
        elif act == "fill":
            if not selector or value is None:
                raise ValueError("Selector and value required for 'fill'")
            res = await manager.fill(selector=selector, value=value)
        elif act == "hover":
            if not selector:
                raise ValueError("Selector required for 'hover'")
            res = await manager.hover(selector=selector)
        elif act == "press_key":
            if not key:
                raise ValueError("Key required for 'press_key'")
            res = await manager.press_key(key=key)
        elif act == "wait_for":
            res = await manager.wait_for(selector=selector, timeout_ms=timeout_ms)
        else:
            raise ValueError(f"Unknown action: {action}. Supported: click, fill, hover, press_key, wait_for")

        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_TakeScreenshot",
        description="Capture a screenshot of the active web page, viewport, or specific element selector.",
        annotations=ToolAnnotations(
            title="Browser Take Screenshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_take_screenshot(
        selector: Optional[str] = None,
        full_page: bool = False,
        save_path: Optional[str] = None,
    ) -> str:
        res = await manager.take_screenshot(selector=selector, full_page=full_page, path=save_path)
        return json.dumps({
            "status": "success",
            "size_bytes": res["size_bytes"],
            "save_path": res["path"],
            "base64_preview": res["base64_preview"],
        }, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_ListNetworkRequests",
        description="Inspect intercepted HTTP/API network requests (method, url, status, duration, resource type) for debugging frontend APIs.",
        annotations=ToolAnnotations(
            title="Browser List Network Requests",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_list_network_requests(
        filter_url: Optional[str] = None,
        resource_type: Optional[str] = None,
        status_code: Optional[int] = None,
        limit: int = 50,
        clear: bool = False,
    ) -> str:
        res = manager.list_network_requests(
            filter_url=filter_url,
            resource_type=resource_type,
            status_code=status_code,
            limit=limit,
            clear=clear,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_GetNetworkDetails",
        description="Retrieve detailed request and response headers, timing, and status for a specific network request URL or ID.",
        annotations=ToolAnnotations(
            title="Browser Get Network Details",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_get_network_details(request_id_or_url: str) -> str:
        res = manager.get_network_details(request_id_or_url)
        return json.dumps(res or {"error": "Request not found"}, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_ListConsoleMessages",
        description="Inspect browser console logs (log, info, warn, error, uncaught page exceptions) for debugging frontend code.",
        annotations=ToolAnnotations(
            title="Browser List Console Messages",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_list_console_messages(
        level: Optional[str] = None,
        limit: int = 50,
        clear: bool = False,
    ) -> str:
        res = manager.list_console_logs(level=level, limit=limit, clear=clear)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_ManageTabs",
        description="Manage browser tabs/pages (action: 'list', 'new', 'select', 'close').",
        annotations=ToolAnnotations(
            title="Browser Manage Tabs",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def browser_manage_tabs(
        action: str = "list",
        page_id: Optional[int] = None,
        url: Optional[str] = None,
    ) -> str:
        act = action.lower()
        if act == "list":
            res = await manager.list_pages()
        elif act == "new":
            res = await manager.new_page(url=url or "about:blank")
        elif act == "select":
            if page_id is None:
                raise ValueError("page_id is required for 'select'")
            res = await manager.select_page(page_id=page_id)
        elif act == "close":
            res = await manager.close_page(page_id=page_id)
        else:
            raise ValueError(f"Unknown action: {action}. Supported: list, new, select, close")

        return json.dumps(res, indent=2, ensure_ascii=False)
