"""Issue #6 — screenshot frame identity: content hash + change tracking."""

from agentic_windows_mcp.desktop.frame_tracking import FrameTracker, frame_hash


def test_hash_is_stable_and_content_sensitive():
    assert frame_hash(b"frame-a") == frame_hash(b"frame-a")
    assert frame_hash(b"frame-a") != frame_hash(b"frame-b")
    assert len(frame_hash(b"x")) == 64


def test_first_capture_is_baseline_not_change():
    tracker = FrameTracker()
    digest, changed = tracker.note(b"a")
    assert digest == frame_hash(b"a")
    assert changed is False
    assert tracker.last_hash == digest


def test_change_detection():
    tracker = FrameTracker()
    tracker.note(b"a")
    _, changed = tracker.note(b"a")
    assert changed is False
    _, changed = tracker.note(b"b")
    assert changed is True
    # The new frame becomes the baseline.
    _, changed = tracker.note(b"b")
    assert changed is False


def test_matches_last():
    tracker = FrameTracker()
    assert tracker.matches_last(b"a") is False  # no baseline yet
    tracker.note(b"a")
    assert tracker.matches_last(b"a") is True
    assert tracker.matches_last(b"b") is False
