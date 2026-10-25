"""Detect Chrome's remote-debugging consent dialog and hand input back.

Chrome/Edge/Brave (136+) gate CDP access to the user's default profile
behind a native consent dialog ("Allow remote debugging?" / "Cho phép gỡ
lỗi từ xa?"). While the dialog is up, the agent's browser call stalls --
and because the control coordinator suppresses physical input during an
AI lease, the user cannot click the dialog away without first forcing a
takeover with the Ctrl+Alt+Shift+Backspace chord. Agent and user lock
each other out.

The owner's decision (2026-10-25): the agent must NOT approve the dialog
itself -- approving stays a human click. Instead, this monitor watches
for exactly that dialog (a top-level window of a browser process whose
UI subtree contains BOTH the consent title and an Allow button) and,
while it is present, asks the control coordinator to release the input
suppression ("dialog yield") so the user can simply move the mouse and
click Allow. When the dialog closes, suppression is restored for the
remainder of the lease. No state changes hands; the pending call keeps
its lease and completes as soon as the user approves.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DIALOG_TITLE_MARKERS = (
    "allow remote debugging",
    "cho phép gỡ lỗi từ xa",
)
ALLOW_BUTTON_LABELS = ("allow", "cho phép")
BROWSER_PROCESS_NAMES = {"chrome.exe", "msedge.exe", "brave.exe"}
BUTTON_CONTROL_TYPES = ("Button", "ButtonControl")
MAX_WALK_NODES = 2500
# The consent prompt is a small modal card; skip full-size browser windows
# in the scan so the per-second poll stays cheap.
MAX_DIALOG_WINDOW_WIDTH = 1000
MAX_DIALOG_WINDOW_HEIGHT = 800

# (control_type_name, name) pairs gathered from one window's UI subtree.
NameEntry = Tuple[str, str]
YieldHandler = Callable[[bool], None]

_yield_handler: Optional[YieldHandler] = None


def set_yield_handler(handler: Optional[YieldHandler]) -> None:
    """Register the coordinator callback driven by the monitor.

    Called once from the server lifespan with the control coordinator's
    ``set_dialog_yield`` method. Until a handler is registered the
    monitor still detects (and logs) the dialog, it just cannot release
    the input suppression.
    """
    global _yield_handler
    _yield_handler = handler


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
    could mistake an unrelated prompt for the consent dialog.
    """
    if not any(is_consent_title(name) for _ctype, name in entries):
        return None
    for ctype, name in entries:
        if ctype in BUTTON_CONTROL_TYPES and is_allow_label(name):
            return name.strip()
    return None


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


def _uia_root():
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


def detect_consent_dialog() -> bool:
    """True while the remote-debugging consent dialog is on screen."""
    try:
        uia, ctx = _uia_root()
    except Exception:
        return False
    with ctx:
        for hwnd in _browser_dialog_windows():
            try:
                root = uia.ControlFromHandle(hwnd)
                if root is not None and find_consent(_walk_names(root)) is not None:
                    return True
            except Exception:
                logger.debug("Consent scan failed for one window", exc_info=True)
    return False


class ConsentDialogMonitor:
    """Poll for the consent dialog and drive the input-yield handler.

    While the dialog is present the handler is (re)asserted with True on
    every pass, so a missed call self-heals; when the dialog disappears
    the handler is called once with False to restore suppression.
    """

    def __init__(
        self,
        interval: float = 1.0,
        detector: Callable[[], bool] = detect_consent_dialog,
    ) -> None:
        self.interval = interval
        self._detector = detector
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._yielding = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="BrowserConsentMonitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._yielding:
            self._emit(False)

    def _emit(self, active: bool) -> None:
        handler = _yield_handler
        if handler is None:
            return
        try:
            handler(active)
        except Exception:
            logger.debug("Dialog yield handler failed", exc_info=True)

    def poll_once(self) -> bool:
        """One detection pass; returns whether the dialog is present."""
        present = bool(self._detector())
        if present:
            if not self._yielding:
                logger.info(
                    "Remote-debugging consent dialog detected; "
                    "releasing input suppression so the user can answer it"
                )
            self._yielding = True
            self._emit(True)
        elif self._yielding:
            self._yielding = False
            logger.info("Consent dialog closed; input suppression restored")
            self._emit(False)
        return present

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.poll_once()
            except Exception:
                logger.debug("Consent monitor pass failed", exc_info=True)
