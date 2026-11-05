from agentic_windows_mcp.desktop.utils import (
    as_bool,
    resolve_known_folder_guid_path,
)
from agentic_windows_mcp.powershell.utils import ps_quote
from agentic_windows_mcp.powershell import PowerShellExecutor
from agentic_windows_mcp.vdm.core import (
    get_all_desktops,
    get_current_desktop,
    is_window_on_current_desktop,
)
from agentic_windows_mcp.desktop.views import DesktopState, Window, Browser, Status, Size, Display
from agentic_windows_mcp.tree.views import BoundingBox, TreeElementNode, TreeState, SemanticNode
from PIL import ImageFont, ImageDraw, Image
from agentic_windows_mcp.tree.service import Tree, _window_process_name
from agentic_windows_mcp.desktop import screenshot as screenshot_capture
from agentic_windows_mcp.desktop.control import get_controller
from agentic_windows_mcp.desktop import flash_overlay
from agentic_windows_mcp.infrastructure import safe_get
from agentic_windows_mcp.desktop import control_overlay
from agentic_windows_mcp.uia import integrity as uia_integrity
from urllib.parse import urljoin
from locale import getpreferredencoding
from dataclasses import dataclass
from typing import Literal
from markdownify import markdownify
from fuzzywuzzy import process
from time import sleep, time, perf_counter
from psutil import Process
import math
import win32process
import win32gui
import win32con
import requests
import logging
import random
import ctypes
import csv
import re
import os
import io

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

import agentic_windows_mcp.uia as uia  # noqa: E402

# Key name aliases for shortcut keys that differ from UIA SpecialKeyNames
_KEY_ALIASES = {
    "backspace": "Back",
    "capslock": "Capital",
    "scrolllock": "Scroll",
    "windows": "Win",
    "command": "Win",
    "option": "Alt",
    # Natural names for punctuation keys (US layout).
    "comma": ",",
    "period": ".",
    "dot": ".",
    "minus": "-",
    "dash": "-",
    "hyphen": "-",
    "equal": "=",
    "equals": "=",
    # "+" itself needs Shift and would be typed as a Unicode packet, not a key chord;
    # the "=/+" key is what apps bind for Ctrl+Plus (zoom in).
    "plus": "=",
    "semicolon": ";",
    "slash": "/",
    "backslash": "\\",
    "grave": "`",
    "backtick": "`",
    "bracketleft": "[",
    "lbracket": "[",
    "bracketright": "]",
    "rbracket": "]",
    "apostrophe": "'",
    "quote": "'",
}

# Characters with a meaning in SendKeys syntax; they must be braced to be literal.
_SENDKEYS_SYNTAX_CHARS = set("{}()")


def _shortcut_to_sendkeys(shortcut: str) -> str:
    """Translate "ctrl+shift+comma" style input into a SendKeys string.

    Natural names such as "comma" or "bracketleft" map to their character, and
    SendKeys syntax characters are braced so "alt+(" is a key, not a group.
    Raises ValueError for empty parts (e.g. "ctrl++"; use "plus") so nothing is
    pressed. Unknown multi-character names are rejected by SendKeys itself before
    any input is sent.
    """
    parts = [part.strip() for part in shortcut.split("+")]
    if not shortcut.strip() or any(not part for part in parts):
        raise ValueError(
            f"Invalid shortcut {shortcut!r}: empty key name. "
            'Use names such as "plus", "comma", "minus" for punctuation keys.'
        )
    sendkeys_str = ""
    for key in parts:
        name = _KEY_ALIASES.get(key.lower(), key)
        if len(name) == 1:
            sendkeys_str += "{" + name + "}" if name in _SENDKEYS_SYNTAX_CHARS else name
        else:
            sendkeys_str += "{" + name + "}"
    return sendkeys_str


