"""Snapshot and Screenshot tools — desktop state capture."""

import logging

from mcp.types import ToolAnnotations
from agentic_windows_mcp.infrastructure import with_analytics
from fastmcp import Context

from agentic_windows_mcp.desktop.image_post import (
    process_screenshot,
    validate_screenshot_options,
)
from agentic_windows_mcp.desktop.service import AmbiguousRefError, StaleRefError
from agentic_windows_mcp.tools._snapshot_helpers import (
    _as_bool,
    _as_region,
    capture_desktop_state,
    build_snapshot_response,
)
from agentic_windows_mcp.tools.elements import parse_ref

logger = logging.getLogger(__name__)

# Populated by register(); exposed for backward-compatible test imports.
state_tool = None
screenshot_tool = None


def register(mcp, *, get_desktop, get_analytics):
    global state_tool, screenshot_tool
    @mcp.tool(
        name='Snapshot',
        description="Take a screenshot and inspect the screen. Keywords: screenshot, screen capture, see screen, observe, look, inspect, UI elements, what's on screen. Captures complete desktop state including: system language, focused/opened windows, interactive elements (buttons, text fields, links, menus with coordinates), and scrollable areas. Set use_vision=True to include screenshot with cursor highlight. Set use_annotation=False to get a clean screenshot without bounding box overlays on UI elements (default: True, draws colored rectangles around detected elements). Set use_ui_tree=False for a faster screenshot-only snapshot when you do not need interactive or scrollable element extraction. Set width_reference_lines/height_reference_lines to overlay a grid for better spatial reasoning (make sure vision is enabled to use it). Set use_dom=True for browser content to get web page elements instead of browser UI. Set display=[0] or display=[0,1] using zero-based active Windows display indices; omit it to keep the default full-desktop behavior. Set region=[left, top, right, bottom] in virtual-desktop pixel coordinates to capture and inspect only that rectangle instead of the whole screen/display — useful when you already know which area matters and want to save tokens; region takes precedence over display when both are given, and an invalid or out-of-bounds region raises an error rather than silently capturing something else. Dense windows are captured up to an element limit (500 by default): set offset/limit to page through the full interactive element list — the response then includes an 'Interactive Elements Page' section with each element's global index (the same numbering label-based tools accept), the total captured count, and the next offset when more remain. Scope the capture with window=<handle> or window='<title or process name>' to capture only one window's tree and refs (an ambiguous name returns the candidate handles instead of guessing); a window-scoped capture also crops the screenshot to the window's bounds by default — set crop_to_window=False to keep the full screen. Re-observe a single element's subtree with root_ref='@e12': the drill-down capture extends the current snapshot's refs — same Snapshot ID, existing refs keep their labels, new elements append — instead of replacing them. Every capture reports 'Capture Completeness': when the element budget cuts the walk the capture is marked PARTIAL and each cut node is marked [truncated] with its child count, so 'children not shown' is never mistaken for 'no children'. Always call this first to understand the current desktop state before taking actions.",
        annotations=ToolAnnotations(
            title="Snapshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "State-Tool", run_sync_in_thread=False)
    def _state_tool(
        use_vision: bool | str = False,
        use_dom: bool | str = False,
        use_annotation: bool | str = True,
        use_ui_tree: bool | str = True,
        width_reference_line: int | None = None,
        height_reference_line: int | None = None,
        display: list[int] | None = None,
        region: list[int] | str | None = None,
        offset: int = 0,
        limit: int | None = None,
        window: int | str | None = None,
        root_ref: str | int | None = None,
        crop_to_window: bool | str = True,
        ctx: Context = None,
    ):
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 5000
        ):
            raise ValueError("limit must be an integer between 1 and 5000")
        if window is not None and root_ref is not None:
            raise ValueError("Pass either window or root_ref, not both.")
        # Window scope (#18): an int is a window handle; a string is
        # resolved against window titles and process names, and an
        # ambiguous string returns the candidate list instead of
        # guessing. The scope restricts the captured tree and its refs;
        # since #28 the screenshot is also cropped to the window's
        # bounds by default (see below).
        window_handle = None
        if isinstance(window, bool):
            raise ValueError(
                "window must be a window handle (int) or a title/process name (str)"
            )
        if isinstance(window, int):
            window_handle = window
        elif isinstance(window, str):
            candidates = get_desktop().find_window_candidates(window)
            if not candidates:
                return [
                    f"No window matches {window!r}. Call Snapshot without "
                    "a window scope to list the open windows."
                ]
            if len(candidates) > 1:
                lines = [
                    f"{len(candidates)} windows match {window!r} — "
                    "call again with the handle of the one you mean:"
                ]
                for candidate in candidates:
                    lines.append(
                        f"- handle {candidate['handle']}: "
                        f"\"{candidate['title']}\" "
                        f"(process {candidate['process'] or 'unknown'})"
                    )
                return ["\n".join(lines)]
            window_handle = candidates[0]["handle"]
        elif window is not None:
            raise ValueError(
                "window must be a window handle (int) or a title/process name (str)"
            )
        # Drill-down (#18): re-observe only one element's subtree. The
        # ref is resolved strictly against the current snapshot, and the
        # subtree's refs extend that snapshot's registry — same Snapshot
        # ID, same numbering — instead of replacing it.
        root_control = None
        root_window_handle = None
        extend_refs = False
        capture_note = None
        if root_ref is not None:
            if region is not None or display is not None:
                raise ValueError(
                    "root_ref cannot be combined with region or display — "
                    "the ref defines the capture root."
                )
            label = parse_ref(root_ref)
            try:
                ref, root_control = get_desktop().resolve_element_ref(label)
            except (StaleRefError, AmbiguousRefError) as e:
                raise ValueError(str(e)) from e
            root_window_handle = ref.window_handle
            extend_refs = True
            capture_note = (
                f"Drill-Down: subtree of @e{label} ({ref.control_type} "
                f"\"{ref.name}\") — refs continue the current snapshot's "
                "numbering and identity scope."
            )
        if window_handle is not None:
            capture_note = (
                f"Scope: window handle {window_handle} only — the tree "
                "and refs below cover that window alone."
            )
        # Screenshot crop (#28): a window-scoped capture crops the
        # screenshot to the window's bounds by default, through the
        # existing region machinery. An explicit region or display
        # always wins, crop_to_window=False keeps the full screen, and
        # unusable bounds (window gone or minimised) quietly fall back
        # to the full screen rather than failing the capture.
        effective_region = _as_region(region)
        if (
            window_handle is not None
            and effective_region is None
            and display is None
            and _as_bool(crop_to_window)
        ):
            bounds = get_desktop().get_window_bounds(window_handle)
            if bounds is not None:
                effective_region = list(bounds)
                capture_note += (
                    " The screenshot is cropped to the window's bounds; "
                    "pass crop_to_window=False for the full screen."
                )
        try:
            capture_result = capture_desktop_state(
                get_desktop(),
                use_vision=_as_bool(use_vision),
                use_dom=_as_bool(use_dom),
                use_annotation=_as_bool(use_annotation),
                use_ui_tree=_as_bool(use_ui_tree),
                width_reference_line=width_reference_line,
                height_reference_line=height_reference_line,
                display=display,
                region=effective_region,
                tool_name="Snapshot tool",
                element_offset=offset,
                element_limit=limit,
                register_refs=True,
                window_handle=window_handle,
                root_control=root_control,
                root_window_handle=root_window_handle,
                extend_refs=extend_refs,
                capture_note=capture_note,
            )
        except Exception as e:
            logger.warning(
                "Snapshot failed with display=%s region=%s use_vision=%s use_dom=%s",
                display,
                region,
                use_vision if 'use_vision' in locals() else None,
                use_dom if 'use_dom' in locals() else None,
                exc_info=True,
            )
            return [f'Error capturing desktop state: {str(e)}. Please try again.']

        return build_snapshot_response(capture_result, include_ui_details=True)

    @mcp.tool(
        name='Screenshot',
        description="Captures a fast screenshot-first desktop snapshot with cursor position, desktop/window summaries, and an image. This path skips UI tree extraction for speed. Use Snapshot when you need interactive element ids, scrollable regions, or browser DOM extraction. Set display=[0] or display=[0,1] using zero-based active Windows display indices to capture only those monitors. Set region=[left, top, right, bottom] in virtual-desktop pixel coordinates to capture only that rectangle instead of the whole screen/display — useful when you already know which area matters and want to save tokens; region takes precedence over display when both are given, and an invalid or out-of-bounds region raises an error rather than silently capturing something else. Every capture reports Captured At and a Frame Hash (sha256 of the frame): identical hashes mean identical frames. Set wait_for_change=True to keep re-capturing until the frame differs from the previous capture (up to wait_timeout seconds, default 10) — use it after an action when you need proof the screen actually updated. Note: the returned image may be downscaled for efficiency; when it is, multiply image coordinates by the ratio of original size to displayed size to get the actual screen coordinates for mouse actions (Click, Move, etc.). Control the returned image size explicitly when a client caps tool-result payloads: set max_width/max_height to downscale (aspect preserved, never upscaled), image_format='jpeg' with quality (1-95) for a much smaller payload than PNG, or save_path to write the image to a file on this machine and return only metadata (path, size, dimensions) instead of the image itself.",
        annotations=ToolAnnotations(
            title="Screenshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Screenshot-Tool")
    def _screenshot_tool(
        use_annotation: bool | str = False,
        width_reference_line: int | None = None,
        height_reference_line: int | None = None,
        display: list[int] | None = None,
        region: list[int] | str | None = None,
        wait_for_change: bool | str = False,
        wait_timeout: float = 10.0,
        max_width: int | None = None,
        max_height: int | None = None,
        image_format: str = "png",
        quality: int = 85,
        save_path: str | None = None,
        ctx: Context = None,
    ):
        if (
            isinstance(wait_timeout, bool)
            or not isinstance(wait_timeout, (int, float))
            or not (0 < wait_timeout <= 60)
        ):
            raise ValueError("wait_timeout must be a number greater than 0 and at most 60")
        fmt = validate_screenshot_options(
            max_width=max_width,
            max_height=max_height,
            image_format=image_format,
            quality=quality,
        )
        try:
            capture_result = capture_desktop_state(
                get_desktop(),
                use_vision=True,
                use_dom=False,
                use_annotation=_as_bool(use_annotation),
                use_ui_tree=False,
                width_reference_line=width_reference_line,
                height_reference_line=height_reference_line,
                display=display,
                region=_as_region(region),
                tool_name="Screenshot tool",
                wait_for_change=_as_bool(wait_for_change),
                wait_timeout=float(wait_timeout),
            )
            if capture_result.get("screenshot_bytes") and (
                max_width is not None
                or max_height is not None
                or fmt != "png"
                or save_path
            ):
                info = process_screenshot(
                    capture_result["screenshot_bytes"],
                    max_width=max_width,
                    max_height=max_height,
                    image_format=fmt,
                    quality=quality,
                    save_path=save_path,
                )
                capture_result["screenshot_bytes"] = info["data"]
                capture_result["image_info"] = info
        except Exception as e:
            logger.warning(
                "Screenshot failed with display=%s region=%s",
                display,
                region,
                exc_info=True,
            )
            return [f'Error capturing screenshot: {str(e)}. Please try again.']

        return build_snapshot_response(
            capture_result,
            include_ui_details=False,
            ui_detail_note="UI Tree: Skipped for fast screenshot-only capture. Call Snapshot when you need interactive or scrollable elements.",
        )

    state_tool = _state_tool
    screenshot_tool = _screenshot_tool
