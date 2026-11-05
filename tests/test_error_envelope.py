"""Issue #13 — typed error envelope + delivery disposition.

Failures from action tools carry a machine-readable code and a disposition
(delivery + retry) appended to the original human message; successes carry
the disposition. Only not_delivered is safe to retry.
"""

import asyncio

import pytest

from agentic_windows_mcp.infrastructure.errors import (
    DELIVERED_VERIFIED,
    classify_error,
    retry_for,
    with_disposition,
)


class GateBlocked(RuntimeError):
    """Mimics the control gate's ControlBlocked (code attribute + text)."""

    def __init__(self, code):
        self.code = code
        super().__init__(f"{code}: unavailable")


class TestClassify:
    def test_stale_ref_prefix(self):
        code, disp, recovery = classify_error(
            ValueError("STALE_REF: element 3 no longer exists")
        )
        assert code == "STALE_REF"
        assert (disp.delivery, disp.retry) == ("not_delivered", "safe")
        assert recovery == "refresh_snapshot_then_retry_original"

    def test_perm_denied_prefix(self):
        code, disp, _ = classify_error(
            PermissionError("PERM_DENIED: target runs higher (findings W-002)")
        )
        assert code == "PERM_DENIED"
        assert disp.delivery == "not_delivered" and disp.retry == "safe"

    def test_unsupported_pattern_prefix(self):
        code, disp, _ = classify_error(
            ValueError("UNSUPPORTED_PATTERN: no InvokePattern")
        )
        assert code == "ACTION_NOT_SUPPORTED"
        assert disp.retry == "safe"

    def test_gate_block_is_policy_denied_not_delivered(self):
        code, disp, recovery = classify_error(GateBlocked("CONTROL_UNAVAILABLE"))
        assert code == "POLICY_DENIED"
        assert (disp.delivery, disp.retry) == ("not_delivered", "safe")
        assert recovery == "inspect_state_then_retry_original"

    def test_timeout_is_unknown(self):
        code, disp, _ = classify_error(TimeoutError("timed out"))
        assert code == "TIMEOUT"
        assert (disp.delivery, disp.retry) == ("unknown", "unknown")

    def test_plain_value_error_is_invalid_args(self):
        code, disp, _ = classify_error(ValueError("loc must be [x, y]"))
        assert code == "INVALID_ARGS"
        assert disp.retry == "safe"

    def test_runtime_error_is_action_failed_uncertain(self):
        code, disp, _ = classify_error(RuntimeError("InvokePattern.Invoke failed"))
        assert code == "ACTION_FAILED"
        assert (disp.delivery, disp.retry) == ("delivery_uncertain", "unsafe")

    def test_unmapped_exception_is_internal(self):
        code, disp, _ = classify_error(Exception("boom"))
        assert code == "INTERNAL"
        assert disp.retry == "unknown"

    def test_retry_derivation(self):
        assert retry_for("not_delivered") == "safe"
        assert retry_for("delivered_unverified") == "unsafe"
        assert retry_for("delivered_verified") == "unsafe"
        assert retry_for("delivery_uncertain") == "unsafe"
        assert retry_for("unknown") == "unknown"


class TestDecorator:
    def test_success_gains_disposition_suffix(self):
        @with_disposition
        def act():
            return "Clicked."

        assert act() == (
            "Clicked.\n[disposition delivery=delivered_unverified retry=unsafe]"
        )

    def test_verified_success_suffix(self):
        @with_disposition(success=DELIVERED_VERIFIED)
        def act():
            return "Toggled."

        assert act().endswith("[disposition delivery=delivered_verified retry=unsafe]")

    def test_non_string_result_passes_through(self):
        @with_disposition
        def act():
            return {"ok": True}

        assert act() == {"ok": True}

    def test_error_keeps_type_prefix_and_gains_envelope(self):
        @with_disposition
        def act():
            raise ValueError("STALE_REF: gone")

        with pytest.raises(ValueError) as excinfo:
            act()
        message = str(excinfo.value)
        assert message.startswith("STALE_REF: gone")
        assert "code=STALE_REF" in message
        assert "delivery=not_delivered" in message
        assert "retry=safe" in message
        assert "recovery=refresh_snapshot_then_retry_original" in message

    def test_runtime_error_envelope_is_unsafe(self):
        @with_disposition
        def act():
            raise RuntimeError("dispatch exploded")

        with pytest.raises(RuntimeError) as excinfo:
            act()
        assert "code=ACTION_FAILED" in str(excinfo.value)
        assert "retry=unsafe" in str(excinfo.value)


class TestToolLevel:
    """Through a real registered tool (FakeMCP), like callers see it."""

    def _tools(self, desktop):
        from agentic_windows_mcp.tools.input import register

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self, *, name, **kwargs):
                def decorator(func):
                    self.tools[name] = func
                    return func

                return decorator

        mcp = FakeMCP()
        register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
        return mcp.tools

    def test_click_success_carries_disposition(self):
        class FakeDesktop:
            desktop_state = object()

            def click(self, **kwargs):
                return None

        result = asyncio.run(self._tools(FakeDesktop())["Click"](loc=[10, 20]))
        assert "Single left clicked at (10,20)." in result
        assert result.endswith(
            "[disposition delivery=delivered_unverified retry=unsafe]"
        )

    def test_click_gate_block_is_policy_denied_safe(self):
        class FakeDesktop:
            desktop_state = object()

            def click(self, **kwargs):
                raise GateBlocked("CONTROL_UNAVAILABLE")

        with pytest.raises(RuntimeError) as excinfo:
            asyncio.run(self._tools(FakeDesktop())["Click"](loc=[10, 20]))
        message = str(excinfo.value)
        assert "code=POLICY_DENIED" in message
        assert "delivery=not_delivered" in message
        assert "retry=safe" in message
