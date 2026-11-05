"""Issue #15 — secure-field (IsPassword) redaction contract.

Read side: a password node's value never reaches snapshot text (the capture
withholds it, and the renderer refuses to print one even if present).
Write side: SetValue/Type into a secure field never echo the value in the
tool result, and Type never reads a secure field back for verification.
"""

import sys

import pytest

from agentic_windows_mcp.tree.views import (
    BoundingBox,
    TreeElementNode,
    TreeState,
    _node_meta_str,
)

SECRET = "hunter2-correct-horse"


def _node(metadata, name="Password"):
    box = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
    return TreeElementNode(
        bounding_box=box,
        center=box.get_center(),
        name=name,
        control_type="Edit",
        window_name="Login",
        metadata=metadata,
    )


class TestRenderRedaction:
    def test_password_value_is_not_rendered(self):
        meta = _node_meta_str({"is_password": True, "value": SECRET})
        assert "password" in meta
        assert SECRET not in meta

    def test_plain_value_still_rendered(self):
        meta = _node_meta_str({"value": "some text"})
        assert 'value:"some text"' in meta

    def test_snapshot_text_contains_no_secret(self):
        state = TreeState(
            interactive_nodes=[
                _node({"is_password": True, "value": SECRET}),
                _node({"value": "visible"}, name="Username"),
            ]
        )
        text = state.interactive_elements_to_string()
        assert SECRET not in text
        assert "visible" in text
        assert "password" in text


@pytest.mark.skipif(sys.platform != "win32", reason="drives the Windows UIA stack")
class TestWriteSide:
    def test_ref_carries_is_password_from_snapshot_metadata(self, monkeypatch):
        from agentic_windows_mcp.desktop.service import Desktop

        desktop = Desktop.__new__(Desktop)
        desktop._refs_generation = 0
        desktop._element_refs = {}
        desktop.register_element_refs(
            TreeState(interactive_nodes=[_node({"is_password": True})])
        )
        assert desktop._element_refs[0].is_password is True

    def test_set_value_tool_does_not_echo_a_password(self):
        import asyncio

        from agentic_windows_mcp.desktop.service import ElementRef
        from agentic_windows_mcp.tools.elements import register

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self, *, name, **kwargs):
                def decorator(func):
                    self.tools[name] = func
                    return func

                return decorator

        class FakeDesktop:
            def set_element_value(self, label, value, snapshot, wait_timeout=3.0):
                return ElementRef(
                    label=label, runtime_id=(1,), window_handle=1,
                    name="Password", control_type="Edit", is_password=True,
                )

        mcp = FakeMCP()
        register(mcp, get_desktop=lambda: FakeDesktop(), get_analytics=lambda: None)
        result = asyncio.run(mcp.tools["SetValue"](ref="@e0", value=SECRET))
        assert SECRET not in result
        assert "secure field" in result

    def test_type_into_secure_field_never_reads_back(self, monkeypatch):
        from agentic_windows_mcp.desktop import service
        from agentic_windows_mcp.desktop.service import Desktop

        instance = Desktop.__new__(Desktop)
        instance.desktop_state = None
        monkeypatch.setattr(service, "sleep", lambda seconds: None)
        monkeypatch.setattr(
            service.uia, "GetVirtualScreenRect", lambda: (0, 0, 1920, 1080)
        )
        monkeypatch.setattr(service.uia, "Click", lambda *a, **k: None)
        monkeypatch.setattr(service.uia, "SendKeys", lambda *a, **k: None)
        monkeypatch.setattr(Desktop, "_focused_is_password", lambda self: True)

        def forbidden(self):
            raise AssertionError("a secure field's value must never be read")

        monkeypatch.setattr(Desktop, "_read_focused_text", forbidden)
        verification, secure = instance.type(
            loc=(100, 100), text=SECRET, verify=True
        )
        assert secure is True
        assert verification is not None
        assert verification[0] == "unavailable"
        assert SECRET not in verification[1]

    def test_type_tool_does_not_echo_a_password(self):
        import asyncio

        from agentic_windows_mcp.tools.input import register

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self, *, name, **kwargs):
                def decorator(func):
                    self.tools[name] = func
                    return func

                return decorator

        class FakeDesktop:
            desktop_state = object()

            def type(self, **kwargs):
                return None, True  # (verification, secure_target)

        mcp = FakeMCP()
        register(mcp, get_desktop=lambda: FakeDesktop(), get_analytics=lambda: None)
        result = asyncio.run(mcp.tools["Type"](text=SECRET, loc=[100, 100]))
        assert SECRET not in result
        assert "secure" in result
