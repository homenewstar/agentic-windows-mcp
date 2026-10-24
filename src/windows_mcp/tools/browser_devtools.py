"""Browser DevTools tools — provides Chrome DevTools Protocol & Playwright automation.

Enables agents to scrape complex web pages, dissect HTML/CSS structures, test UI interactions,
and debug frontend network/console logs like chrome-devtools-mcp directly on the user's running browser.
"""

from typing import Any, List, Optional
import json

from mcp.types import ToolAnnotations
from windows_mcp.browser.devtools_manager import BrowserDevToolsManager


def register(mcp, *, get_desktop, get_analytics):
    manager = BrowserDevToolsManager.get_instance()

    @mcp.tool(
        name="Browser_Connect",
        description="Connect directly to the user's running browser (Chrome, Edge, Brave) via DevTools CDP WebSocket.",
        annotations=ToolAnnotations(
            title="Browser Connect",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_connect(cdp_url: Optional[str] = None) -> str:
        res = await manager.connect_or_launch(cdp_url=cdp_url)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_Navigate",
        description="Navigate a browser tab to a URL with configurable wait condition (load, domcontentloaded, networkidle). Can target a specific page_id.",
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
        page_id: Optional[int] = None,
    ) -> str:
        res = await manager.navigate(url=url, wait_until=wait_until, timeout_ms=timeout_ms, page_id=page_id)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_GetPageSnapshot",
        description="Extract and dissect the page's HTML structure or text content on the active tab or specific page_id.",
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
        page_id: Optional[int] = None,
    ) -> str:
        res = await manager.get_page_snapshot(selector=selector, format_type=format_type, max_length=max_length, page_id=page_id)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_GetCssStyles",
        description="Dissect CSS styles and box-model layout of elements matching a CSS selector.",
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
        page_id: Optional[int] = None,
    ) -> str:
        res = await manager.get_css_styles(selector=selector, properties=properties, page_id=page_id)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_GetComputedStyle",
        description=(
            "Advanced computed styles extraction: captures element styles, ::before and ::after pseudo-elements, "
            "CSS animations/transitions, and generates a ready-to-use clean CSS snippet for cloning or frontend debugging."
        ),
        annotations=ToolAnnotations(
            title="Browser Get Computed Style",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_get_computed_style(
        selector: str,
        include_pseudo: bool = True,
        include_animations: bool = True,
        page_id: Optional[int] = None,
    ) -> str:
        res = await manager.get_advanced_computed_styles(
            selector=selector,
            include_pseudo=include_pseudo,
            include_animations=include_animations,
            page_id=page_id,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_Highlight",
        description="Visually highlight an element on the user's screen with a vibrant red outline and box-shadow for real-time visual debugging.",
        annotations=ToolAnnotations(
            title="Browser Highlight",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def browser_highlight(
        selector: str,
        color: str = "#ef4444",
        duration_ms: int = 5000,
        scroll_into_view: bool = True,
        page_id: Optional[int] = None,
    ) -> str:
        res = await manager.highlight_element(
            selector=selector,
            color=color,
            duration_ms=duration_ms,
            scroll_into_view=scroll_into_view,
            page_id=page_id,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_EvaluateScript",
        description="Execute arbitrary JavaScript directly in the browser's context. Supports targeting a specific page_id.",
        annotations=ToolAnnotations(
            title="Browser Evaluate Script",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def browser_evaluate_script(
        script: str,
        arg: Optional[Any] = None,
        page_id: Optional[int] = None,
    ) -> str:
        res = await manager.evaluate_script(script=script, arg=arg, page_id=page_id)
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
        page_id: Optional[int] = None,
    ) -> str:
        res = await manager.query_elements(selector=selector, attributes=attributes, limit=limit, page_id=page_id)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_Interact",
        description=(
            "Interact with web elements for automated UI testing. "
            "Supported actions: 'click', 'right_click' (opens context menu), 'double_click' (select text/detail), "
            "'hover', 'hover_and_wait' (shows tooltips), 'fill', 'press_key', 'wait_for', "
            "'drag' (drag element center to absolute page coords target_x/target_y, e.g. panel resize handles). Supports target page_id."
        ),
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
        delay_ms: int = 500,
        timeout_ms: int = 10000,
        page_id: Optional[int] = None,
        target_x: Optional[float] = None,
        target_y: Optional[float] = None,
        steps: int = 20,
        step_delay_ms: int = 0,
        hold_ms: int = 0,
    ) -> str:
        act = action.lower()
        if act == "click":
            if not selector:
                raise ValueError("Selector required for 'click'")
            res = await manager.click(selector=selector, button="left", click_count=1, page_id=page_id)
        elif act == "right_click":
            if not selector:
                raise ValueError("Selector required for 'right_click'")
            res = await manager.click(selector=selector, button="right", click_count=1, page_id=page_id)
        elif act == "double_click":
            if not selector:
                raise ValueError("Selector required for 'double_click'")
            res = await manager.click(selector=selector, button="left", click_count=2, page_id=page_id)
        elif act == "hover":
            if not selector:
                raise ValueError("Selector required for 'hover'")
            res = await manager.hover(selector=selector, delay_ms=0, page_id=page_id)
        elif act == "hover_and_wait":
            if not selector:
                raise ValueError("Selector required for 'hover_and_wait'")
            res = await manager.hover(selector=selector, delay_ms=delay_ms, page_id=page_id)
        elif act == "fill":
            if not selector or value is None:
                raise ValueError("Selector and value required for 'fill'")
            res = await manager.fill(selector=selector, value=value, page_id=page_id)
        elif act == "press_key":
            if not key:
                raise ValueError("Key required for 'press_key'")
            res = await manager.press_key(key=key, page_id=page_id)
        elif act == "wait_for":
            res = await manager.wait_for(selector=selector, timeout_ms=timeout_ms, page_id=page_id)
        elif act == "drag":
            if not selector:
                raise ValueError("Selector required for 'drag'")
            if target_x is None or target_y is None:
                raise ValueError("target_x and target_y required for 'drag'")
            res = await manager.drag(selector=selector, target_x=target_x, target_y=target_y, steps=steps, step_delay_ms=step_delay_ms, hold_ms=hold_ms, page_id=page_id)
        else:
            raise ValueError(
                f"Unknown action: {action}. Supported: click, right_click, double_click, hover, hover_and_wait, fill, press_key, wait_for, drag"
            )

        return json.dumps(res, indent=2, ensure_ascii=False)

    @mcp.tool(
        name="Browser_TakeScreenshot",
        description="Capture a screenshot of the active web page, viewport, or specific element selector. Supports target page_id.",
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
        page_id: Optional[int] = None,
    ) -> str:
        res = await manager.take_screenshot(selector=selector, full_page=full_page, path=save_path, page_id=page_id)
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
