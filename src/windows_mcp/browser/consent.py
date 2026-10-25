"""Auto-allow the browser's remote-debugging consent dialog.

Chrome/Edge/Brave (136+) show a native consent dialog ("Allow remote
debugging?" / "Cho phép gỡ lỗi từ xa?") when a CDP client attaches to the
user's default browser profile. While the dialog is up, CDP commands
stall -- and the prompt deadlocks the agent: its browser call waits on
the dialog while the desktop control lease it holds keeps the user from
clicking the dialog away without a manual takeover.

This module breaks the deadlock from inside the server process: a small
watcher thread looks for exactly that dialog -- a top-level window of a
browser process whose UI subtree contains BOTH the consent title and an
Allow button -- and invokes the button. The match is deliberately narrow
and every approval is logged. The behaviour is ON by default in this fork
at the owner's explicit request; disable it with
``auto_allow_remote_debugging = false`` under ``[browser]`` in
~/.windows-mcp/config.toml, or the env var
WINDOWS_MCP_AUTO_ALLOW_REMOTE_DEBUGGING=0.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DIALOG_TITLE_MARKERS = (
    "allow remote debugging",
    "cho phép gỡ lỗi từ xa",
)
ALLOW_BUTTON_LABELS = ("allow", "cho phép")
BROWSER_PROCESS_NAMES = {"chrome.exe", "msedge.exe", "brave.exe"}
BUTTON_CONTROL_TYPES = ("Button", "ButtonControl")
MAX_WALK_NODES = 2500
MAX_CLICK_ATTEMPTS_PER_WINDOW = 3
RETRY_CLICK_AFTER_SECONDS = 5.0
# The consent prompt is a small modal card; skip full-size browser windows
# in the scan so the per-second poll stays cheap.
MAX_DIALOG_WINDOW_WIDTH = 1000
MAX_DIALOG_WINDOW_HEIGHT = 800

# (control_type_name, name) pairs gathered from one window's UI subtree.
NameEntry = Tuple[str, str]


def is_consent_title(text: Optional[str]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in DIALOG_TITLE_MARKERS)


def is_allow_label(text: Optional[str]) -> bool:
    if not text:
        return False
    return text.strip().lower() in ALLOW_BUTTON_LABELS


def find_consent(entries: Sequence[NameEntry]) -> Optional[str]:
    """Return the allow button's label if *entries* describe the dialog.

    Both halves must be present in the same window: a consent title text
    and a button whose label is exactly an allow label. Anything looser
    could approve unrelated prompts, so partial matches return None.
    """
    if not any(is_consent_title(name) for _ctype, name in entries):
        return None
    for ctype, name in entries:
        if ctype in BUTTON_CONTROL_TYPES and is_allow_label(name):
            return name.strip()
    return None


def auto_allow_enabled() -> bool:
    """Env override wins; then the config file; default True in this fork."""
    raw = os.getenv("WINDOWS_MCP_AUTO_ALLOW_REMOTE_DEBUGGING")
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "no", "off", "")
    try:
        from windows_mcp.infrastructure.config import discover_config_path, load_config

        cfg = load_config(discover_config_path(None))
        return bool(cfg.browser.auto_allow_remote_debugging)
    except Exception:
        return True


def _browser_dialog_windows() -> List[int]:
    """Visible, dialog-sized top-level windows owned by a browser process."""
    import psutil
    import win32gui
    import win32process

    handles: List[int] = []

    def _cb(hwnd, _extra):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if (right - left) > MAX_DIALOG_WINDOW_WIDTH or (
                bottom - top
            ) > MAX_DIALOG_WINDOW_HEIGHT:
                return True
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                name = psutil.Process(pid).name().lower()
            except Exception:
                return True
            if name in BROWSER_PROCESS_NAMES:
                handles.append(hwnd)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return handles


def _uia_root(hwnd: int):
    import contextlib

    import windows_mcp.uia as uia

    init = getattr(uia, "UIAutomationInitializerInThread", None)
    ctx = init() if init is not None else contextlib.nullcontext()
    return uia, ctx


def _walk_names(root) -> List[NameEntry]:
    entries: List[NameEntry] = []
    stack = [root]
    while stack and len(entries) < MAX_WALK_NODES:
        ctrl = stack.pop()
        try:
            entries.append((ctrl.ControlTypeName or "", ctrl.Name or ""))
        except Exception:
            continue
        try:
            stack.extend(ctrl.GetChildren() or [])
        except Exception:
            pass
    return entries


def _find_button(root, label: str):
    stack = [root]
    seen = 0
    while stack and seen < MAX_WALK_NODES:
        ctrl = stack.pop()
        seen += 1
        try:
            ctype = ctrl.ControlTypeName or ""
            name = (ctrl.Name or "").strip()
        except Exception:
            continue
        if ctype in BUTTON_CONTROL_TYPES and name == label:
            return ctrl
        try:
            stack.extend(ctrl.GetChildren() or [])
        except Exception:
            pass
    return None


def _click(control) -> bool:
    try:
        pattern = control.GetInvokePattern()
        if pattern is not None:
            pattern.Invoke()
            return True
    except Exception:
        pass
    try:
        control.Click()
        return True
    except Exception:
        return False


_attempts: dict[int, tuple[int, float]] = {}
_attempts_lock = threading.Lock()


def _should_click(hwnd: int) -> bool:
    now = time.monotonic()
    with _attempts_lock:
        count, last = _attempts.get(hwnd, (0, 0.0))
        if count >= MAX_CLICK_ATTEMPTS_PER_WINDOW:
            return False
        if now - last < RETRY_CLICK_AFTER_SECONDS:
            return False
        _attempts[hwnd] = (count + 1, now)
        return True


def try_allow_once() -> bool:
    """Approve the consent dialog if it is currently shown anywhere."""
    try:
        uia, ctx = _uia_root(0)
    except Exception:
        return False
    with ctx:
        for hwnd in _browser_dialog_windows():
            try:
                root = uia.ControlFromHandle(hwnd)
                if root is None:
                    continue
                label = find_consent(_walk_names(root))
                if label is None or not _should_click(hwnd):
                    continue
                button = _find_button(root, label)
                if button is None:
                    continue
                if _click(button):
                    logger.info(
                        "Approved the browser remote-debugging consent dialog "
                        "(window %s, button %r)",
                        hwnd,
                        label,
                    )
                    return True
            except Exception:
                logger.debug("Consent scan failed for one window", exc_info=True)
    return False


class ConsentWatcher:
    """Poll for the consent dialog and approve it while the server runs."""

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="BrowserConsentWatcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                try_allow_once()
            except Exception:
                logger.debug("Consent watcher pass failed", exc_info=True)
