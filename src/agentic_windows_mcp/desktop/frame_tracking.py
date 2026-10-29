"""Screenshot frame identity + change tracking (issue #6).

Every capture gets a content hash of its encoded frame and a wall-clock
timestamp, so a caller can tell whether two screenshots actually show the
same frame instead of trusting that a capture is fresh. The tracker keeps
the most recent hash across calls, which is what ``wait_for_change``
compares against: identical bytes in, identical hash out — any visual
change changes the hash.
"""

from __future__ import annotations

import hashlib
import threading
import time


def frame_hash(png_bytes: bytes) -> str:
    """Stable content hash of one encoded frame."""
    return hashlib.sha256(png_bytes).hexdigest()


class FrameTracker:
    """Remembers the last captured frame's hash and when it was taken."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_hash: str | None = None
        self._last_captured_at: float | None = None

    def note(self, png_bytes: bytes, captured_at: float | None = None) -> tuple[str, bool]:
        """Record a capture; return ``(hash, changed_since_previous)``."""
        digest = frame_hash(png_bytes)
        with self._lock:
            changed = self._last_hash is not None and digest != self._last_hash
            self._last_hash = digest
            self._last_captured_at = captured_at if captured_at is not None else time.time()
            return digest, changed

    def matches_last(self, png_bytes: bytes) -> bool:
        """True when this frame is byte-identical to the previous capture."""
        digest = frame_hash(png_bytes)
        with self._lock:
            return self._last_hash is not None and digest == self._last_hash

    @property
    def last_hash(self) -> str | None:
        with self._lock:
            return self._last_hash


FRAMES = FrameTracker()