def _snapshot_profile_enabled() -> bool:
    value = os.getenv("AGENTIC_WINDOWS_MCP_PROFILE_SNAPSHOT", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _escape_text_for_sendkeys(text: str) -> str:
    """Escape special characters so uia.SendKeys types them correctly."""
    result = []
    for ch in text:
        if ch == "{":
            result.append("{{}")
        elif ch == "}":
            result.append("{}}")
        elif ch == "\n":
            result.append("{Enter}")
        elif ch == "\t":
            result.append("{Tab}")
        elif ch == "\r":
            continue
        else:
            result.append(ch)
    return "".join(result)


def _normalize_field_text(value: str) -> str:
    """Normalize a field value for comparison: CRLF/CR become LF."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _assess_typed_text(
    baseline: str | None,
    text: str,
    actual: str | None,
    *,
    clear: bool,
    caret_position: str,
) -> tuple[str, str]:
    """Judge whether typed text landed, for Type's verify option (#10).

    Returns ``(status, detail)`` where status is ``verified``,
    ``mismatch`` or ``unavailable``. ``baseline`` is the field's value
    read before typing (None when unreadable), ``actual`` its value
    after typing. The comparison is exact when the expected content is
    fully determined (clear, or a known baseline); with an unreadable
    baseline on an append it degrades to a containment check, which
    still catches the classic dropped-characters failure.
    """
    if actual is None:
        return (
            "unavailable",
            "the focused element does not expose a readable value, "
            "so the typed text could not be checked",
        )
    actual_norm = _normalize_field_text(actual)
    text_norm = _normalize_field_text(text)
    expected: str | None = None
    if clear:
        expected = text_norm
    elif baseline is not None and caret_position in ("start", "end"):
        baseline_norm = _normalize_field_text(baseline)
        expected = text_norm + baseline_norm if caret_position == "start" else baseline_norm + text_norm
    # caret_position == "idle" leaves the insertion point wherever the
    # click landed, so the expected content is not fully determined and
    # the containment check below applies even with a known baseline.
    if expected is not None:
        if actual_norm == expected:
            return "verified", "the field's content matches the typed text"
        return (
            "mismatch",
            f"expected the field to contain {expected[:80]!r} "
            f"but it contains {actual_norm[:80]!r}",
        )
    if text_norm and text_norm in actual_norm:
        return "verified", "the typed text is present in the field's content"
    if not text_norm:
        return "verified", "nothing to check (empty text)"
    return (
        "mismatch",
        f"the typed text was not found in the field's content {actual_norm[:80]!r}",
    )


class StaleRefError(RuntimeError):
    """An element ref (@e/@s) no longer resolves to its snapshot element."""


class AmbiguousRefError(RuntimeError):
    """An element ref matches several live elements, or was never unique
    (issue #12) — resolving it would mean guessing, so it is refused."""


@dataclass
class ElementRef:
    """Identity of one snapshot element, captured for later re-acquisition."""

    label: int
    runtime_id: tuple | None
    window_handle: int
    name: str
    control_type: str
    # Secure-field marker (#15): the element's IsPassword state at snapshot
    # time. Carried on the ref so write tools can refuse to echo values; the
    # ref identity itself never contains the field's content.
    is_password: bool = False
    # Strict-resolution evidence (#12): the element's AutomationId and help
    # text at capture; whether its identity predicate (window, control type,
    # AutomationId, name) was unique in the window at capture; and the
    # window owner's identity (pid + process creation-time token) so a
    # recycled HWND fails closed instead of resolving into another window.
    # Control values and bounds are never part of the identity.
    automation_id: str = ""
    description: str = ""
    window_pid: int = 0
    window_process_token: float | None = None
    unique_at_capture: bool = True


def find_control_by_runtime_id(root, runtime_id: tuple, max_nodes: int = 20000):
    """Breadth-first search of a UIA subtree for an exact RuntimeId match.

    Returns the live control, or None when the element is gone (closed,
    re-rendered, navigated away) — the caller turns that into STALE_REF.
    """
    visited = 0
    queue = [root]
    while queue:
        control = queue.pop(0)
        visited += 1
        if visited > max_nodes:
            return None
        try:
            if tuple(control.GetRuntimeId()) == tuple(runtime_id):
                return control
        except Exception:
            continue
        try:
            queue.extend(control.GetChildren())
        except Exception:
            continue
    return None


def _window_identity(window_handle: int) -> tuple[int, float | None]:
    """(pid, process creation-time token) owning a window handle (#12).

    The token corroborates the handle: HWNDs are recycled by the OS after a
    window is destroyed, so a stored ref also records *which process
    generation* owned the window at capture time. (0, None) when nothing
    can be read — unknown identity is checked against nothing, never
    treated as a match or a mismatch by itself.
    """
    if not window_handle:
        return 0, None
    try:
        _, pid = win32process.GetWindowThreadProcessId(window_handle)
    except Exception:
        return 0, None
    if not pid:
        return 0, None
    try:
        token = Process(pid).create_time()
    except Exception:
        token = None
    return pid, token


def find_controls_by_identity(
    root,
    *,
    control_type: str,
    automation_id: str,
    name: str,
    max_nodes: int = 20000,
):
    """Count live elements matching a stored identity predicate (#12).

    The predicate is control type + AutomationId + stable name; control
    values and bounds are never identity (the reference counting semantics
    live in benchmarks/locator-resolution). Returns ``(matches, unproven)``:
    ``unproven`` is True when at least one candidate whose type matched had
    a field that could not be read, so a zero count cannot be trusted as
    proof of absence.
    """
    matches = []
    unproven = False
    visited = 0
    queue = [root]
    while queue:
        control = queue.pop(0)
        visited += 1
        if visited > max_nodes:
            break
        try:
            queue.extend(control.GetChildren())
        except Exception:
            pass
        try:
            live_type = (control.LocalizedControlType or "").title()
        except Exception:
            continue
        if live_type != control_type:
            continue
        try:
            live_aid = control.AutomationId or ""
            live_name = (control.Name or "").strip()
        except Exception:
            unproven = True
            continue
        if live_aid == automation_id and live_name == name:
            matches.append(control)
    return matches, unproven


def _rect_tuple(control):
    """(left, top, right, bottom) of a control, or None when unavailable."""
    try:
        rect = control.BoundingRectangle
    except Exception:
        return None
    if rect is None:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


# ---- Snapshot query helpers (issue #17) ----
#
# Get/Is read LIVE properties through these readers. Each returns
# (known, value): known=False means the element does not expose the
# property or the read failed, and the caller must report "unknown" —
# a value is never guessed. Any exception inside a reader is treated
# the same way by the caller.

def _q_name(ref, control):
    return True, str(control.Name or "")


def _q_automation_id(ref, control):
    return True, str(control.AutomationId or "")


def _q_enabled(ref, control):
    return True, bool(control.IsEnabled)


def _q_offscreen(ref, control):
    return True, bool(control.IsOffscreen)


def _q_focused(ref, control):
    return True, bool(control.HasKeyboardFocus)


def _q_bounds(ref, control):
    rect = _rect_tuple(control)
    return rect is not None, (list(rect) if rect is not None else None)


def _q_value(ref, control):
    # Secure fields (#15): a password value is never read back, not even
    # by a query tool. The read is "known" — what is known is that the
    # value exists and is withheld.
    if getattr(ref, "is_password", False) or getattr(control, "IsPassword", False):
        return True, "(secure field — value withheld)"
    pattern = control.GetPattern(uia.PatternId.ValuePattern)
    if pattern is not None:
        return True, str(pattern.Value)
    legacy = control.GetPattern(uia.PatternId.LegacyIAccessiblePattern)
    if legacy is not None:
        return True, str(legacy.Value)
    return False, None


def _q_toggle_state(ref, control):
    pattern = control.GetPattern(uia.PatternId.TogglePattern)
    if pattern is None:
        return False, None
    state = pattern.ToggleState
    return True, {0: "Off", 1: "On", 2: "Indeterminate"}.get(state, str(state))


def _q_selected(ref, control):
    pattern = control.GetPattern(uia.PatternId.SelectionItemPattern)
    if pattern is None:
        return False, None
    return True, bool(pattern.IsSelected)


_QUERY_READERS = {
    "name": _q_name,
    "value": _q_value,
    "bounds": _q_bounds,
    "enabled": _q_enabled,
    "offscreen": _q_offscreen,
    "focused": _q_focused,
    "toggle_state": _q_toggle_state,
    "selected": _q_selected,
    "automation_id": _q_automation_id,
}


def _p_enabled(ref, control):
    return bool(control.IsEnabled)


def _p_visible(ref, control):
    return not bool(control.IsOffscreen)


def _p_focused(ref, control):
    return bool(control.HasKeyboardFocus)


def _p_checked(ref, control):
    known, state = _q_toggle_state(ref, control)
    if not known:
        return None
    # Indeterminate is honestly neither checked nor unchecked.
    return {"On": True, "Off": False}.get(state)


def _p_expanded(ref, control):
    pattern = control.GetPattern(uia.PatternId.ExpandCollapsePattern)
    if pattern is None:
        return None
    state = pattern.ExpandCollapseState
    if state in (1, 2):  # Expanded, PartiallyExpanded
        return True
    if state in (0, 3):  # Collapsed, LeafNode (a leaf cannot expand)
        return False
    return None


def _p_selected(ref, control):
    known, value = _q_selected(ref, control)
    return value if known else None


def _p_editable(ref, control):
    pattern = control.GetPattern(uia.PatternId.ValuePattern)
    if pattern is None:
        return None
    return not bool(pattern.IsReadOnly)


def _p_password(ref, control):
    return bool(control.IsPassword)


_QUERY_PREDICATES = {
    "enabled": _p_enabled,
    "visible": _p_visible,
    "focused": _p_focused,
    "checked": _p_checked,
    "expanded": _p_expanded,
    "selected": _p_selected,
    "editable": _p_editable,
    "password": _p_password,
}


def _text_match(value, needle, exact):
    if exact:
        return (value or "") == needle
    return needle.lower() in (value or "").lower()


def _semantic_paths(root):
    """Map (control_type, name, center_x, center_y) -> ancestor path.

    The path joins the names of the semantic-tree ancestors (window,
    containers) with " › ", giving Find results their context inside
    the window. Informational only: on a key collision the first
    node's path wins.
    """
    paths: dict[tuple, str] = {}

    def walk(node, trail):
        if getattr(node, "element_type", "") in ("interactive", "scrollable"):
            center = getattr(node, "center", None)
            if center is not None:
                key = (node.control_type, node.name, center.x, center.y)
                paths.setdefault(key, " › ".join(trail))
        name = (getattr(node, "name", "") or "").strip()
        child_trail = trail + [name] if name else trail
        for child in getattr(node, "children", None) or []:
            walk(child, child_trail)

    if root is not None:
        walk(root, [])
    return paths


class Desktop:
    def __init__(self):
        self.encoding = getpreferredencoding()
        self.tree = Tree(self)
        self.desktop_state = None
        # Element-ref registry (issue #3): replaced wholesale by every
        # Snapshot capture; refs from older snapshots stop resolving.
        self._refs_generation = 0
        self._element_refs: dict[int, ElementRef] = {}
        # The tree state behind the current registry (issue #17): Find
        # reads window names, centers and static text from it, so its
        # results always describe the same capture the refs came from.
        self._refs_tree_state = None
        # Label -> snapshot node behind each ref (#18). Registration is
        # positional in wholesale mode, but drill-down merges keep
        # existing labels while appending new ones, so label order and
        # node-list order can diverge; this map stays authoritative.
        self._refs_nodes: dict[int, object] = {}

    def get_state(
        self,
        use_annotation: bool | str = True,
        use_vision: bool | str = False,
        use_dom: bool | str = False,
        use_ui_tree: bool | str = True,
        as_bytes: bool | str = False,
        scale: float = 1.0,
        grid_lines: tuple[int, int] | None = None,
        display_indices: list[int] | None = None,
        region: list[int] | tuple[int, ...] | None = None,
        max_image_size: Size | None = None,
        max_tree_elements: int | None = None,
        window_handle: int | None = None,
        tree_state_override: TreeState | None = None,
    ) -> DesktopState:
        use_annotation = use_annotation is True or (
            isinstance(use_annotation, str) and use_annotation.lower() == "true"
        )
        use_vision = use_vision is True or (
            isinstance(use_vision, str) and use_vision.lower() == "true"
        )
        use_dom = use_dom is True or (isinstance(use_dom, str) and use_dom.lower() == "true")
        use_ui_tree = use_ui_tree is True or (
            isinstance(use_ui_tree, str) and use_ui_tree.lower() == "true"
        )
        as_bytes = as_bytes is True or (isinstance(as_bytes, str) and as_bytes.lower() == "true")

        if use_dom and not use_ui_tree:
            raise ValueError("use_dom=True requires use_ui_tree=True")

        start_time = time()
        profile_enabled = _snapshot_profile_enabled()
        profile_started_at = perf_counter()
        stage_started_at = profile_started_at
        desktop_context_ms = 0.0
        tree_capture_ms = 0.0
        region_filter_ms = 0.0
        screenshot_capture_ms = 0.0
        screenshot_resize_ms = 0.0
        state_build_ms = 0.0
        displays = self.get_displays()
        available_displays = [self._display_to_view(display) for display in displays]
        region_rect = self.parse_region_selection(region)
        capture_rect = (
            region_rect
            if region_rect is not None
            else (
                self.get_display_union_rect(display_indices, displays) if display_indices else None
            )
        )
        screenshot_region = self._rect_to_bounding_box(capture_rect) if capture_rect else None

        # Fast path for Screenshot tool (use_ui_tree=False): skip window enumeration.
        # UIAutomation calls (get_controls_handles / get_windows / get_active_window)
        # can hang when an app is launching and not responding to WM messages.
        if use_ui_tree:
            controls_handles = self.get_controls_handles()  # Taskbar,Program Manager,Apps, Dialogs
            windows, windows_handles = self.get_windows(controls_handles=controls_handles)  # Apps
            active_window = self.get_active_window(windows=windows)  # Active Window
            active_window_handle = active_window.handle if active_window else None
        else:
            controls_handles = set()
            windows = []
            windows_handles = set()
            active_window = None
            active_window_handle = None

        cursor_position = self.get_cursor_location()

        try:
            active_desktop = get_current_desktop()
            all_desktops = get_all_desktops()
        except RuntimeError:
            active_desktop = {
                "id": "00000000-0000-0000-0000-000000000000",
                "name": "Default Desktop",
            }
            all_desktops = [active_desktop]

        if active_window is not None and active_window in windows:
            windows.remove(active_window)

        if profile_enabled:
            desktop_context_ms = (perf_counter() - stage_started_at) * 1000
            stage_started_at = perf_counter()

        logger.debug(f"Active window: {active_window or 'No Active Window Found'}")
        logger.debug(f"Windows: {windows}")

        if use_ui_tree and tree_state_override is not None:
            # Drill-down (#18): the subtree was captured by the caller.
            tree_state = tree_state_override
        elif use_ui_tree and window_handle is not None:
            # Window scope (#18): capture only this window's tree.
            tree_state = self.tree.get_state(
                window_handle, [], use_dom=use_dom, min_elements=max_tree_elements
            )
        elif use_ui_tree:
            other_windows_handles = set(controls_handles - windows_handles)
            if active_window_handle is not None:
                other_windows_handles.discard(active_window_handle)
            tree_active_window_handle = active_window_handle
            if screenshot_region:
                active_window_in_region = (
                    self._filter_window_to_region(active_window, screenshot_region) is not None
                )
                tree_active_window_handle = (
                    active_window_handle if active_window_in_region else None
                )
                other_windows_handles.update(
                    window.handle
                    for window in windows
                    if self._filter_window_to_region(window, screenshot_region) is not None
                )
            tree_state = self.tree.get_state(
                tree_active_window_handle,
                list(other_windows_handles),
                use_dom=use_dom,
                min_elements=max_tree_elements,
            )
        else:
            root_box = screenshot_region or self.tree.screen_box
            tree_state = TreeState(
                status=True,
                root_node=TreeElementNode(
                    name="Desktop",
                    control_type="PaneControl",
                    bounding_box=root_box,
                    center=root_box.get_center(),
                    window_name="Desktop",
                    metadata={},
                ),
            )

        if profile_enabled:
            tree_capture_ms = (perf_counter() - stage_started_at) * 1000
            stage_started_at = perf_counter()

        if screenshot_region:
            active_window = self._filter_window_to_region(active_window, screenshot_region)
            windows = self._filter_windows_to_region(windows, screenshot_region)
            if use_ui_tree:
                tree_state = self._filter_tree_state_to_region(tree_state, screenshot_region)
            if cursor_position and not self._point_in_region(cursor_position, screenshot_region):
                cursor_position = None

        if profile_enabled:
            region_filter_ms = (perf_counter() - stage_started_at) * 1000
            stage_started_at = perf_counter()

        screenshot_original_size = None
        applied_scale = None
        if use_vision:
            if use_annotation:
                nodes = tree_state.interactive_nodes
                screenshot = self.get_annotated_screenshot(
                    nodes=nodes,
                    cursor_pos=cursor_position,
                    grid_lines=grid_lines,
                    capture_rect=capture_rect,
                )
            else:
                screenshot = self.get_screenshot(capture_rect=capture_rect)

            screenshot_original_size = Size(width=screenshot.width, height=screenshot.height)

            if profile_enabled:
                screenshot_capture_ms = (perf_counter() - stage_started_at) * 1000
                stage_started_at = perf_counter()

            if max_image_size:
                scale_width = (
                    max_image_size.width / screenshot.width
                    if screenshot.width > max_image_size.width
                    else 1.0
                )
                scale_height = (
                    max_image_size.height / screenshot.height
                    if screenshot.height > max_image_size.height
                    else 1.0
                )
                scale = min(scale, scale_width, scale_height)

            applied_scale = scale
            if scale != 1.0:
                screenshot = screenshot.resize(
                    (int(screenshot.width * scale), int(screenshot.height * scale)),
                    Image.LANCZOS,
                )

            if profile_enabled:
                screenshot_resize_ms = (perf_counter() - stage_started_at) * 1000
                stage_started_at = perf_counter()

            if as_bytes:
                buffered = io.BytesIO()
                screenshot.save(buffered, format="PNG", optimize=True, compress_level=6)
                screenshot = buffered.getvalue()
                buffered.close()
        else:
            screenshot = None

        self.desktop_state = DesktopState(
            active_window=active_window,
            windows=windows,
            active_desktop=active_desktop,
            all_desktops=all_desktops,
            screenshot=screenshot,
            cursor_position=cursor_position,
            screenshot_original_size=screenshot_original_size,
            screenshot_scale=applied_scale,
            screenshot_region=screenshot_region,
            screenshot_displays=display_indices,
            available_displays=available_displays,
            tree_state=tree_state,
            screenshot_backend=getattr(self, "_last_screenshot_backend", None)
            if use_vision
            else None,
            capture_sec=time() - start_time,
        )
        if profile_enabled:
            state_build_ms = (perf_counter() - stage_started_at) * 1000
            total_profile_ms = (perf_counter() - profile_started_at) * 1000
            logger.info(
                "Snapshot profile: desktop_context_ms=%.1f tree_capture_ms=%.1f region_filter_ms=%.1f screenshot_capture_ms=%.1f screenshot_resize_ms=%.1f state_build_ms=%.1f total_ms=%.1f use_vision=%s use_dom=%s use_ui_tree=%s use_annotation=%s displays=%s",
                desktop_context_ms,
                tree_capture_ms,
                region_filter_ms,
                screenshot_capture_ms,
                screenshot_resize_ms,
                state_build_ms,
                total_profile_ms,
                use_vision,
                use_dom,
                use_ui_tree,
                use_annotation,
                display_indices,
            )
        # Log the time taken to capture the state
        end_time = time()
        logger.info(f"Desktop State capture took {end_time - start_time:.2f} seconds")
        return self.desktop_state

    def get_window_status(self, control: uia.Control) -> Status:
        if uia.IsIconic(control.NativeWindowHandle):
            return Status.MINIMIZED
        elif uia.IsZoomed(control.NativeWindowHandle):
            return Status.MAXIMIZED
        elif uia.IsWindowVisible(control.NativeWindowHandle):
            return Status.NORMAL
        else:
            return Status.HIDDEN

    def get_cursor_location(self) -> tuple[int, int]:
        return uia.GetCursorPos()

    def get_apps_from_start_menu(self) -> dict[str, str]:
        """Get installed apps. Tries Get-StartApps first, falls back to shortcut scanning."""
        command = "Get-StartApps | ConvertTo-Csv -NoTypeInformation"
        apps_info, status = PowerShellExecutor.execute_command(command)

        apps: dict[str, str] = {}
        if status == 0 and apps_info and apps_info.strip():
            try:
                reader = csv.DictReader(io.StringIO(apps_info.strip()))
                apps = {
                    row.get("Name", "").lower(): row.get("AppID", "")
                    for row in reader
                    if row.get("Name") and row.get("AppID")
                }
            except Exception as e:
                logger.warning(f"Error parsing Get-StartApps output: {e}")

        if not apps:
            # Fallback: scan Start Menu shortcut folders (works on all Windows versions)
            logger.info("Get-StartApps unavailable, falling back to Start Menu folder scan")
            apps = self._get_apps_from_shortcuts()

        # AppsFolder supplies the display name rendered for the current Windows locale.
        for name, appid in self._get_apps_folder_display_names().items():
            apps.setdefault(name, appid)
        return apps

    def _get_apps_folder_display_names(self) -> dict[str, str]:
        """Return localized Start Menu display names mapped to AppUserModelIDs."""
        command = (
            "(New-Object -ComObject Shell.Application)."
            "NameSpace('shell:::{4234d49b-0245-4df3-b780-3893943456e1}').Items() "
            "| Select-Object Name,@{n='AppID';e={$_.Path}} | ConvertTo-Csv -NoTypeInformation"
        )
        apps_info, status = PowerShellExecutor.execute_command(command)

        if status == 0 and apps_info and apps_info.strip():
            try:
                reader = csv.DictReader(io.StringIO(apps_info.strip()))
                return {
                    row.get("Name", "").lower(): row.get("AppID", "")
                    for row in reader
                    if row.get("Name") and row.get("AppID")
                }
            except Exception as e:
                logger.warning(f"Error parsing AppsFolder display names: {e}")
        return {}

    def _get_apps_from_shortcuts(self) -> dict[str, str]:
        """Scan Start Menu folders for .lnk shortcuts as a fallback for Get-StartApps."""
        import glob

        apps = {}
        start_menu_paths = [
            os.path.join(
                os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                r"Microsoft\Windows\Start Menu\Programs",
            ),
            os.path.join(
                os.environ.get("APPDATA", ""),
                r"Microsoft\Windows\Start Menu\Programs",
            ),
        ]
        for base_path in start_menu_paths:
            if not os.path.isdir(base_path):
                continue
            for lnk_path in glob.glob(os.path.join(base_path, "**", "*.lnk"), recursive=True):
                name = os.path.splitext(os.path.basename(lnk_path))[0].lower()
                if name and name not in apps:
                    apps[name] = lnk_path
        return apps

    def execute_command(
        self, command: str, timeout: int = 10, shell: str | None = None
    ) -> tuple[str, int]:
        return PowerShellExecutor.execute_command(command, timeout, shell)

    def is_window_browser(self, node: uia.Control):
        """Give any node of the app and it will return True if the app is a browser, False otherwise."""
        try:
            process = Process(node.ProcessId)
            return Browser.has_process(process.name())
        except Exception:
            return False

    def _find_window_by_name(
        self, name: str, refresh_state: bool = False
    ) -> tuple["Window | None", str]:
        """Find a window by fuzzy name match. Returns (window, error_msg).
        If the returned window is None, error_msg describes the failure reason.

        If refresh_state is True, always refresh desktop_state before searching;
        otherwise refresh only when desktop_state is absent or empty.
        """
        if refresh_state or self.desktop_state is None or not self.desktop_state.windows:
            self.get_state()
        if self.desktop_state is None:
            return None, "Failed to get desktop state. Please try again."

        window_list = [
            w
            for w in [self.desktop_state.active_window] + (self.desktop_state.windows or [])
            if w is not None
        ]
        if not window_list:
            return None, "No windows found on the desktop."

        windows = {window.name: window for window in window_list}
        matched_window = process.extractOne(name, list(windows.keys()), score_cutoff=70)
        if matched_window is None:
            return None, f"Application {name.title()} not found."
        window_name, _ = matched_window
        return windows.get(window_name), ""

    def resize_app(
        self, name: str | None = None, size: tuple[int, int] = None, loc: tuple[int, int] = None
    ) -> tuple[str, int]:
        if name is not None:
            target_window, error = self._find_window_by_name(name, refresh_state=True)
            if target_window is None:
                return error, 1
        else:
            # If no name provided, try to resize the active window
            target_window = self.desktop_state.active_window if self.desktop_state else None

            if target_window is None:
                return "No active window found", 1

        # target_window is guaranteed to be non-None here
        if target_window.status == Status.MINIMIZED:
            return f"{target_window.name} is minimized", 1
        elif target_window.status == Status.MAXIMIZED:
            return f"{target_window.name} is maximized", 1
        else:
            window_control = uia.ControlFromHandle(target_window.handle)
            if loc is None:
                x = window_control.BoundingRectangle.left
                y = window_control.BoundingRectangle.top
                loc = (x, y)
            if size is None:
                width = window_control.BoundingRectangle.width()
                height = window_control.BoundingRectangle.height()
                size = (width, height)
            x, y = loc
            width, height = size
            window_control.MoveWindow(x, y, width, height)
            return (f"{target_window.name} resized to {width}x{height} at {x},{y}.", 0)

    def app(
        self,
        mode: Literal["launch", "switch", "resize"],
        name: str | None = None,
        loc: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
    ):
        match mode:
            case "launch":
                response, status, pid = self.launch_app(name)
                if status != 0:
                    return response

                # Smart wait using UIA Exists (avoids manual Python loops)
                launched = False
                if pid > 0:
                    if uia.WindowControl(ProcessId=pid).Exists(maxSearchSeconds=10):
                        launched = True

                if not launched:
                    # Fallback: Regex search for the window title
                    safe_name = re.escape(name)
                    if uia.WindowControl(RegexName=f"(?i).*{safe_name}.*").Exists(
                        maxSearchSeconds=10
                    ):
                        launched = True

                if launched:
                    return f"{name.title()} launched."
                return f"Launching {name.title()} sent, but window not detected yet."
            case "resize":
                response, status = self.resize_app(name=name, size=size, loc=loc)
                if status != 0:
                    return response
                else:
                    return response
            case "switch":
                response, status = self.switch_app(name)
                if status != 0:
                    raise ValueError(response)
                else:
                    return response

    def _check_app_exists(self, app_id: str) -> bool:
        """Check if an app with the given AppID exists in shell:AppsFolder."""
        safe_app_id = ps_quote(app_id)
        command = (
            f"$folder = (New-Object -ComObject Shell.Application).NameSpace('shell:AppsFolder'); "
            f"if ($folder) {{ [bool]$folder.ParseName({safe_app_id}) }} else {{ $false }}"
        )
        response, status = PowerShellExecutor.execute_command(command)
        return status == 0 and response.strip().lower() == "true"

    def launch_app(self, name: str) -> tuple[str, int, int]:
        apps_map = self.get_apps_from_start_menu()
        matched_app = process.extractOne(name, apps_map.keys(), score_cutoff=70)
        if matched_app is None:
            return (f"{name.title()} not found in start menu.", 1, 0)
        app_name, _ = matched_app
        appid = apps_map.get(app_name)
        if appid is None:
            return (f"{name.title()} not found in start menu.", 1, 0)

        pid = 0
        if os.path.exists(appid) or "\\" in appid:
            exe_path = resolve_known_folder_guid_path(appid)
            safe_exe_path = ps_quote(exe_path)
            command = f"Start-Process {safe_exe_path} -PassThru | Select-Object -ExpandProperty Id"
            response, status = PowerShellExecutor.execute_command(command)
            if status == 0 and response.strip().isdigit():
                pid = int(response.strip())
        else:
            if not self._check_app_exists(appid):
                return (f"Invalid app identifier: {appid}", 1, 0)

            safe = ps_quote(f"shell:AppsFolder\\{appid}")
            command = f"Start-Process {safe}"
            response, status = PowerShellExecutor.execute_command(command)

        return response, status, pid

    def switch_app(self, name: str):
        try:
            window, error = self._find_window_by_name(name)
            if window is None:
                return error, 1

            target_handle = window.handle

            was_minimized = uia.IsIconic(target_handle)
            self.bring_window_to_top(target_handle)
            # Windows can deny activation without raising; verify the foreground HWND.
            deadline = perf_counter() + 1.0
            while win32gui.GetForegroundWindow() != target_handle:
                if perf_counter() >= deadline:
                    return (
                        f"Failed to bring {window.name} to the foreground within 1 second.",
                        1,
                    )
                sleep(0.05)
            if was_minimized:
                content = f"Restored {window.name.title()} from minimized and switched to it."
            else:
                content = f"Switched to {window.name.title()} window."
            return content, 0
        except Exception as e:
            return (f"Error switching app: {str(e)}", 1)

    def bring_window_to_top(self, target_handle: int):
        if not win32gui.IsWindow(target_handle):
            raise ValueError("Invalid window handle")

        try:
            if win32gui.IsIconic(target_handle):
                win32gui.ShowWindow(target_handle, win32con.SW_RESTORE)

            foreground_handle = win32gui.GetForegroundWindow()

            # Validate both handles before proceeding
            if not win32gui.IsWindow(foreground_handle):
                # No valid foreground window, just try to set target as foreground
                win32gui.SetForegroundWindow(target_handle)
                win32gui.BringWindowToTop(target_handle)
                return

            # We attach our own thread (current_tid) to both the foreground and
            # target window threads to make focus change succeed.
            #
            # Simply attaching foreground_thread to target_thread is not sufficient:
            # SetForegroundWindow is called from our MCP thread, and Windows requires
            # the calling process to satisfy the "received the last input event"
            # criterion. Without attaching current_tid, the system may only bring the
            # target window to the front but refuse to transfer keyboard
            # focus, causing subsequent keyboard input to remain in the previous window.
            #
            # By attaching current_tid to both threads, our thread shares
            # their input state and inherits that eligibility, allowing the system to
            # grant the focus switch.
            foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground_handle)
            target_thread, _ = win32process.GetWindowThreadProcessId(target_handle)
            current_tid = ctypes.windll.kernel32.GetCurrentThreadId()

            if not foreground_thread or not target_thread or foreground_thread == target_thread:
                win32gui.SetForegroundWindow(target_handle)
                win32gui.BringWindowToTop(target_handle)
                return

            ctypes.windll.user32.AllowSetForegroundWindow(-1)

            attached_threads = []
            try:
                for thread in (foreground_thread, target_thread):
                    if thread and thread != current_tid:
                        try:
                            win32process.AttachThreadInput(current_tid, thread, True)
                            attached_threads.append(thread)
                        except Exception as e:
                            # AttachThreadInput fails with Access Denied for elevated
                            # processes (e.g. Settings, Task Manager). Skip the attach
                            # and still attempt SetForegroundWindow below.
                            logger.debug(
                                f"AttachThreadInput failed for thread {thread} "
                                f"(likely elevated process), skipping: {e}"
                            )

                win32gui.SetForegroundWindow(target_handle)
                win32gui.BringWindowToTop(target_handle)

                win32gui.SetWindowPos(
                    target_handle,
                    win32con.HWND_TOP,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )

            finally:
                for tid in reversed(attached_threads):
                    win32process.AttachThreadInput(current_tid, tid, False)

        except Exception as e:
            logger.exception(f"Failed to bring window to top: {e}")

    def get_coordinates_from_label(self, label: int) -> tuple[int, int]:
        tree_state = self.desktop_state.tree_state
        if label < 0:
            raise IndexError(f"Label {label} out of range")
        if label < len(tree_state.interactive_nodes):
            element_node = tree_state.interactive_nodes[label]
        else:
            scroll_idx = label - len(tree_state.interactive_nodes)
            if scroll_idx < len(tree_state.scrollable_nodes):
                element_node = tree_state.scrollable_nodes[scroll_idx]
            else:
                raise IndexError(f"Label {label} out of range")
        return element_node.center.x, element_node.center.y

    def get_coordinates_from_labels(self, labels: list[int]) -> list[tuple[int, int]]:
        """Resolve multiple UI element labels to screen coordinates in bulk."""
        tree_state = self.desktop_state.tree_state
        interactive_nodes = tree_state.interactive_nodes
        scrollable_nodes = tree_state.scrollable_nodes
        interactive_len = len(interactive_nodes)

        results = []
        for label in labels:
            if label < 0:
                raise IndexError(f"Label {label} out of range")
            if label < interactive_len:
                element_node = interactive_nodes[label]
            else:
                scroll_idx = label - interactive_len
                if scroll_idx < len(scrollable_nodes):
                    element_node = scrollable_nodes[scroll_idx]
                else:
                    raise IndexError(f"Label {label} out of range")
            results.append((element_node.center.x, element_node.center.y))
        return results

    def find_window_candidates(self, query: str) -> list[dict]:
        """Top-level windows matching a title substring or process name (#18).

        Returns [{"handle", "title", "process"}] in enumeration order.
        The caller decides what zero results (no such window) or several
        (ambiguous — never guess) mean; this never picks one itself.
        """
        query = (query or "").strip().lower()
        if not query:
            return []
        controls_handles = self.get_controls_handles()
        windows, _handles = self.get_windows(controls_handles=controls_handles)
        active = self.get_active_window(windows=windows)
        if active is not None and active not in windows:
            windows = [active, *windows]
        candidates = []
        seen: set[int] = set()
        for window in windows:
            handle = window.handle
            if handle in seen:
                continue
            seen.add(handle)
            title = window.name or ""
            process = _window_process_name(handle) or ""
            if (
                query in title.lower()
                or query == process.lower()
                or query == process.lower().removesuffix(".exe")
            ):
                candidates.append(
                    {"handle": handle, "title": title, "process": process}
                )
        return candidates

    # ---- Element refs (issue #3) ----

    @property
    def refs_generation(self) -> int:
        return self._refs_generation

    def register_element_refs(self, tree_state, *, extend: bool = False) -> int:
        """Snapshot the identity of every labelled element for ref tools.

        Called by the Snapshot tool on each capture; replaces the previous
        registry wholesale, so refs issued by an older snapshot stop
        resolving (callers get STALE_REF and re-snapshot).

        With ``extend=True`` (drill-down captures, #18) the new nodes are
        instead merged into the current registry: an element the parent
        snapshot already labelled keeps its label, new elements append,
        and the generation — the identity scope — is unchanged, so refs
        from the parent snapshot and the drill-down stay valid together.
        """
        interactive = list(getattr(tree_state, "interactive_nodes", []) or [])
        scrollable = list(getattr(tree_state, "scrollable_nodes", []) or [])
        nodes = interactive + scrollable
        merged = self._merged_ref_nodes(nodes) if extend and self._element_refs else None
        if merged is not None:
            nodes = merged
        else:
            self._refs_generation += 1
        # Identity evidence per element (#12): the AutomationId, and whether
        # the (window, control type, AutomationId, name) predicate was
        # unique in the window at capture time.
        prepared = []
        groups: dict[tuple, int] = {}
        for label, node in enumerate(nodes):
            metadata = getattr(node, "metadata", None) or {}
            automation_id = str(metadata.get("automation_id") or "")
            key = (
                getattr(node, "window_handle", 0),
                node.control_type,
                automation_id,
                node.name,
            )
            groups[key] = groups.get(key, 0) + 1
            prepared.append((label, node, metadata, automation_id, key))
        identity_cache: dict[int, tuple[int, float | None]] = {}
        refs: dict[int, ElementRef] = {}
        for label, node, metadata, automation_id, key in prepared:
            handle = getattr(node, "window_handle", 0)
            if handle not in identity_cache:
                identity_cache[handle] = _window_identity(handle)
            pid, token = identity_cache[handle]
            refs[label] = ElementRef(
                label=label,
                runtime_id=getattr(node, "runtime_id", None),
                window_handle=handle,
                name=node.name,
                control_type=node.control_type,
                is_password=bool(metadata.get("is_password")),
                automation_id=automation_id,
                description=str(metadata.get("help_text") or ""),
                window_pid=pid,
                window_process_token=token,
                unique_at_capture=groups[key] == 1,
            )
        self._element_refs = refs
        self._refs_nodes = {label: node for label, node in enumerate(nodes)}
        self._refs_tree_state = tree_state
        return self._refs_generation

    def _merged_ref_nodes(self, new_nodes) -> list | None:
        """Node list for an extend registration (#18), or None to fall back.

        The current registry's nodes in label order, with the new
        capture's nodes merged in: a node whose (window handle,
        RuntimeId) matches an existing ref takes that ref's slot — the
        fresher capture of the same element — and genuinely new nodes
        append in encounter order. Returns None when the tracked nodes
        do not cover the registry (not reachable through the public
        API), where merging would corrupt the label mapping.
        """
        tracked = getattr(self, "_refs_nodes", None) or {}
        if len(tracked) != len(self._element_refs):
            return None
        existing_by_identity = {}
        for label, ref in self._element_refs.items():
            if ref.runtime_id is not None:
                existing_by_identity[(ref.window_handle, tuple(ref.runtime_id))] = label
        merged: dict[int, object] = dict(tracked)
        next_label = max(merged) + 1 if merged else 0
        for node in new_nodes:
            runtime_id = getattr(node, "runtime_id", None)
            label = None
            if runtime_id is not None:
                label = existing_by_identity.get(
                    (getattr(node, "window_handle", 0), tuple(runtime_id))
                )
            if label is None:
                label = next_label
                next_label += 1
            merged[label] = node
        return [merged[label] for label in sorted(merged)]

    def resolve_element_ref(self, label: int, snapshot: int | None = None):
        """Re-acquire the live UIA control behind a snapshot ref.

        Raises StaleRefError when the ref's snapshot was superseded, the
        element has no re-acquirable identity, its window is gone, the
        element itself no longer exists, or it now shows a different name
        than the snapshot recorded — in every case the safe recovery is a
        fresh Snapshot, never a blind coordinate click.
        """
        if snapshot is not None and snapshot != self._refs_generation:
            raise StaleRefError(
                f"STALE_REF: ref is from snapshot {snapshot}, but the current snapshot "
                f"is {self._refs_generation}. Take a new Snapshot and use its refs."
            )
        ref = self._element_refs.get(label)
        if ref is None:
            raise StaleRefError(
                f"STALE_REF: no element with label {label} in snapshot "
                f"{self._refs_generation}. Take a new Snapshot."
            )
        if (ref.runtime_id is None and not ref.automation_id) or not ref.window_handle:            raise StaleRefError(
                f"STALE_REF: element {label} ({ref.control_type} \"{ref.name}\") has no UIA "
                "identity to re-acquire (extracted text or IA2/MSAA node). "
                "Use Click with its coordinates instead."
            )
        root = uia.ControlFromHandle(ref.window_handle)
        if root is None:
            raise StaleRefError(
                f"STALE_REF: the window containing element {label} no longer exists. "
                "Take a new Snapshot."
            )
        self._verify_window_identity(ref)
        control = None
        if ref.runtime_id is not None:
            control = find_control_by_runtime_id(root, ref.runtime_id)
        if control is not None:
            try:
                live_name = (control.Name or "").strip()
            except Exception:
                live_name = ref.name
            if ref.name and live_name != ref.name:
                raise StaleRefError(
                    f"STALE_REF: element {label} now shows \"{live_name}\" instead of "
                    f"\"{ref.name}\" — the UI changed since the snapshot. Take a new Snapshot."
                )
            return ref, control
        # The RuntimeId no longer resolves — the element was probably
        # re-rendered under a fresh one. Fall back to counting the identity
        # predicate over the live window (#12): exactly one match resolves,
        # zero is stale, two or more is ambiguous, and a field that cannot
        # be read leaves the count unproven instead of guessed.
        if not ref.unique_at_capture:
            raise AmbiguousRefError(
                f"AMBIGUOUS_TARGET: element {label} ({ref.control_type} \"{ref.name}\") "
                "was not the only element with its identity in the snapshot, and "
                "its RuntimeId no longer resolves, so no search can pick it out "
                "safely. Take a new Snapshot; if it is still ambiguous, use "
                "Click with its coordinates."
            )
        matches, unproven = find_controls_by_identity(
            root,
            control_type=ref.control_type,
            automation_id=ref.automation_id,
            name=ref.name,
        )
        if len(matches) == 1:
            return ref, matches[0]
        if len(matches) > 1:
            raise AmbiguousRefError(
                f"AMBIGUOUS_TARGET: {len(matches)} live elements match element "
                f"{label}'s identity ({ref.control_type} \"{ref.name}\"). "
                "Refusing to guess. Take a new Snapshot."
            )
        if unproven:
            raise TimeoutError(
                f"RESOLUTION_UNKNOWN: element {label}'s identity could not be "
                "proven — a field needed for matching could not be read from "
                "the live window. Take a new Snapshot."
            )
        raise StaleRefError(
            f"STALE_REF: element {label} ({ref.control_type} \"{ref.name}\") no longer "
            "exists — the UI changed since the snapshot. Take a new Snapshot."
        )

    def _verify_window_identity(self, ref: ElementRef) -> None:
        """Fail closed when the ref's window handle changed owner (#12).

        HWNDs are recycled: after the original window closes, the same
        handle can belong to a different window, possibly in a restarted
        process. The ref records the owning pid and the process
        creation-time token at capture; a proven mismatch means the handle
        no longer names the window the snapshot saw. Unknown values (never
        captured, or unreadable now) prove nothing and do not block.
        """
        if not ref.window_pid and ref.window_process_token is None:
            return
        pid, token = _window_identity(ref.window_handle)
        if ref.window_pid and pid and pid != ref.window_pid:
            raise StaleRefError(
                f"STALE_REF: the window containing element {ref.label} is now "
                f"owned by a different process (pid {pid}, was {ref.window_pid}) "
                "— the handle was recycled. Take a new Snapshot."
            )
        if (
            ref.window_process_token is not None
            and token is not None
            and abs(token - ref.window_process_token) > 0.01
        ):
            raise StaleRefError(
                f"STALE_REF: the process owning element {ref.label}'s window "
                "restarted since the snapshot (its creation-time token "
                "changed) — the handle was recycled. Take a new Snapshot."
            )

    # ---- Snapshot queries (issue #17) ----

    # Find results are a query answer, not a capture: cap the list and
    # report the true total so a broad query cannot flood the caller.
    _FIND_LIMIT = 100

    def find_elements(
        self,
        *,
        text: str | None = None,
        exact: bool = False,
        role: str | None = None,
        automation_id: str | None = None,
        window: str | None = None,
        snapshot: int | None = None,
    ) -> dict:
        """Search the current snapshot's registry without re-capturing.

        Actionable matches carry their global snapshot label (the same
        numbering the paged Snapshot output uses, #7) so they can be
        passed straight to Invoke/SetValue/Toggle/Click. Static text
        matches (DOM informative nodes) have no element behind them and
        are returned separately with no label. Name substring matching
        is case-insensitive; exact matching is a full-string,
        case-sensitive equality.
        """
        if snapshot is not None and snapshot != self._refs_generation:
            raise StaleRefError(
                f"STALE_REF: snapshot {snapshot} was superseded; the current "
                f"snapshot is {self._refs_generation}. Take a new Snapshot."
            )
        result = {
            "generation": self._refs_generation,
            "has_snapshot": self._refs_generation > 0,
            "matches": [],
            "static_matches": [],
            "total_matches": 0,
            "total_static": 0,
            "truncated": False,
        }
        tree_state = getattr(self, "_refs_tree_state", None)
        interactive = list(getattr(tree_state, "interactive_nodes", []) or [])
        scrollable = list(getattr(tree_state, "scrollable_nodes", []) or [])
        paths = _semantic_paths(getattr(tree_state, "semantic_tree_root", None))
        nodes_by_label = getattr(self, "_refs_nodes", None) or {}

        def node_for(label):
            node = nodes_by_label.get(label)
            if node is not None:
                return node
            if 0 <= label < len(interactive):
                return interactive[label]
            idx = label - len(interactive)
            if 0 <= idx < len(scrollable):
                return scrollable[idx]
            return None

        for label in sorted(self._element_refs):
            ref = self._element_refs[label]
            if role and ref.control_type.lower() != role.lower():
                continue
            if automation_id is not None and ref.automation_id != automation_id:
                continue
            if text is not None and not _text_match(ref.name, text, exact):
                continue
            node = node_for(label)
            window_name = getattr(node, "window_name", "") if node else ""
            if window and window.lower() not in window_name.lower():
                continue
            center = getattr(node, "center", None) if node else None
            path = None
            if center is not None:
                path = paths.get(
                    (ref.control_type, ref.name, center.x, center.y)
                )
            result["total_matches"] += 1
            if len(result["matches"]) < self._FIND_LIMIT:
                result["matches"].append(
                    {
                        "label": label,
                        "control_type": ref.control_type,
                        "name": ref.name,
                        "automation_id": ref.automation_id,
                        "window_name": window_name,
                        "center": (center.x, center.y) if center else None,
                        "path": path,
                    }
                )
        if text is not None and not role and automation_id is None and not window:
            for tnode in getattr(tree_state, "dom_informative_nodes", []) or []:
                static_text = getattr(tnode, "text", "") or ""
                if _text_match(static_text, text, exact):
                    result["total_static"] += 1
                    if len(result["static_matches"]) < self._FIND_LIMIT:
                        result["static_matches"].append(static_text)
        result["truncated"] = (
            result["total_matches"] > len(result["matches"])
            or result["total_static"] > len(result["static_matches"])
        )
        return result

    def get_ref_property(
        self, label: int, property_name: str, snapshot: int | None = None
    ):
        """Read one live property of a ref: (ref, known, value) (#17).

        Resolution goes through the strict resolver (#12), so a stale
        or ambiguous ref raises instead of returning a wrong element's
        value. known=False means the property is not exposed or the
        read failed — the caller reports "unknown", never a guess.
        """
        key = str(property_name or "").strip().lower()
        reader = _QUERY_READERS.get(key)
        if reader is None:
            raise ValueError(
                f"Unknown property {property_name!r}. Supported properties: "
                + ", ".join(sorted(_QUERY_READERS))
                + "."
            )
        ref, control = self.resolve_element_ref(label, snapshot)
        try:
            known, value = reader(ref, control)
        except Exception:
            known, value = False, None
        return ref, known, value

    def evaluate_ref_predicate(
        self, label: int, predicate: str, snapshot: int | None = None
    ):
        """Evaluate one predicate on a ref: (ref, verdict) (#17).

        verdict is True/False, or None when the live read cannot
        decide (no pattern, provider error) — reported as "unknown".
        """
        key = str(predicate or "").strip().lower()
        check = _QUERY_PREDICATES.get(key)
        if check is None:
            raise ValueError(
                f"Unknown predicate {predicate!r}. Supported predicates: "
                + ", ".join(sorted(_QUERY_PREDICATES))
                + "."
            )
        ref, control = self.resolve_element_ref(label, snapshot)
        try:
            verdict = check(ref, control)
        except Exception:
            verdict = None
        return ref, verdict if isinstance(verdict, bool) else None

    # Poll interval for the actionability battery (#16).
    _ACTIONABILITY_POLL = 0.05

    def _await_actionable(self, ref, control, *, timeout: float) -> None:
        """Auto-wait until a resolved element is actionable (#16).

        The transient checks — visible (not offscreen), enabled, bounding
        rect stable across two reads — are polled until they pass or the
        budget expires; the expiry error names the check that never
        passed. Terminal problems (unsupported pattern, read-only value,
        policy denial, stale ref) are ruled out by the caller *before*
        this runs, so they never consume the budget. A property a control
        does not expose reads as passing (getattr default); a property
        whose read raises counts as "unreadable" and is polled like any
        other transient state. ``timeout=0`` restores the pre-#16
        behaviour: dispatch immediately, no battery at all.
        """
        try:
            budget = float(timeout)
        except (TypeError, ValueError):
            raise ValueError("wait_timeout must be a number of seconds") from None
        if budget < 0 or budget > 30:
            raise ValueError("wait_timeout must be between 0 and 30 seconds")
        if budget == 0:
            return
        deadline = perf_counter() + budget
        last_failure = "stable"
        previous_rect = None
        rect_reads = 0
        while True:
            failure = None
            try:
                if getattr(control, "IsOffscreen", False):
                    failure = "visible"
                elif not getattr(control, "IsEnabled", True):
                    failure = "enabled"
                else:
                    rect = _rect_tuple(control)
                    if rect is not None:
                        rect_reads += 1
                        if previous_rect is not None and rect != previous_rect:
                            failure = "stable"
                        previous_rect = rect
                        if failure is None and rect_reads < 2:
                            # Stability needs a confirming second read.
                            failure = "stable"
            except Exception:
                failure = "unreadable"
            if failure is None:
                return
            last_failure = failure
            if perf_counter() >= deadline:
                raise TimeoutError(
                    f"ACTIONABILITY_TIMEOUT: element {ref.label} "
                    f"({ref.control_type} \"{ref.name}\") never became actionable "
                    f"within {budget:.1f}s — failing check: {last_failure}. "
                    "Observe the app and retry, or pass wait_timeout=0 to "
                    "dispatch immediately."
                )
            sleep(self._ACTIONABILITY_POLL)

    def invoke_element(self, label: int, snapshot: int | None = None, wait_timeout: float = 3.0) -> ElementRef:
        owner = get_controller()
        owner.checkpoint_current()
        ref, control = self.resolve_element_ref(label, snapshot)
        self._check_uipi_control(control, "InvokePattern")
        pattern = control.GetPattern(uia.PatternId.InvokePattern)
        if pattern is None:
            raise ValueError(
                f"UNSUPPORTED_PATTERN: element {label} ({ref.control_type} \"{ref.name}\") "
                "does not support InvokePattern. Use Click with its coordinates instead."
            )
        self._await_actionable(ref, control, timeout=wait_timeout)
        owner.checkpoint_current()
        if pattern.Invoke() is False:
            raise RuntimeError(f"InvokePattern.Invoke failed for element {label}")
        owner.record_step_current()
        return ref

    def set_element_value(
        self, label: int, value: str, snapshot: int | None = None, wait_timeout: float = 3.0
    ) -> ElementRef:
        owner = get_controller()
        owner.checkpoint_current()
        ref, control = self.resolve_element_ref(label, snapshot)
        self._check_uipi_control(control, "ValuePattern")
        pattern = control.GetPattern(uia.PatternId.ValuePattern)
        legacy = None
        if pattern is None:
            legacy = control.GetPattern(uia.PatternId.LegacyIAccessiblePattern)
        if pattern is None and legacy is None:
            raise ValueError(
                f"UNSUPPORTED_PATTERN: element {label} ({ref.control_type} \"{ref.name}\") "
                "supports neither ValuePattern nor LegacyIAccessible. "
                "Use the Type tool with its coordinates instead."
            )
        if pattern is not None:
            # Editability is a terminal check (#16): a read-only field does
            # not become writable by waiting, so fail before the battery.
            try:
                readonly = bool(pattern.IsReadOnly)
            except Exception:
                readonly = False
            if readonly:
                raise ValueError(
                    f"NOT_EDITABLE: element {label} ({ref.control_type} \"{ref.name}\") "
                    "is read-only; its value cannot be set."
                )
        self._await_actionable(ref, control, timeout=wait_timeout)
        owner.checkpoint_current()
        try:
            ok = pattern.SetValue(value) if pattern is not None else legacy.SetValue(value)
        except Exception as e:
            raise RuntimeError(f"Setting the value of element {label} failed: {e}") from e
        if ok is False:
            raise RuntimeError(f"Setting the value of element {label} was rejected")
        owner.record_step_current()
        return ref

    def toggle_element(self, label: int, snapshot: int | None = None, wait_timeout: float = 3.0) -> tuple[ElementRef, object]:
        owner = get_controller()
        owner.checkpoint_current()
        ref, control = self.resolve_element_ref(label, snapshot)
        self._check_uipi_control(control, "TogglePattern")
        pattern = control.GetPattern(uia.PatternId.TogglePattern)
        if pattern is None:
            raise ValueError(
                f"UNSUPPORTED_PATTERN: element {label} ({ref.control_type} \"{ref.name}\") "
                "does not support TogglePattern. Use Click with its coordinates instead."
            )
        self._await_actionable(ref, control, timeout=wait_timeout)
        owner.checkpoint_current()
        if pattern.Toggle() is False:
            raise RuntimeError(f"TogglePattern.Toggle failed for element {label}")
        owner.record_step_current()
        try:
            state = pattern.ToggleState
        except Exception:
            state = None
        return ref, state

    @staticmethod
    def _validate_screen_point(x: int, y: int) -> None:
        """Reject a point no window can receive input at.

        SetCursorPos and absolute mouse events both clamp an out-of-range
        point to the nearest reachable pixel, so input aimed past the edge of
        the desktop lands somewhere else entirely while the tool still reports
        success. Bounds come from the virtual desktop rather than the primary
        monitor, so a secondary monitor left of or above the primary one keeps
        its legitimate negative coordinates.

        Raises:
            ValueError: If (x, y) falls outside the virtual desktop.
        """
        left, top, width, height = uia.GetVirtualScreenRect()
        right = left + width - 1
        bottom = top + height - 1
        if not (left <= x <= right and top <= y <= bottom):
            raise ValueError(
                f"Coordinates ({x},{y}) are outside the desktop bounds "
                f"x={left}..{right}, y={top}..{bottom}"
            )

    # ---- UIPI pre-check (issue #14, findings W-002) ----
    # SendInput reports success even when UIPI blocks delivery to a
    # higher-integrity window, so every input path first proves the target
    # process does not outrank this server. Resolution failures (no window
    # at the point, an unreadable pid) never block by themselves: only the
    # integrity comparison inside check_target_process can deny, and it
    # raises UipiBlockedError outside these try blocks.

    def _check_uipi_point(self, x: int, y: int, action: str) -> None:
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            if not hwnd:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        uia_integrity.check_target_process(pid, action)

    def _check_uipi_foreground(self, action: str) -> None:
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        uia_integrity.check_target_process(pid, action)

    def _check_uipi_control(self, control, action: str) -> None:
        uia_integrity.check_target_process(getattr(control, "ProcessId", None), action)

    def click(self, loc: tuple[int, int] | list[int], button: str = "left", clicks: int = 1):
        owner = get_controller()
        owner.checkpoint_current()
        if isinstance(loc, list):
            x, y = loc[0], loc[1]
        else:
            x, y = loc
        self._validate_screen_point(x, y)
        if clicks == 0:
            owner.checkpoint_current()
            uia.SetCursorPos(x, y)
            return
        self._check_uipi_point(x, y, "Click")
        match button:
            case "left":
                if clicks >= 2:
                    dbl_wait = uia.GetDoubleClickTime() / 2000.0
                    for i in range(clicks):
                        owner.checkpoint_current()
                        uia.Click(x, y, waitTime=dbl_wait if i < clicks - 1 else 0.5)
                        owner.record_step_current()
                else:
                    owner.checkpoint_current()
                    uia.Click(x, y)
                    owner.record_step_current()
            case "right":
                for _ in range(clicks):
                    owner.checkpoint_current()
                    uia.RightClick(x, y)
                    owner.record_step_current()
            case "middle":
                for _ in range(clicks):
                    owner.checkpoint_current()
                    uia.MiddleClick(x, y)
                    owner.record_step_current()

    # Strings longer than this typed via clipboard paste instead of
    # per-key SendKeys. SendKeys at high cadence loses keystrokes on
    # slower / loaded systems (Win11 VMs in particular) — observed
    # "hello from agentic-windows-mcp drive test" rendering as
    # "hello tttttttttttttttttttttttttt" on a 4-core Win11 VM. The
    # paste path is reliable because it bypasses the scan-code queue
    # entirely. Plain-text only; control chars route through SendKeys
    # so escape sequences ({Enter}, {Tab}, …) still work.
    _LONG_TEXT_PASTE_THRESHOLD = 20

    # The SendKeys path types in chunks with a short pause between them.
    # A flat-out keystroke stream overflows slower targets' input queues
    # and silently drops characters — observed on multi-hundred-character
    # code snippets full of braces/newlines (which cannot take the paste
    # path) typed into a browser DevTools console (#10).
    _TYPE_CHUNK_SIZE = 48
    _TYPE_CHUNK_PAUSE = 0.08

    def type(
        self,
        loc: tuple[int, int],
        text: str,
        caret_position: Literal["start", "idle", "end"] = "idle",
        clear: bool | str = False,
        press_enter: bool | str = False,
        verify: bool | str = False,
    ):
        clear = as_bool(clear, "clear")
        press_enter = as_bool(press_enter, "press_enter")
        verify = as_bool(verify, "verify")
        owner = get_controller()
        owner.checkpoint_current()
        x, y = loc
        self._validate_screen_point(x, y)
        self._check_uipi_point(x, y, "Type")
        uia.Click(x, y)
        owner.record_step_current()
        # Secure fields (#15): when the click landed in a password field, the
        # value is never read back (no baseline, no verification read) and
        # the caller is told not to echo the typed text in its result.
        secure_target = self._focused_is_password()
        if caret_position == "start":
            owner.checkpoint_current()
            uia.SendKeys("{Home}", waitTime=0.05)
            owner.record_step_current()
        elif caret_position == "end":
            owner.checkpoint_current()
            uia.SendKeys("{End}", waitTime=0.05)
            owner.record_step_current()
        baseline = self._read_focused_text() if verify and not secure_target else None
        if clear:
            sleep(0.5)
            owner.checkpoint_current()
            uia.SendKeys("{Ctrl}a", waitTime=0.05)
            owner.record_step_current()
            owner.checkpoint_current()
            uia.SendKeys("{Back}", waitTime=0.05)
            owner.record_step_current()
        # Per-key SendKeys for short text (so escape sequences keep working);
        # clipboard paste for long text (so the scan-code queue can't race).
        has_control_chars = any(c in text for c in ("\n", "\t", "{", "}"))
        if len(text) >= self._LONG_TEXT_PASTE_THRESHOLD and not has_control_chars:
            self._paste_text(text)
        else:
            # Bump interval from 0.02 → 0.04. Keeps short-text speed acceptable
            # while reducing key-loss; each character is a cancellation point.
            for index, ch in enumerate(text):
                owner.checkpoint_current()
                uia.SendKeys(_escape_text_for_sendkeys(ch), interval=0.04, waitTime=0.05)
                owner.record_step_current()
                if (index + 1) % self._TYPE_CHUNK_SIZE == 0 and index + 1 < len(text):
                    sleep(self._TYPE_CHUNK_PAUSE)
        verification = None
        if verify:
            if secure_target:
                verification = (
                    "unavailable",
                    "the target is a secure (password) field; its value is "
                    "never read back, so the typed text cannot be verified",
                )
            else:
                verification = _assess_typed_text(
                    baseline,
                    text,
                    self._read_focused_text(),
                    clear=clear,
                    caret_position=caret_position,
                )
        if press_enter:
            owner.checkpoint_current()
            uia.SendKeys("{Enter}", waitTime=0.05)
            owner.record_step_current()
        return verification, secure_target

    def _paste_text(self, text: str):
        """Stash text on the clipboard, Ctrl+V, restore prior clipboard.
        Plain-text only — control chars (newlines, tabs, braces) need to
        route through SendKeys instead so escape sequences are honored.
        """
        prior = None
        owner = get_controller()
        owner.checkpoint_current()
        try:
            prior = uia.GetClipboardText()
        except Exception:
            pass
        uia.SetClipboardText(text)
        # Tiny pause so the OS clipboard write settles before Ctrl+V reads.
        sleep(0.05)
        try:
            owner.checkpoint_current()
            uia.SendKeys("{Ctrl}v", waitTime=0.05)
            owner.record_step_current()
        finally:
            # Do not overwrite a clipboard value changed by the user.
            if prior is not None:
                try:
                    if uia.GetClipboardText() == text:
                        uia.SetClipboardText(prior)
                except Exception:
                    pass

    def _focused_is_password(self) -> bool:
        """True when the focused element is a secure (password) field (#15).

        Any failure — no focused element, no IsPassword property — reads as
        not secure, matching how the pre-#15 code behaved for ordinary fields.
        """
        try:
            control = uia.GetFocusedControl()
            if control is None:
                return False
            return bool(control.IsPassword)
        except Exception:
            return False

    def _read_focused_text(self) -> str | None:
        """Best-effort read of the focused element's text value.

        Used only by Type verification (#10). Any failure — no focused
        element, neither ValuePattern nor TextPattern supported, a UIA
        error — returns None, which the assessment reports as
        "unavailable" instead of failing the type action itself.
        """
        try:
            control = uia.GetFocusedControl()
            if control is None:
                return None
            try:
                value_pattern = control.GetValuePattern()
                if value_pattern is not None:
                    return value_pattern.Value
            except Exception:
                pass
            try:
                text_pattern = control.GetTextPattern()
                if text_pattern is not None:
                    return text_pattern.DocumentRange.GetText(-1)
            except Exception:
                pass
        except Exception:
            pass
        return None

    def scroll(
        self,
        loc: tuple[int, int] = None,
        type: Literal["horizontal", "vertical"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
    ) -> str | None:
        owner = get_controller()
        owner.checkpoint_current()
        if loc:
            self._check_uipi_point(loc[0], loc[1], "Scroll")
        else:
            cx, cy = uia.GetCursorPos()
            self._check_uipi_point(cx, cy, "Scroll")
        if loc:
            self.move(loc)
        match type:
            case "vertical":
                match direction:
                    case "up":
                        for _ in range(wheel_times):
                            owner.checkpoint_current()
                            uia.WheelUp(1)
                            owner.record_step_current()
                    case "down":
                        for _ in range(wheel_times):
                            owner.checkpoint_current()
                            uia.WheelDown(1)
                            owner.record_step_current()
                    case _:
                        return 'Invalid direction. Use "up" or "down".'
            case "horizontal":
                match direction:
                    case "left":
                        try:
                            owner.input_ledger.press(
                                "key:shift",
                                lambda: uia.PressKey(uia.Keys.VK_SHIFT, waitTime=0.05),
                                lambda: uia.ReleaseKey(uia.Keys.VK_SHIFT, waitTime=0.05),
                                lambda: owner.physical_key_down(uia.Keys.VK_SHIFT),
                            )
                            for _ in range(wheel_times):
                                owner.checkpoint_current()
                                uia.WheelUp(1)
                                owner.record_step_current()
                        finally:
                            owner.input_ledger.release("key:shift")
                    case "right":
                        try:
                            owner.input_ledger.press(
                                "key:shift",
                                lambda: uia.PressKey(uia.Keys.VK_SHIFT, waitTime=0.05),
                                lambda: uia.ReleaseKey(uia.Keys.VK_SHIFT, waitTime=0.05),
                                lambda: owner.physical_key_down(uia.Keys.VK_SHIFT),
                            )
                            for _ in range(wheel_times):
                                owner.checkpoint_current()
                                uia.WheelDown(1)
                                owner.record_step_current()
                        finally:
                            owner.input_ledger.release("key:shift")
                    case _:
                        return 'Invalid direction. Use "left" or "right".'
            case _:
                return 'Invalid type. Use "horizontal" or "vertical".'
        return None

    def _normalize_drag_duration(self, duration: float | int | str | None) -> float | None:
        if duration is None:
            return None
        if isinstance(duration, bool):
            raise ValueError("duration must be a finite number of seconds")
        try:
            effective_duration = float(duration)
        except (TypeError, ValueError) as exc:
            raise ValueError("duration must be a finite number of seconds") from exc
        if not math.isfinite(effective_duration):
            raise ValueError("duration must be a finite number of seconds")
        if effective_duration < 0 or effective_duration > 10:
            raise ValueError("duration must be between 0 and 10 seconds")
        return effective_duration

    @staticmethod
    def _normalize_drag_point(value: object, name: str) -> tuple[int, int]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"{name} must be a list or tuple of exactly 2 integers [x, y]")
        x, y = value
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (x, y)):
            raise ValueError(f"{name} must contain exactly 2 integers")
        return x, y

    def drag(
        self,
        loc: tuple[int, int] | list[int],
        from_loc: tuple[int, int] | list[int] | None = None,
        duration: float | int | str | None = None,
    ) -> dict[str, object]:
        owner = get_controller()
        x, y = self._normalize_drag_point(loc, "loc")
        normalized_from_loc = (
            None if from_loc is None else self._normalize_drag_point(from_loc, "from_loc")
        )
        effective_duration = self._normalize_drag_duration(duration)
        owner.checkpoint_current()
        sleep(0.5)
        owner.checkpoint_current()
        if normalized_from_loc is None:
            cx, cy = uia.GetCursorPos()
        else:
            cx, cy = normalized_from_loc
        self._check_uipi_point(cx, cy, "Drag")
        self._check_uipi_point(x, y, "Drag")
        completed = False
        try:
            owner.input_ledger.press(
                "mouse:left",
                lambda: uia.PressMouse(cx, cy, waitTime=0.05),
                lambda: uia.ReleaseMouse(waitTime=0.05),
                lambda: owner.physical_mouse_down("left"),
            )
            owner.record_step_current()
            # Keep both the legacy drag and duration-based drag interruptible.
            steps = (
                max(1, min(80, max(abs(x - cx), abs(y - cy)) // 20))
                if effective_duration is None
                else max(1, min(200, math.ceil(effective_duration / 0.01)))
            )
            interval = 0.01 if effective_duration is None else effective_duration / steps
            for step in range(1, steps + 1):
                owner.checkpoint_current()
                uia.SetCursorPos(
                    cx + round((x - cx) * step / steps), cy + round((y - cy) * step / steps)
                )
                owner.record_step_current()
                if interval:
                    sleep(interval)
            completed = True
        finally:
            # The release must never mask the outcome (#23): when the drag
            # itself failed, its error propagates and a release failure is
            # only logged; when the drag completed but the release could
            # not be delivered, the honest result is RELEASE_UNCONFIRMED —
            # the button may still be held.
            try:
                owner.input_ledger.release("mouse:left")
            except Exception as release_error:
                if completed:
                    raise RuntimeError(
                        f"RELEASE_UNCONFIRMED: the drag reached ({x}, {y}) but "
                        "releasing the left mouse button could not be confirmed; "
                        "the button may still be held."
                    ) from release_error
                logger.warning(
                    "drag aborted and its mouse release also failed: %r", release_error
                )
        return {"start": [cx, cy], "end": [x, y], "duration": effective_duration}

    def move(self, loc: tuple[int, int]):
        owner = get_controller()
        x, y = loc
        owner.checkpoint_current()
        cx, cy = uia.GetCursorPos()
        steps = max(1, min(80, max(abs(x - cx), abs(y - cy)) // 20))
        for step in range(1, steps + 1):
            owner.checkpoint_current()
            uia.SetCursorPos(cx + (x - cx) * step // steps, cy + (y - cy) * step // steps)
            owner.record_step_current()
            sleep(0.003)

    def shortcut(self, shortcut: str):
        sendkeys_str = _shortcut_to_sendkeys(shortcut)
        owner = get_controller()
        owner.checkpoint_current()
        self._check_uipi_foreground("Shortcut")
        uia.SendKeys(sendkeys_str, interval=0.01)
        owner.record_step_current()

    def multi_select(self, press_ctrl: bool | str = False, locs: list[tuple[int, int]] = []):
        owner = get_controller()
        owner.checkpoint_current()
        press_ctrl = press_ctrl is True or (
            isinstance(press_ctrl, str) and press_ctrl.lower() == "true"
        )
        # Validate every point before Ctrl goes down, so bad input fails without
        # touching the keyboard (and without clicking half of the list).
        points = [self._normalize_drag_point(loc, f"locs[{i}]") for i, loc in enumerate(locs)]
        for px, py in points:
            self._check_uipi_point(px, py, "MultiSelect")
        completed = False
        try:
            if press_ctrl:
                owner.input_ledger.press(
                    "key:ctrl",
                    lambda: uia.PressKey(uia.Keys.VK_CONTROL, waitTime=0.05),
                    lambda: uia.ReleaseKey(uia.Keys.VK_CONTROL, waitTime=0.05),
                    lambda: owner.physical_key_down(uia.Keys.VK_CONTROL),
                )
            for x, y in points:
                owner.checkpoint_current()
                uia.Click(x, y, waitTime=0.2)
                owner.record_step_current()
                sleep(0.5)
            completed = True
        finally:
            # Same non-masking release discipline as drag (#23).
            if press_ctrl:
                try:
                    owner.input_ledger.release("key:ctrl")
                except Exception as release_error:
                    if completed:
                        raise RuntimeError(
                            "RELEASE_UNCONFIRMED: the selection clicks completed "
                            "but releasing Ctrl could not be confirmed; the "
                            "modifier may still be held."
                        ) from release_error
                    logger.warning(
                        "multi-select aborted and its Ctrl release also failed: %r",
                        release_error,
                    )

    def multi_edit(self, locs: list[tuple[int, int, str]]):
        for loc in locs:
            get_controller().checkpoint_current()
            x, y, text = loc
            self.type((x, y), text=text, clear=True)

    def scrape(self, url: str) -> str:
        current_url = url
        try:
            for _ in range(5):
                response = safe_get(current_url, timeout=10)
                if not response.is_redirect:
                    break
                current_url = urljoin(current_url, response.headers["Location"])
            else:
                raise ValueError("Too many redirects while fetching URL")
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise ValueError(f"HTTP error for {current_url}: {e}") from e
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(f"Failed to connect to {current_url}: {e}") from e
        except requests.exceptions.Timeout as e:
            raise TimeoutError(f"Request timed out for {current_url}: {e}") from e
        has_charset = "charset" in response.headers.get("content-type", "").lower()
        if not has_charset and response.encoding == "ISO-8859-1":
            # requests assumes ISO-8859-1 for ``text/*`` without a charset
            # (RFC 2616) and ignores the in-document ``<meta charset>``, so
            # non-ASCII pages come back as mojibake. Sniff the body instead.
            response.encoding = response.apparent_encoding or "utf-8"
        html = response.text
        content = markdownify(html=html)
        return content

    def is_overlay_window(self, element: uia.Control) -> bool:
        """Return True if the window is a decorative overlay rather than a real app window.

        "No children" alone is deliberately not enough. When UIA child
        enumeration degrades — as it does on Windows ARM64 under x86-emulated
        Python — every window looks childless, and treating that as an overlay
        filters out the entire desktop and leaves the caller blind. A real app
        window always has a title, so require both signals.
        """
        name = element.Name.strip()
        if "Overlay" in name:
            return True
        return not name and len(element.GetChildren()) == 0

    def get_controls_handles(self, optimized: bool = False):
        handles = set()
        own_pid = os.getpid()

        def owned_by_this_server(hwnd) -> bool:
            # The control overlay (7 WindowsMCPControl_* windows) and the click flashes live in this
            # process. They are never a target, and the tree walk spends about a second on each one,
            # which made every Snapshot and every after-state take ~15 s. A handle whose owner cannot
            # be read is kept, as before.
            try:
                return win32process.GetWindowThreadProcessId(hwnd)[1] == own_pid
            except Exception:
                return False

        # For even more faster results (still under development)
        def callback(hwnd, _):
            try:
                # Validate handle before checking properties
                if (
                    win32gui.IsWindow(hwnd)
                    and win32gui.IsWindowVisible(hwnd)
                    and is_window_on_current_desktop(hwnd)
                    and not owned_by_this_server(hwnd)
                ):
                    handles.add(hwnd)
            except Exception:
                # Skip invalid handles without logging (common during window enumeration)
                pass

        win32gui.EnumWindows(callback, None)

        if desktop_hwnd := win32gui.FindWindow("Progman", None):
            handles.add(desktop_hwnd)
        if taskbar_hwnd := win32gui.FindWindow("Shell_TrayWnd", None):
            handles.add(taskbar_hwnd)
        if secondary_taskbar_hwnd := win32gui.FindWindow("Shell_SecondaryTrayWnd", None):
            handles.add(secondary_taskbar_hwnd)
        return handles

    # Tuned retry envelope for transient UIA empty results. The OS
    # briefly returns NULL from GetForegroundWindow during focus
    # transitions, app launches, and notification overlays — three
    # attempts at 100 ms each covers the typical race without
    # noticeably slowing the snapshot path when state is steady.
    _UIA_RETRIES = 3
    _UIA_RETRY_SLEEP_MS = 100

    def get_active_window(self, windows: list[Window] | None = None) -> Window | None:
        """Return the foreground app window, retrying briefly on transient
        empty results.

        GetForegroundWindow can return NULL during focus transitions, app
        launches, and notification-overlay flicker — even when there is a
        visible focused window on screen. Without a retry, Snapshot
        reports "No active window found" and the caller is left blind.
        """
        last_error = None
        for attempt in range(self._UIA_RETRIES):
            try:
                if windows is None:
                    windows, _ = self.get_windows()
                active_window = self.get_foreground_window()
                if active_window is None:
                    # NULL foreground — retry, this often clears on the
                    # next pass once whatever was transitioning settles.
                    sleep(self._UIA_RETRY_SLEEP_MS / 1000.0)
                    continue
                if active_window.ClassName == "Progman":
                    return None
                active_window_handle = active_window.NativeWindowHandle
                for window in windows:
                    if window.handle != active_window_handle:
                        continue
                    return window
                # In case active window is not present in the windows list
                return Window(
                    **{
                        "name": active_window.Name,
                        "is_browser": self.is_window_browser(active_window),
                        "depth": 0,
                        "bounding_box": BoundingBox(
                            left=active_window.BoundingRectangle.left,
                            top=active_window.BoundingRectangle.top,
                            right=active_window.BoundingRectangle.right,
                            bottom=active_window.BoundingRectangle.bottom,
                            width=active_window.BoundingRectangle.width(),
                            height=active_window.BoundingRectangle.height(),
                        ),
                        "status": self.get_window_status(active_window),
                        "handle": active_window_handle,
                        "process_id": active_window.ProcessId,
                    }
                )
            except Exception as ex:
                last_error = ex
                if uia.is_timeout_exception(ex):
                    # The foreground window is not answering UI Automation; a retry
                    # would only wait out the same timeout again.
                    break
                # Same retry policy for transient exceptions —
                # ControlFromHandle(NULL) raises during focus transitions
                # and the next attempt usually succeeds.
                sleep(self._UIA_RETRY_SLEEP_MS / 1000.0)
                continue
        if last_error is not None:
            logger.error(
                f"Error in get_active_window after {attempt + 1} attempt(s): {last_error}"
            )
        return None

    def get_foreground_window(self) -> uia.Control | None:
        handle = uia.GetForegroundWindow()
        # NULL handle means no window has foreground focus right now —
        # don't pass that into ControlFromHandle, which would raise.
        if not handle:
            return None
        return self.get_window_from_element_handle(handle)

    def get_window_from_element_handle(self, element_handle: int) -> uia.Control:
        current = uia.ControlFromHandle(element_handle)
        root_handle = uia.GetRootControl().NativeWindowHandle

        while True:
            parent = current.GetParentControl()
            if parent is None or parent.NativeWindowHandle == root_handle:
                return current
            current = parent

    def get_windows(
        self, controls_handles: set[int] | None = None
    ) -> tuple[list[Window], set[int]]:
        try:
            windows = []
            window_handles = set()
            controls_handles = controls_handles or self.get_controls_handles()
            for depth, hwnd in enumerate(controls_handles):
                try:
                    child = uia.ControlFromHandle(hwnd)
                except Exception:
                    continue

                # Filter out Overlays (e.g. NVIDIA, Steam)
                if self.is_overlay_window(child):
                    continue

                if isinstance(child, (uia.WindowControl, uia.PaneControl)):
                    window_pattern = child.GetPattern(uia.PatternId.WindowPattern)
                    if window_pattern is None:
                        continue

                    if window_pattern.CanMinimize and window_pattern.CanMaximize:
                        status = self.get_window_status(child)

                        bounding_rect = child.BoundingRectangle
                        if bounding_rect.isempty() and status != Status.MINIMIZED:
                            continue

                        windows.append(
                            Window(
                                **{
                                    "name": child.Name,
                                    "depth": depth,
                                    "status": status,
                                    "bounding_box": BoundingBox(
                                        left=bounding_rect.left,
                                        top=bounding_rect.top,
                                        right=bounding_rect.right,
                                        bottom=bounding_rect.bottom,
                                        width=bounding_rect.width(),
                                        height=bounding_rect.height(),
                                    ),
                                    "handle": child.NativeWindowHandle,
                                    "process_id": child.ProcessId,
                                    "is_browser": self.is_window_browser(child),
                                }
                            )
                        )
                        window_handles.add(child.NativeWindowHandle)
        except Exception as ex:
            logger.error(f"Error in get_windows: {ex}")
            windows = []
        return windows, window_handles

    def get_screen_size(self) -> Size:
        width, height = uia.GetVirtualScreenSize()
        return Size(width=width, height=height)

    def get_screen_box(self) -> BoundingBox:
        left, top, width, height = uia.GetVirtualScreenRect()
        return BoundingBox(
            left=left,
            top=top,
            right=left + width,
            bottom=top + height,
            width=width,
            height=height,
        )

    @staticmethod
    def parse_display_selection(
        display: int | list[int] | tuple[int, ...] | None,
    ) -> list[int] | None:
        if display is None or display == "":
            return None

        if isinstance(display, bool):
            raise ValueError(
                "display must be a JSON array of zero-based active display indices, for example [0] or [0,1]"
            )

        if isinstance(display, int):
            values = [display]
        elif isinstance(display, (list, tuple)):
            values = list(display)
        else:
            raise ValueError(
                "display must be a JSON array of zero-based active display indices, for example [0] or [0,1]"
            )

        unique_values: list[int] = []
        for value in values:
            if not isinstance(value, int) or value < 0:
                raise ValueError("display must contain only zero-based active display indices")
            if value not in unique_values:
                unique_values.append(value)
        return unique_values or None

    def parse_region_selection(self, region: list[int] | tuple[int, ...] | None) -> uia.Rect | None:
        if region is None or region == "":
            return None

        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ValueError("region must be a JSON array of 4 integers [left, top, right, bottom]")

        values = []
        for item in region:
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError("region must contain only integers [left, top, right, bottom]")
            values.append(item)

        left, top, right, bottom = values
        if right <= left or bottom <= top:
            raise ValueError("region must satisfy right > left and bottom > top")

        candidate = uia.Rect(left, top, right, bottom)
        screen_box = self.get_screen_box()
        screen_rect = uia.Rect(screen_box.left, screen_box.top, screen_box.right, screen_box.bottom)
        overlap = candidate.intersect(screen_rect)
        if overlap.width() <= 0 or overlap.height() <= 0:
            raise ValueError(
                f"region {region} does not overlap the virtual screen bounds "
                f"{screen_box.xyxy_to_string()}"
            )

        return candidate

    @staticmethod
    def get_displays() -> list[uia.DisplayInfo]:
        return uia.GetDisplays()

    @staticmethod
    def _display_to_view(display: uia.DisplayInfo) -> Display:
        return Display(
            index=display.index,
            device_name=display.device_name,
            bounding_box=Desktop._rect_to_bounding_box(display.rect),
            primary=display.primary,
        )

    def get_display_union_rect(
        self,
        display_indices: list[int],
        displays: list[uia.DisplayInfo] | None = None,
    ) -> uia.Rect:
        displays = displays if displays is not None else self.get_displays()
        if not displays:
            logger.warning(
                "Monitor enumeration returned no monitors while display filter was requested"
            )
            raise ValueError("No displays detected")

        display_by_index = {display.index: display for display in displays}
        invalid_indices = [index for index in display_indices if index not in display_by_index]
        if invalid_indices:
            available_indices = ",".join(str(display.index) for display in displays)
            logger.warning(
                "Invalid display selection %s. Available displays: %s",
                invalid_indices,
                available_indices,
            )
            raise ValueError(
                f"Invalid display index {invalid_indices[0]}. Available displays: {available_indices}"
            )

        selected_rects = [display_by_index[index].rect for index in display_indices]
        return uia.Rect(
            left=min(rect.left for rect in selected_rects),
            top=min(rect.top for rect in selected_rects),
            right=max(rect.right for rect in selected_rects),
            bottom=max(rect.bottom for rect in selected_rects),
        )

    def get_screenshot(self, capture_rect: uia.Rect | None = None) -> Image.Image:
        # The persistent AI indicator is hidden and acknowledged before any
        # screenshot backend reads pixels. Physical input is released first so
        # a stalled capture cannot leave the user blocked by an invisible AI.
        controller = get_controller()
        capture_generation = controller.pause_for_capture()
        restored = False
        try:
            with control_overlay.suspend_for_capture():
                if not flash_overlay.cancel_active_flash():
                    raise RuntimeError("Previous screenshot flash did not close before capture")
                image, used_backend = screenshot_capture.capture(capture_rect)
                self._last_screenshot_backend = used_backend
            restored = True
        finally:
            controller.resume_after_capture(capture_generation, restored=restored)
        flash_overlay.show_capture_flash(capture_rect)
        return image

    def get_annotated_screenshot(
        self,
        nodes: list[TreeElementNode],
        cursor_pos: tuple[int, int] | None = None,
        grid_lines: tuple[int, int] | None = None,
        capture_rect: uia.Rect | None = None,
    ) -> Image.Image:
        screenshot = self.get_screenshot(capture_rect=capture_rect)
        annotated_screenshot = screenshot.copy()
        draw = ImageDraw.Draw(annotated_screenshot)
        image_width, image_height = annotated_screenshot.size
        font_size = 12
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except IOError:
            font = ImageFont.load_default()

        def get_random_color():
            return "#{:06x}".format(random.randint(0, 0xFFFFFF))

        def clamp(value: float, minimum: float, maximum: float) -> float:
            return max(minimum, min(value, maximum))

        def get_label_size(text: str) -> tuple[int, int]:
            text_box = draw.textbbox((0, 0), text, font=font)
            return text_box[2] - text_box[0] + 4, text_box[3] - text_box[1] + 4

        def draw_label(text: str, x: float, y: float, color: str) -> None:
            label_width, label_height = get_label_size(text)
            label_x = int(clamp(x, 0, max(0, image_width - label_width)))
            label_y = int(clamp(y, 0, max(0, image_height - label_height)))
            draw.rectangle(
                [(label_x, label_y), (label_x + label_width, label_y + label_height)],
                fill=color,
            )
            draw.text((label_x + 2, label_y + 2), text, fill=(255, 255, 255), font=font)

        if capture_rect:
            left_offset, top_offset = capture_rect.left, capture_rect.top
        else:
            left_offset, top_offset, _, _ = uia.GetVirtualScreenRect()

        # Draw grid lines if requested
        if grid_lines:
            w_count, h_count = grid_lines
            for i in range(1, w_count):
                x = image_width * i // w_count
                draw.line([(x, 0), (x, image_height)], fill=(200, 200, 200, 128), width=1)
            for i in range(1, h_count):
                y = image_height * i // h_count
                draw.line([(0, y), (image_width, y)], fill=(200, 200, 200, 128), width=1)

        def draw_annotation(label, node: TreeElementNode):
            box = node.bounding_box
            color = get_random_color()

            adjusted_left = int(box.left - left_offset)
            adjusted_top = int(box.top - top_offset)
            adjusted_right = int(box.right - left_offset)
            adjusted_bottom = int(box.bottom - top_offset)
            clipped_box = (
                int(clamp(adjusted_left, 0, image_width - 1)),
                int(clamp(adjusted_top, 0, image_height - 1)),
                int(clamp(adjusted_right, 0, image_width - 1)),
                int(clamp(adjusted_bottom, 0, image_height - 1)),
            )
            left, top, right, bottom = clipped_box
            if right <= left or bottom <= top:
                return

            draw.rectangle(clipped_box, outline=color, width=2)

            label_text = str(label)
            label_width, label_height = get_label_size(label_text)
            label_x = right - label_width
            label_y = top - label_height - 2
            if label_y < 0:
                label_y = bottom + 2
            draw_label(label_text, label_x, label_y, color)

        # Draw annotations sequentially: PIL ImageDraw is not thread-safe and
        # drawing is GIL-bound, so parallel execution adds risk without speed.
        for i, node in enumerate(nodes):
            draw_annotation(i, node)

        # Draw cursor highlight if pos provided
        if cursor_pos:
            cx, cy = cursor_pos
            acx = int(cx - left_offset)
            acy = int(cy - top_offset)

            # Draw a distinctive marker (e.g., a circle or crosshair with a box)
            r = 15
            draw.ellipse([acx - r, acy - r, acx + r, acy + r], outline="red", width=3)
            draw.line([acx - r, acy, acx + r, acy], fill="red", width=2)
            draw.line([acx, acy - r, acx, acy + r], fill="red", width=2)

            # Draw "Cursor" label
            c_label = "CURSOR"
            draw_label(c_label, acx + r, acy - r, "red")

        return annotated_screenshot

    @staticmethod
    def _rect_to_bounding_box(rect: uia.Rect | None) -> BoundingBox | None:
        if rect is None:
            return None
        return BoundingBox(
            left=rect.left,
            top=rect.top,
            right=rect.right,
            bottom=rect.bottom,
            width=rect.width(),
            height=rect.height(),
        )

    @staticmethod
    def _point_in_region(point: tuple[int, int], region: BoundingBox) -> bool:
        x, y = point
        return region.left <= x < region.right and region.top <= y < region.bottom

    @staticmethod
    def _clip_bounding_box_to_region(
        box: BoundingBox | None, region: BoundingBox
    ) -> BoundingBox | None:
        if box is None:
            return None
        left = max(box.left, region.left)
        top = max(box.top, region.top)
        right = min(box.right, region.right)
        bottom = min(box.bottom, region.bottom)
        if right <= left or bottom <= top:
            return None
        return BoundingBox(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            width=right - left,
            height=bottom - top,
        )

    def _filter_window_to_region(self, window: Window | None, region: BoundingBox) -> Window | None:
        if window is None:
            return None
        clipped_box = self._clip_bounding_box_to_region(window.bounding_box, region)
        if clipped_box is None:
            return None
        return Window(
            name=window.name,
            is_browser=window.is_browser,
            depth=window.depth,
            status=window.status,
            bounding_box=clipped_box,
            handle=window.handle,
            process_id=window.process_id,
        )

    def _filter_windows_to_region(self, windows: list[Window], region: BoundingBox) -> list[Window]:
        filtered_windows: list[Window] = []
        for window in windows:
            filtered_window = self._filter_window_to_region(window, region)
            if filtered_window is not None:
                filtered_windows.append(filtered_window)
        return filtered_windows

    def _filter_tree_node_to_region(
        self, node: TreeElementNode, region: BoundingBox
    ) -> TreeElementNode | None:
        clipped_box = self._clip_bounding_box_to_region(node.bounding_box, region)
        if clipped_box is None:
            return None
        return TreeElementNode(
            name=node.name,
            control_type=node.control_type,
            window_name=node.window_name,
            bounding_box=clipped_box,
            center=clipped_box.get_center(),
            metadata=node.metadata,
        )

    def _filter_scroll_node_to_region(self, node, region: BoundingBox):
        clipped_box = self._clip_bounding_box_to_region(node.bounding_box, region)
        if clipped_box is None:
            return None
        return node.__class__(
            name=node.name,
            control_type=node.control_type,
            window_name=node.window_name,
            bounding_box=clipped_box,
            center=clipped_box.get_center(),
            metadata=node.metadata,
        )

    def _filter_semantic_node_to_region(
        self,
        node: SemanticNode | None,
        region: BoundingBox,
    ) -> SemanticNode | None:
        if node is None:
            return None

        clipped_box = None
        if node.bounding_box is not None:
            clipped_box = self._clip_bounding_box_to_region(node.bounding_box, region)
            if clipped_box is None:
                return None

        filtered_children = []
        for child in node.children:
            filtered_child = self._filter_semantic_node_to_region(child, region)
            if filtered_child is not None:
                filtered_children.append(filtered_child)

        if node.element_type != "desktop" and clipped_box is None and not filtered_children:
            return None

        filtered_node = SemanticNode(
            control_type=node.control_type,
            element_type=node.element_type,
            name=node.name,
            window_name=node.window_name,
            center=clipped_box.get_center() if clipped_box is not None else node.center,
            bounding_box=clipped_box,
            metadata=dict(node.metadata),
        )
        filtered_node.children = filtered_children
        return filtered_node

    def _filter_tree_state_to_region(self, tree_state, region: BoundingBox):
        filtered_interactive_nodes = []
        for node in tree_state.interactive_nodes:
            filtered_node = self._filter_tree_node_to_region(node, region)
            if filtered_node is not None:
                filtered_interactive_nodes.append(filtered_node)

        filtered_scrollable_nodes = []
        for node in tree_state.scrollable_nodes:
            filtered_node = self._filter_scroll_node_to_region(node, region)
            if filtered_node is not None:
                filtered_scrollable_nodes.append(filtered_node)

        filtered_dom_node = None
        if tree_state.dom_node is not None:
            filtered_dom_node = self._filter_scroll_node_to_region(tree_state.dom_node, region)

        filtered_semantic_root = self._filter_semantic_node_to_region(
            tree_state.semantic_tree_root,
            region,
        )
        if filtered_semantic_root is not None:
            filtered_semantic_root.bounding_box = region
            filtered_semantic_root.center = region.get_center()

        return tree_state.__class__(
            status=tree_state.status,
            root_node=TreeElementNode(
                name="Desktop",
                control_type="PaneControl",
                bounding_box=region,
                center=region.get_center(),
                window_name="Desktop",
                metadata={},
            ),
            dom_node=filtered_dom_node,
            interactive_nodes=filtered_interactive_nodes,
            scrollable_nodes=filtered_scrollable_nodes,
            dom_informative_nodes=tree_state.dom_informative_nodes if filtered_dom_node else [],
            capture_sec=tree_state.capture_sec,
            semantic_tree_root=filtered_semantic_root,
            truncated=tree_state.truncated,
            element_limit=tree_state.element_limit,
        )
