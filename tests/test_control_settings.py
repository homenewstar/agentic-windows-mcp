"""Configured waits and disabled ownership, without touching the real desktop."""

import asyncio
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP

from agentic_windows_mcp.desktop import control
from agentic_windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate


@pytest.mark.parametrize("state", ["ready", "ai"])
def test_long_timeout_does_not_invent_physical_input(monkeypatch, state):
    monkeypatch.setattr(control.time, "monotonic", lambda: 100.0)
    owner = control.ControlCoordinator()
    owner.idle_timeout_seconds = 1000.0
    owner._state = state
    assert owner.status()["state"] == "ready"


@pytest.mark.parametrize("state", ["user", "ready", "ai"])
@pytest.mark.parametrize("timeout", [0.5, 25.0])
def test_configured_idle_boundary(monkeypatch, state, timeout):
    now = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: now[0])
    owner = control.ControlCoordinator()
    owner.idle_timeout_seconds = timeout
    owner._state = state
    owner._last_user = owner._last_physical_event = 100.0
    if state == "ai":
        # Keep the lease live so only the physical-recency rule is exercised.
        owner._active_calls = 1
        owner._lease_until = 100.0 + timeout + 60.0
    now[0] = 100.0 + timeout - 0.01
    if state == "user":
        # Only a chord takeover enters "user"; it holds until the idle edge.
        assert owner.status()["state"] == "user"
        assert owner.status()["wait_seconds"] == pytest.approx(0.01)
        with pytest.raises(control.ControlBlocked):
            owner.begin_call("PowerShell")
    else:
        # Agent-priority: recent physical input never turns ready/ai into a
        # "user" wait, and never blocks an agent call from ready.
        assert owner.status()["state"] == state
        assert owner.status()["wait_seconds"] == 0.0
        if state == "ready":
            token = owner.begin_call("PowerShell")
            owner.checkpoint(token)
            owner.end_call(token)
            with owner._lock:
                owner._set_locked("ready")
            owner._lease_until = 0.0
    now[0] = 100.0 + timeout
    if state == "ai":
        owner._active_calls = 0
        owner._lease_until = 0.0
    assert owner.status()["state"] == "ready"
    token = owner.begin_call("PowerShell")
    owner.checkpoint(token)
    owner.end_call(token)


@pytest.mark.parametrize("held", ["_pressed", "_mouse_down"])
def test_configured_wait_does_not_override_held_input(monkeypatch, held):
    monkeypatch.setattr(control.time, "monotonic", lambda: 100.0)
    owner = control.ControlCoordinator()
    owner.idle_timeout_seconds = 0.5
    owner._state = "user"
    owner._last_physical_event = 90.0
    getattr(owner, held).add(1)
    assert owner.status()["state"] == "user"
    getattr(owner, held).clear()
    assert owner.status()["state"] == "ready"


@pytest.mark.parametrize("elapsed,expected", [(2, "ready"), (4, "ready")])
def test_start_ignores_configured_wait_after_physical_input(monkeypatch, elapsed, expected):
    owner = control.ControlCoordinator()
    owner.idle_timeout_seconds = 3
    owner._last_physical_event = 100.0 - elapsed
    monkeypatch.setattr(control.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(control, "_interactive_desktop", lambda: True)

    def monitor():
        owner._thread_beat = 100.0
        owner._ready.set()
        owner._stop.wait()

    monkeypatch.setattr(owner, "_run", monitor)
    owner.start()
    try:
        assert owner.status()["state"] == expected
    finally:
        owner.stop()


def test_disabled_start_status_and_cleanup(monkeypatch):
    owner = control.ControlCoordinator()
    owner.enabled = False
    owner.idle_timeout_seconds = 2.5
    monitor = Mock(side_effect=AssertionError("must not install hooks"))
    monkeypatch.setattr(control, "_interactive_desktop", monitor)
    listener = Mock()
    owner.subscribe(listener)
    owner.input_ledger.block_new()
    owner.start()
    released = []
    owner.input_ledger.press("key", lambda: None, lambda: released.append(1), lambda: False)
    assert owner.status() == {
        "state": "disabled",
        "enabled": False,
        "policy": "relaxed",
        "idle_timeout_seconds": 2.5,
        "generation": 0,
        "wait_seconds": 0,
        "health": {
            "active_calls": 0,
            "current_call": None,
            "call_age_seconds": 0.0,
            "last_call": None,
            "last_call_seconds": None,
            "watchdog_alive": False,
            "monitor_beat_age_seconds": None,
            "wedged": False,
            "recovery_enabled": False,
            "recovery_events": [],
        },
    }
    assert owner.input_ledger.pending() == ("key",)
    assert not owner._suppress
    assert owner._thread is owner._watchdog is None
    owner.stop()
    assert released == [1]
    assert owner.input_ledger.pending() == ()
    monitor.assert_not_called()
    listener.assert_not_called()


def test_disabled_desktop_drag_still_injects_and_releases(monkeypatch):
    from agentic_windows_mcp.desktop import service

    owner = control.ControlCoordinator()
    owner.enabled = False
    owner.start()
    monkeypatch.setattr(service, "get_controller", lambda: owner)
    monkeypatch.setattr(service, "sleep", lambda _: None)
    press, release, move = Mock(), Mock(), Mock()
    monkeypatch.setattr(service.uia, "PressMouse", press)
    monkeypatch.setattr(service.uia, "ReleaseMouse", release)
    monkeypatch.setattr(service.uia, "SetCursorPos", move)
    desktop = service.Desktop.__new__(service.Desktop)
    try:
        desktop.drag((40, 0), from_loc=(0, 0))
        press.assert_called_once()
        release.assert_called_once()
        move.assert_called_with(40, 0)
        assert owner.input_ledger.pending() == ()
    finally:
        owner.stop()


@pytest.mark.asyncio
async def test_disabled_mcp_executes_serially_without_lease(monkeypatch):
    owner = control.ControlCoordinator()
    owner.enabled = False
    owner.start()
    begin = Mock(side_effect=AssertionError("disabled must not request ownership"))
    monkeypatch.setattr(owner, "begin_call", begin)
    mcp = FastMCP("disabled-test")
    mcp.add_middleware(ControlToolGate(owner, ControlNotifier(owner)))
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []

    @mcp.tool(name="Hold")
    async def hold():
        entered.set()
        await release.wait()
        effects.append("first")

    @mcp.tool(name="Next")
    def next_tool():
        owner.checkpoint_current()
        assert control.current_token.get() is None
        effects.append("second")

    try:
        async with Client(mcp) as client:
            first = asyncio.create_task(client.call_tool("Hold"))
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(client.call_tool("Next"))
            await asyncio.sleep(0.05)
            assert effects == []
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 3)
        assert effects == ["first", "second"]
        begin.assert_not_called()
    finally:
        release.set()
        owner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["user", "unavailable"])
async def test_enabled_faults_never_bypass_gate(state):
    owner = control.ControlCoordinator()
    owner._state = state
    owner._last_user = control.time.monotonic()
    mcp = FastMCP("enabled-test")
    mcp.add_middleware(ControlToolGate(owner, ControlNotifier(owner)))
    effects = []

    @mcp.tool(name="Probe")
    def probe():
        effects.append(1)

    async with Client(mcp) as client:
        result = await client.call_tool("Probe", raise_on_error=False)
        assert result.is_error
    assert effects == []
