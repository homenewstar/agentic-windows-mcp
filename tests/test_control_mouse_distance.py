"""Raw motion is measured per device, but under agent-priority it never takes control."""

import ctypes

import pytest

from agentic_windows_mcp.desktop import control, control_hooks


def owner_ready():
    owner = control.ControlCoordinator()
    owner._state = "ready"
    token = owner.begin_call("Type")
    owner._motion_since = 0.0  # Synthetic arrivals use a small, deterministic epoch.
    return owner, token


def test_round_trip_counts_path_length_and_never_invalidates_token():
    owner, token = owner_ready()
    generation = owner._generation
    owner._handle(("raw", 1, 300, 0, 10.0))
    owner._handle(("raw", 1, -199, 0, 10.1))
    owner.checkpoint(token)
    assert owner._generation == generation and owner._suppress
    owner._handle(("raw", 1, -1, 0, 10.2))
    # Agent-priority: crossing the former takeover distance changes nothing;
    # the path length is still measured, but the lease is never preempted.
    assert owner._raw_distance == 500
    assert owner.status()["state"] == "ai"
    assert owner._suppress
    owner.checkpoint(token)


@pytest.mark.parametrize("gap,expected_distance", [(0.499, 500), (0.5, 200), (0.501, 200)])
def test_arrival_gap_boundary_even_when_queue_processing_is_delayed(gap, expected_distance):
    owner, token = owner_ready()
    # These arrivals are deliberately much older than the processing clock.
    owner._handle(("raw", 1, 300, 0, 10.0))
    owner._handle(("raw", 1, 200, 0, 10.0 + gap))
    # The 0.5s arrival gap still resets the measured segment, but under
    # agent-priority no distance ever takes control from the AI.
    assert owner._raw_distance == expected_distance
    assert owner.status()["state"] == "ai"
    owner.checkpoint(token)


def test_zero_motion_does_not_extend_segment():
    owner, token = owner_ready()
    owner._handle(("raw", 1, 300, 0, 10.0))
    owner._handle(("raw", 1, 0, 0, 10.4))
    owner._handle(("raw", 1, 200, 0, 10.5))
    owner.checkpoint(token)
    assert owner._raw_distance == 200


def test_diagonal_distance_and_repeated_small_steps():
    owner, token = owner_ready()
    for i in range(99):
        owner._handle(("raw", 1, 3, 4, 10.0 + i * 0.001))
    owner.checkpoint(token)
    assert owner._raw_distance == 495
    owner._handle(("raw", 1, 3, 4, 10.1))
    assert owner._raw_distance == 500
    assert owner.status()["state"] == "ai"


def test_point_and_raw_are_not_added_together():
    owner, token = owner_ready()
    owner._handle(("point", 0, 0, 0x200))
    owner._handle(("point", 10000, 10000, 0x200))
    owner._handle(("raw", 1, 499, 0))
    owner.checkpoint(token)
    assert owner._raw_distance == 499


def test_new_control_generation_clears_distance():
    owner, _ = owner_ready()
    owner._handle(("raw", 1, 400, 0))
    owner._set_locked("ready")
    token = owner.begin_call("Move")
    owner._handle(("raw", 1, 100, 0))
    owner.checkpoint(token)
    assert owner._raw_distance == 100


def test_event_queued_before_new_control_period_does_not_count():
    owner, token = owner_ready()
    owner._motion_since = 11.0
    owner._handle(("raw", 1, 500, 0, 10.9))
    owner.checkpoint(token)
    assert owner._raw_distance == 0


def test_raw_callback_does_not_pause_and_preserves_arrival_timestamp(monkeypatch):
    owner, token = owner_ready()
    monkeypatch.setattr(control_hooks, "_read_raw_mouse", lambda _: (1, 2, -3))
    monkeypatch.setattr(control_hooks.time, "monotonic", lambda: 42.0)
    owner._raw_input(0)
    assert owner._events.get_nowait() == ("raw", 1, 2, -3, 42.0)
    owner.checkpoint(token)
    assert owner._suppress and not owner._fast_pending


def test_injected_motion_is_not_queued(monkeypatch):
    owner, token = owner_ready()
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)
    data = control._MouseHookData()
    data.flags = 1
    data.pt.x = 50000
    assert owner._physical_mouse(0, 0x200, ctypes.addressof(data)) == 7
    assert owner._events.empty()
    owner.checkpoint(token)


def test_unusable_raw_event_does_not_count(monkeypatch):
    owner, token = owner_ready()
    monkeypatch.setattr(control_hooks, "_read_raw_mouse", lambda _: None)
    owner._raw_input(0)
    assert owner._events.empty()
    owner.checkpoint(token)


@pytest.mark.parametrize("last_step", [199, 200])
async def test_mcp_affine_tool_continues_below_and_at_former_threshold(last_step):
    """Real MCP gate and UIA dispatch; physical input effects are simulated."""
    from fastmcp import Client, FastMCP
    from agentic_windows_mcp.infrastructure.analytics import with_analytics
    from agentic_windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate

    owner = control.ControlCoordinator()
    owner._state = "ready"
    owner._suppress = True
    mcp = FastMCP("cumulative-mouse-motion")
    mcp.add_middleware(ControlToolGate(owner, ControlNotifier(owner)))
    effects = []

    @mcp.tool(name="Click")
    @with_analytics(None, "simulated-input", run_sync_in_thread=False)
    def operation():
        stamp = control.time.monotonic()
        owner._handle(("raw", 1, 300, 0, stamp))
        owner.checkpoint_current()
        effects.append("first")
        owner.record_step_current()
        owner._handle(("raw", 1, -last_step, 0, stamp + 0.01))
        owner.checkpoint_current()
        effects.append("second")
        return "completed"

    try:
        async with Client(mcp) as client:
            result = await client.call_tool("Click", raise_on_error=False)
        # Agent-priority: even the step that reaches the former 500-unit
        # takeover distance completes; motion never preempts a call.
        assert not result.is_error
        assert effects == ["first", "second"]
        assert owner._active_calls == 0
    finally:
        owner.stop()
