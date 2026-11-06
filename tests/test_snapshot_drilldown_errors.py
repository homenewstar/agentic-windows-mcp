"""Issue #33 — the Snapshot drill-down failure surface.

Whatever fails while resolving a root_ref or capturing its subtree,
the caller must receive the typed envelope (code / delivery / retry)
from issue #13 — never a naked COM tuple like the live failure
``(-2147220991, 'An event was unable to invoke any of the subscribers',
...)`` (0x80040201 = UIA_E_ELEMENTNOTAVAILABLE).
"""

import asyncio
import sys
from _ctypes import COMError
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="snapshot tools drive the Windows UIA stack"
)

from agentic_windows_mcp.desktop.service import StaleRefError


def _unavailable_error():
    return COMError(
        -2147220991,
        "An event was unable to invoke any of the subscribers",
        (None, None, None, 0, None),
    )


class _RefDesktop:
    def __init__(self, resolve_error=None):
        self._resolve_error = resolve_error

    def resolve_element_ref(self, label):
        if self._resolve_error is not None:
            raise self._resolve_error
        ref = SimpleNamespace(
            control_type="Document", name="doc", window_handle=4242
        )
        return ref, SimpleNamespace()


def _snapshot_tool(monkeypatch, desktop, capture_error=None):
    from agentic_windows_mcp.tools import snapshot as snapshot_tools
    from tests.test_multi_tools import FakeMCP

    def fake_capture(desktop_arg, **kwargs):
        if capture_error is not None:
            raise capture_error
        return {}

    monkeypatch.setattr(snapshot_tools, "capture_desktop_state", fake_capture)
    monkeypatch.setattr(
        snapshot_tools,
        "build_snapshot_response",
        lambda result, include_ui_details: ["ok"],
    )
    mcp = FakeMCP()
    snapshot_tools.register(
        mcp, get_desktop=lambda: desktop, get_analytics=lambda: None
    )
    return mcp.tools["Snapshot"]


class TestDrillDownResolveFailures:
    def test_unavailable_element_is_stale_ref_with_envelope(self, monkeypatch):
        tool = _snapshot_tool(
            monkeypatch, _RefDesktop(resolve_error=_unavailable_error())
        )
        with pytest.raises(ValueError) as excinfo:
            asyncio.run(tool(root_ref="@e22"))
        text = str(excinfo.value)
        assert text.startswith("STALE_REF:")
        assert "[error code=STALE_REF delivery=not_delivered retry=safe" in text
        assert "recovery=refresh_snapshot_then_retry_original]" in text

    def test_other_com_failure_is_capture_failed_with_envelope(self, monkeypatch):
        tool = _snapshot_tool(
            monkeypatch,
            _RefDesktop(
                resolve_error=COMError(
                    -2147220988, "not supported", (None, None, None, 0, None)
                )
            ),
        )
        with pytest.raises(ValueError) as excinfo:
            asyncio.run(tool(root_ref="@e22"))
        text = str(excinfo.value)
        assert text.startswith("UIA_CAPTURE_FAILED:")
        assert (
            "[error code=UIA_CAPTURE_FAILED delivery=not_delivered retry=safe"
            in text
        )


class TestDrillDownCaptureFailures:
    def test_capture_failure_text_carries_the_envelope(self, monkeypatch):
        tool = _snapshot_tool(
            monkeypatch,
            _RefDesktop(),
            capture_error=StaleRefError(
                "STALE_REF: the drill-down root element is no longer available"
            ),
        )
        result = asyncio.run(tool(root_ref="@e22"))
        text = result[0]
        assert "STALE_REF:" in text
        assert "[error code=STALE_REF delivery=not_delivered retry=safe" in text
        assert "recovery=refresh_snapshot_then_retry_original]" in text

    def test_raw_com_tuple_is_never_the_whole_answer(self, monkeypatch):
        tool = _snapshot_tool(
            monkeypatch, _RefDesktop(), capture_error=_unavailable_error()
        )
        result = asyncio.run(tool(root_ref="@e22"))
        text = result[0]
        assert text.startswith("Error capturing desktop state:")
        assert "[error code=" in text
