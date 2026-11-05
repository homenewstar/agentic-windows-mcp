"""Issue #10 — Type reliability: long SendKeys text is typed in paced
chunks so loaded targets stop dropping characters, and verify=True reads
the field back and reports landed / mismatch / unavailable instead of
silent success."""

import asyncio
from collections.abc import Callable

import pytest

from agentic_windows_mcp.desktop import service
from agentic_windows_mcp.desktop.service import Desktop, _assess_typed_text
from agentic_windows_mcp.tools.input import register

SINGLE_MONITOR = (0, 0, 1920, 1080)


class TestAssessTypedText:
    def test_clear_with_exact_content_is_verified(self):
        status, _ = _assess_typed_text("old", "hello", "hello", clear=True, caret_position="idle")
        assert status == "verified"

    def test_clear_with_a_dropped_character_is_a_mismatch(self):
        status, detail = _assess_typed_text(
            "old", "hello world", "hello wrld", clear=True, caret_position="idle"
        )
        assert status == "mismatch"
        assert "hello wrld" in detail

    def test_append_at_end_uses_baseline(self):
        status, _ = _assess_typed_text("abc", "def", "abcdef", clear=False, caret_position="end")
        assert status == "verified"

    def test_prepend_at_start_uses_baseline(self):
        status, _ = _assess_typed_text("abc", "def", "defabc", clear=False, caret_position="start")
        assert status == "verified"

    def test_idle_caret_falls_back_to_containment(self):
        # Click landed mid-field: the text sits inside the old content.
        status, _ = _assess_typed_text("abcd", "XY", "abXYcd", clear=False, caret_position="idle")
        assert status == "verified"

    def test_no_baseline_falls_back_to_containment(self):
        status, _ = _assess_typed_text(None, "hello", "say hello there", clear=False, caret_position="end")
        assert status == "verified"
        status, _ = _assess_typed_text(None, "hello", "say hllo there", clear=False, caret_position="end")
        assert status == "mismatch"

    def test_unreadable_field_is_unavailable(self):
        status, detail = _assess_typed_text("old", "hello", None, clear=True, caret_position="idle")
        assert status == "unavailable"
        assert "could not be checked" in detail

    def test_crlf_differences_are_normalized(self):
        status, _ = _assess_typed_text(
            None, "line1\nline2", "line1\r\nline2", clear=True, caret_position="idle"
        )
        assert status == "verified"


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch):
    """A Desktop with recorded uia calls and recorded sleeps."""
    instance = Desktop.__new__(Desktop)
    instance.desktop_state = None
    sleeps: list[float] = []
    keys: list[str] = []
    monkeypatch.setattr(service, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: SINGLE_MONITOR)
    monkeypatch.setattr(service.uia, "Click", lambda *a, **k: None)
    monkeypatch.setattr(
        service.uia, "SendKeys", lambda text, **k: keys.append(text)
    )
    return instance, sleeps, keys


class TestChunkedTyping:
    def test_long_sendkeys_text_pauses_between_chunks(self, rig):
        instance, sleeps, keys = rig
        # Braces force the SendKeys path even above the paste threshold.
        instance.type(loc=(100, 100), text="{" * 50 + "}" * 50)
        assert len(keys) == 100
        assert sleeps == [service.Desktop._TYPE_CHUNK_PAUSE] * 2

    def test_short_text_has_no_chunk_pause(self, rig):
        instance, sleeps, keys = rig
        instance.type(loc=(100, 100), text="{ok}")
        assert len(keys) == 4
        assert sleeps == []


class TestVerifyWiring:
    def test_verify_returns_assessment(self, rig, monkeypatch: pytest.MonkeyPatch):
        instance, _, _ = rig
        reads = iter(["old content", "typed text"])
        monkeypatch.setattr(
            Desktop, "_read_focused_text", lambda self: next(reads)
        )
        monkeypatch.setattr(Desktop, "_focused_is_password", lambda self: False)
        verification, secure = instance.type(
            loc=(100, 100), text="typed text", clear=True, verify=True
        )
        assert secure is False
        assert verification is not None
        assert verification[0] == "verified"

    def test_verify_detects_a_dropped_character(self, rig, monkeypatch: pytest.MonkeyPatch):
        instance, _, _ = rig
        reads = iter(["", "hello wrld"])
        monkeypatch.setattr(
            Desktop, "_read_focused_text", lambda self: next(reads)
        )
        monkeypatch.setattr(Desktop, "_focused_is_password", lambda self: False)
        verification, secure = instance.type(
            loc=(100, 100), text="hello world", clear=True, verify=True
        )
        assert secure is False
        assert verification is not None
        assert verification[0] == "mismatch"

    def test_no_verify_returns_none_and_never_reads(self, rig, monkeypatch: pytest.MonkeyPatch):
        instance, _, _ = rig

        def forbidden(self):
            raise AssertionError("_read_focused_text must not be called without verify")

        monkeypatch.setattr(Desktop, "_read_focused_text", forbidden)
        monkeypatch.setattr(Desktop, "_focused_is_password", lambda self: False)
        verification, secure = instance.type(loc=(100, 100), text="hi")
        assert verification is None
        assert secure is False


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


class FakeDesktop:
    def __init__(self, verification=None) -> None:
        self.type_calls: list[dict[str, object]] = []
        self.verification = verification

    def type(self, **kwargs: object):
        self.type_calls.append(kwargs)
        # Desktop.type returns (verification, secure_target) since #15.
        return self.verification, False


def _tools(desktop: FakeDesktop) -> dict[str, Callable]:
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


class TestTypeToolVerify:
    def test_verify_flag_passes_through_and_reports_pass(self):
        fake = FakeDesktop(("verified", "the field's content matches the typed text"))
        message = asyncio.run(
            _tools(fake)["Type"](text="hello", loc=[100, 100], verify=True)
        )
        assert fake.type_calls[0]["verify"] is True
        assert "Verification: passed" in message

    def test_mismatch_is_reported_loudly(self):
        fake = FakeDesktop(("mismatch", "expected the field to contain 'hello'"))
        message = asyncio.run(
            _tools(fake)["Type"](text="hello", loc=[100, 100], verify="true")
        )
        assert "Verification FAILED" in message

    def test_missing_result_reports_unavailable(self):
        fake = FakeDesktop(None)
        message = asyncio.run(
            _tools(fake)["Type"](text="hello", loc=[100, 100], verify=True)
        )
        assert "Verification unavailable" in message

    def test_verify_rejects_a_non_boolean(self):
        fake = FakeDesktop()
        with pytest.raises(ValueError, match="verify must be true or false"):
            asyncio.run(_tools(fake)["Type"](text="hello", loc=[100, 100], verify="yes"))
        assert fake.type_calls == []

    def test_without_verify_the_message_is_unchanged(self):
        fake = FakeDesktop()
        message = asyncio.run(_tools(fake)["Type"](text="hello", loc=[100, 100]))
        # The verify feature adds nothing; the #13 disposition suffix that
        # every action result carries is the only addition.
        assert message.startswith("Typed hello at (100,100).")
        assert message.endswith(
            "[disposition delivery=delivered_unverified retry=unsafe]"
        )
