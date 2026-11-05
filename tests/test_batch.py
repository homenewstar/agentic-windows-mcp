"""Issue #19 — Batch tool: a sequence of actions in one gated call.

Steps run in order through the same Desktop service methods the single
tools use; a failing step stops the batch by default and later steps
are reported "not run"; every step's result carries its message or the
typed error envelope (#13). Validation happens before anything runs.
"""

import asyncio
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="batch steps drive the Windows desktop service"
)

from agentic_windows_mcp.desktop.service import ElementRef, StaleRefError


def _ref(label, name="OK", control_type="Button"):
    return ElementRef(
        label=label, runtime_id=(1,), window_handle=1,
        name=name, control_type=control_type,
    )


class FakeDesktop:
    def __init__(self, fail_invoke=False):
        self.calls = []
        self.fail_invoke = fail_invoke

    def click(self, loc=None, button="left", clicks=1):
        self.calls.append(("click", tuple(loc), button, clicks))

    def type(self, loc=None, text=None, caret_position="idle", clear=False,
             press_enter=False, verify=False):
        self.calls.append(("type", tuple(loc), text))
        return (None, False)

    def invoke_element(self, label, snapshot=None, wait_timeout=3.0):
        self.calls.append(("invoke", label))
        if self.fail_invoke:
            raise StaleRefError("STALE_REF: element 5 no longer exists.")
        return _ref(label)

    def set_element_value(self, label, value, snapshot=None, wait_timeout=3.0):
        self.calls.append(("set", label, value))
        return _ref(label, name="Field", control_type="Edit")

    def toggle_element(self, label, snapshot=None, wait_timeout=3.0):
        self.calls.append(("toggle", label))
        return _ref(label, name="Agree", control_type="CheckBox"), 1

    def shortcut(self, shortcut):
        self.calls.append(("shortcut", shortcut))

    def scroll(self, loc, type, direction, wheel_times):
        self.calls.append(("scroll", loc, direction))
        return None

    def move(self, loc):
        self.calls.append(("move", tuple(loc)))


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *, name, **kwargs):
        def decorator(func):
            self.tools[name] = func
            return func

        return decorator


def _batch(desktop):
    from agentic_windows_mcp.tools import batch

    mcp = FakeMCP()
    batch.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools["Batch"]


def _run(desktop, steps, **kwargs):
    return asyncio.run(_batch(desktop)(steps=steps, **kwargs))


class TestBatchExecution:
    def test_four_step_batch_completes_in_one_call(self):
        desktop = FakeDesktop()
        result = _run(desktop, [
            {"tool": "Click", "loc": [10, 20]},
            {"tool": "SetValue", "ref": "@e1", "value": "Ada"},
            {"tool": "SetValue", "ref": "@e2", "value": "Lovelace"},
            {"tool": "Invoke", "ref": "@e3"},
        ])
        lines = result.splitlines()
        assert lines[0] == "Batch: 4/4 steps succeeded."
        assert all(": ok —" in line for line in lines[1:])
        assert desktop.calls == [
            ("click", (10, 20), "left", 1),
            ("set", 1, "Ada"),
            ("set", 2, "Lovelace"),
            ("invoke", 3),
        ]

    def test_toggle_reports_verified_delivery(self):
        desktop = FakeDesktop()
        result = _run(desktop, [{"tool": "Toggle", "ref": "@e0"}])
        assert "now On" in result
        assert "delivery=delivered_verified" in result

    def test_wait_step(self):
        desktop = FakeDesktop()
        result = _run(desktop, [{"tool": "Wait", "seconds": 0}])
        assert "Waited 0s" in result


class TestBatchFailure:
    def test_failing_middle_step_stops_the_batch(self):
        desktop = FakeDesktop(fail_invoke=True)
        result = _run(desktop, [
            {"tool": "SetValue", "ref": "@e1", "value": "Ada"},
            {"tool": "Invoke", "ref": "@e5"},
            {"tool": "SetValue", "ref": "@e2", "value": "never"},
        ])
        lines = result.splitlines()
        assert lines[0] == "Batch: 1/3 steps succeeded; stopped at step 2 (Invoke)."
        assert ": ok —" in lines[1]
        assert "FAILED" in lines[2]
        assert "code=STALE_REF" in lines[2]
        assert "delivery=not_delivered" in lines[2]
        assert lines[3].endswith("not run.")
        # The third step truly never executed.
        assert ("set", 2, "never") not in desktop.calls

    def test_continue_on_error_runs_the_rest(self):
        desktop = FakeDesktop(fail_invoke=True)
        result = _run(desktop, [
            {"tool": "Invoke", "ref": "@e5"},
            {"tool": "SetValue", "ref": "@e2", "value": "Ada"},
        ], continue_on_error=True)
        lines = result.splitlines()
        assert lines[0] == "Batch: 1/2 steps succeeded."
        assert "FAILED" in lines[1]
        assert ": ok —" in lines[2]
        assert ("set", 2, "Ada") in desktop.calls


class TestBatchValidation:
    def test_unknown_step_tool_fails_before_anything_runs(self):
        desktop = FakeDesktop()
        with pytest.raises(ValueError, match="unknown tool"):
            _run(desktop, [
                {"tool": "Click", "loc": [1, 2]},
                {"tool": "Explode"},
            ])
        assert desktop.calls == []

    def test_missing_arguments_fail_up_front(self):
        desktop = FakeDesktop()
        with pytest.raises(ValueError, match="ref is required"):
            _run(desktop, [{"tool": "Invoke"}])
        with pytest.raises(ValueError, match="non-empty"):
            _run(desktop, [])
        assert desktop.calls == []

    def test_bad_ref_fails_up_front(self):
        desktop = FakeDesktop()
        with pytest.raises(ValueError, match="Invalid element ref"):
            _run(desktop, [{"tool": "Invoke", "ref": "banana"}])
        assert desktop.calls == []
