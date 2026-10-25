"""Tests for the remote-debugging consent dialog detector and monitor.

Only the pure logic is exercised here: the UIA/Win32 scan only runs on
Windows inside the live server, but the decision of WHAT counts as the
consent dialog must never widen beyond the exact Chrome prompt, and the
monitor must drive the input-yield handler exactly on the dialog's
appearance/disappearance (the agent never approves the dialog itself --
it only hands input back so the user can click).
"""

from __future__ import annotations

import pytest

from windows_mcp.browser import consent


def test_title_markers_en_and_vi():
    assert consent.is_consent_title("Allow remote debugging?")
    assert consent.is_consent_title("Cho phép gỡ lỗi từ xa?")
    assert consent.is_consent_title("ALLOW REMOTE DEBUGGING")
    assert not consent.is_consent_title("Allow notifications?")
    assert not consent.is_consent_title("Remote debugging")
    assert not consent.is_consent_title("")
    assert not consent.is_consent_title(None)


def test_allow_label_is_exact():
    assert consent.is_allow_label("Allow")
    assert consent.is_allow_label("Cho phép")
    assert consent.is_allow_label("  allow  ")
    assert not consent.is_allow_label("Allow all")
    assert not consent.is_allow_label("Don't allow")
    assert not consent.is_allow_label("Huỷ")
    assert not consent.is_allow_label("Cancel")
    assert not consent.is_allow_label(None)


def test_find_consent_requires_title_and_button_in_same_window():
    entries = [
        ("TextControl", "Allow remote debugging?"),
        ("TextControl", "An external app wants full control of this Chrome session."),
        ("ButtonControl", "Turn off in Settings"),
        ("ButtonControl", "Cho phép"),
        ("ButtonControl", "Huỷ"),
    ]
    assert consent.find_consent(entries) == "Cho phép"


def test_find_consent_rejects_partial_matches():
    # Button without the consent title: some other dialog's Allow.
    assert consent.find_consent([("ButtonControl", "Allow")]) is None
    # Title without an allow button.
    assert (
        consent.find_consent(
            [("TextControl", "Allow remote debugging?"), ("ButtonControl", "Cancel")]
        )
        is None
    )
    # A non-button element labelled Allow does not count.
    assert (
        consent.find_consent(
            [("TextControl", "Allow remote debugging?"), ("TextControl", "Allow")]
        )
        is None
    )
    assert consent.find_consent([]) is None


class _Script:
    """Detector stub returning a scripted sequence of booleans."""

    def __init__(self, values: list[bool]) -> None:
        self._values = list(values)

    def __call__(self) -> bool:
        return self._values.pop(0) if self._values else False


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_monitor_yields_on_appear_and_holds_release_for_10s(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[bool] = []
    monkeypatch.setattr(consent, "_yield_handler", calls.append)
    clock = _Clock()
    monitor = consent.ConsentDialogMonitor(
        detector=_Script([False, True, True, False, False, False]),
        clock=clock,
    )
    assert monitor.poll_once() is False
    assert calls == []
    # Dialog appears at t=0: release, held until t=10.
    assert monitor.poll_once() is True
    assert calls == [True]
    # Still present at t=1: re-asserted, hold renewed to t=11.
    clock.now = 1.0
    assert monitor.poll_once() is True
    assert calls == [True, True]
    # Dialog flickers away at t=2: still inside the hold -- no restore.
    clock.now = 2.0
    assert monitor.poll_once() is False
    assert calls == [True, True]
    # Still gone at t=10.9: hold (until 11) not yet elapsed.
    clock.now = 10.9
    assert monitor.poll_once() is False
    assert calls == [True, True]
    # t=11: window elapsed -> suppression restored exactly once.
    clock.now = 11.0
    assert monitor.poll_once() is False
    assert calls == [True, True, False]


def test_monitor_without_handler_is_quiet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(consent, "_yield_handler", None)
    clock = _Clock()
    monitor = consent.ConsentDialogMonitor(
        detector=_Script([True, False]), clock=clock
    )
    assert monitor.poll_once() is True
    clock.now = 100.0
    assert monitor.poll_once() is False
