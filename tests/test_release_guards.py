"""Fault-injection tests for the multi-event release guards (#23).

Every physical sequence that holds state — a click's down/up, a drag's
mouse hold, MultiSelect's Ctrl hold, a SendKeys chord — must release
exactly what it holds on any abort path, and must say
RELEASE_UNCONFIRMED when the release itself cannot be delivered
instead of implying a clean state.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentic_windows_mcp.desktop import service
from agentic_windows_mcp.desktop.control_ledger import InputLedger
from agentic_windows_mcp.desktop.service import Desktop
from agentic_windows_mcp.infrastructure.errors import classify_error
from agentic_windows_mcp.uia import core
from agentic_windows_mcp.uia.enums import KeyboardEventFlag, MouseEventFlag

KEY_UP = int(KeyboardEventFlag.KeyUp)


# ---------------------------------------------------------------------------
# uia click primitives
# ---------------------------------------------------------------------------


def _patch_click_io(monkeypatch, events: list, fail_ups: int = 0):
    state = {"ups": 0}

    def absolute(flag, x, y):
        events.append(int(flag))
        if int(flag) in (
            int(MouseEventFlag.LeftUp),
            int(MouseEventFlag.RightUp),
            int(MouseEventFlag.MiddleUp),
        ):
            state["ups"] += 1
            if state["ups"] <= fail_ups:
                raise OSError("SendInput failed")

    monkeypatch.setattr(core, "AbsoluteMouseEvent", absolute)
    monkeypatch.setattr(core, "SetCursorPos", lambda x, y: None)
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)


class TestClickGuard:
    def test_normal_click_is_one_down_one_up(self, monkeypatch):
        events: list[int] = []
        _patch_click_io(monkeypatch, events)
        core.Click(10, 20)
        assert events == [int(MouseEventFlag.LeftDown), int(MouseEventFlag.LeftUp)]

    def test_failed_up_is_retried_and_original_error_propagates(self, monkeypatch):
        events: list[int] = []
        _patch_click_io(monkeypatch, events, fail_ups=1)
        with pytest.raises(OSError, match="SendInput failed"):
            core.Click(10, 20)
        # The corrective release was attempted after the failed up.
        assert events.count(int(MouseEventFlag.LeftUp)) == 2

    def test_undeliverable_up_reports_release_unconfirmed(self, monkeypatch):
        events: list[int] = []
        _patch_click_io(monkeypatch, events, fail_ups=99)
        with pytest.raises(RuntimeError, match="RELEASE_UNCONFIRMED:") as excinfo:
            core.Click(10, 20)
        code, disposition, _recovery = classify_error(excinfo.value)
        assert code == "RELEASE_UNCONFIRMED"
        assert disposition.delivery == "delivered_unverified"
        assert disposition.retry == "unsafe"

    def test_interrupt_during_hold_still_releases(self, monkeypatch):
        events: list[int] = []
        _patch_click_io(monkeypatch, events)
        sleeps = {"n": 0}

        def interrupting_sleep(seconds):
            sleeps["n"] += 1
            if sleeps["n"] == 1:
                raise KeyboardInterrupt

        monkeypatch.setattr(core.time, "sleep", interrupting_sleep)
        with pytest.raises(KeyboardInterrupt):
            core.Click(10, 20)
        assert events == [int(MouseEventFlag.LeftDown), int(MouseEventFlag.LeftUp)]


# ---------------------------------------------------------------------------
# SendKeys chord sweep
# ---------------------------------------------------------------------------


class TestSendKeysSweep:
    def test_interrupted_chord_releases_the_modifier(self, monkeypatch):
        calls: list[tuple[int, int]] = []

        def keybd_event(vk, scan, flags, extra):
            calls.append((vk, flags))
            if vk == 0x41 and not flags & KEY_UP:
                raise OSError("injection died mid-chord")

        monkeypatch.setattr(core, "keybd_event", keybd_event)
        monkeypatch.setattr(core.time, "sleep", lambda seconds: None)
        with pytest.raises(OSError, match="mid-chord"):
            core.SendKeys("{Ctrl}{A}")
        # After the failure, key-ups were swept for the letter and Ctrl.
        failure_at = next(
            i for i, (vk, flags) in enumerate(calls) if vk == 0x41 and not flags & KEY_UP
        )
        swept = calls[failure_at + 1 :]
        assert any(vk == 0x41 and flags & KEY_UP for vk, flags in swept)
        assert any(vk == 0x11 and flags & KEY_UP for vk, flags in swept)


# ---------------------------------------------------------------------------
# Desktop drag / multi_select (ledger-backed sequences)
# ---------------------------------------------------------------------------


def _desktop() -> Desktop:
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    return desktop


def _stub_control(monkeypatch, owner) -> None:
    monkeypatch.setattr(service, "get_controller", lambda: owner)
    monkeypatch.setattr(service, "sleep", lambda seconds: None)


def _owner(checkpoint_side_effect=None):
    owner = Mock()
    owner.input_ledger = InputLedger()
    owner.physical_mouse_down.return_value = False
    owner.physical_key_down.return_value = False
    if checkpoint_side_effect is not None:
        owner.checkpoint_current = Mock(side_effect=checkpoint_side_effect)
    return owner


class TestDragGuard:
    def test_drag_killed_mid_flight_releases_the_mouse(self, monkeypatch):
        calls: list[str] = []
        owner = _owner(checkpoint_side_effect=[None, None, RuntimeError("preempted")])
        _stub_control(monkeypatch, owner)
        monkeypatch.setattr(
            service.uia, "PressMouse", lambda x, y, **kw: calls.append("down")
        )
        monkeypatch.setattr(service.uia, "SetCursorPos", lambda x, y: None)
        monkeypatch.setattr(
            service.uia, "ReleaseMouse", lambda **kw: calls.append("up")
        )
        desktop = _desktop()
        with pytest.raises(RuntimeError, match="preempted"):
            desktop.drag([100, 200], from_loc=[10, 20], duration="0.25")
        assert calls == ["down", "up"]
        assert owner.input_ledger.pending() == ()

    def test_completed_drag_with_failed_release_is_unconfirmed(self, monkeypatch):
        owner = _owner()
        _stub_control(monkeypatch, owner)
        monkeypatch.setattr(service.uia, "PressMouse", lambda x, y, **kw: None)
        monkeypatch.setattr(service.uia, "SetCursorPos", lambda x, y: None)

        def bad_release(**kw):
            raise OSError("release undeliverable")

        monkeypatch.setattr(service.uia, "ReleaseMouse", bad_release)
        desktop = _desktop()
        with pytest.raises(RuntimeError, match="RELEASE_UNCONFIRMED:"):
            desktop.drag([30, 40], from_loc=[10, 20], duration="0.01")
        # The hold stays on the ledger, visible to the recovery backstop.
        assert owner.input_ledger.pending() == ("mouse:left",)

    def test_aborted_drag_keeps_original_error_when_release_also_fails(
        self, monkeypatch
    ):
        owner = _owner(checkpoint_side_effect=[None, None, RuntimeError("preempted")])
        _stub_control(monkeypatch, owner)
        monkeypatch.setattr(service.uia, "PressMouse", lambda x, y, **kw: None)
        monkeypatch.setattr(service.uia, "SetCursorPos", lambda x, y: None)

        def bad_release(**kw):
            raise OSError("release undeliverable")

        monkeypatch.setattr(service.uia, "ReleaseMouse", bad_release)
        desktop = _desktop()
        with pytest.raises(RuntimeError, match="preempted"):
            desktop.drag([100, 200], from_loc=[10, 20], duration="0.25")


class TestMultiSelectGuard:
    def test_abort_mid_clicks_releases_ctrl(self, monkeypatch):
        calls: list[str] = []
        owner = _owner()
        _stub_control(monkeypatch, owner)
        monkeypatch.setattr(
            service.uia, "PressKey", lambda key, **kw: calls.append("ctrl-down")
        )
        monkeypatch.setattr(
            service.uia, "ReleaseKey", lambda key, **kw: calls.append("ctrl-up")
        )
        clicks = {"n": 0}

        def flaky_click(x, y, **kw):
            clicks["n"] += 1
            calls.append(f"click{clicks['n']}")
            if clicks["n"] == 2:
                raise OSError("click died")

        monkeypatch.setattr(service.uia, "Click", flaky_click)
        desktop = _desktop()
        with pytest.raises(OSError, match="click died"):
            desktop.multi_select(
                press_ctrl=True, locs=[(10, 10), (20, 20), (30, 30)]
            )
        assert calls[0] == "ctrl-down"
        assert calls[-1] == "ctrl-up"
        assert "click3" not in calls
        assert owner.input_ledger.pending() == ()
