"""Tests for the remote-debugging consent dialog matcher and switch.

Only the pure logic is exercised here: the UIA/Win32 layer only runs on
Windows inside the live server, but the decision of WHAT may be clicked
must never widen beyond the exact Chrome consent prompt.
"""

from __future__ import annotations

import sys
import types

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


def test_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WINDOWS_MCP_AUTO_ALLOW_REMOTE_DEBUGGING", "0")
    assert consent.auto_allow_enabled() is False
    monkeypatch.setenv("WINDOWS_MCP_AUTO_ALLOW_REMOTE_DEBUGGING", "false")
    assert consent.auto_allow_enabled() is False
    monkeypatch.setenv("WINDOWS_MCP_AUTO_ALLOW_REMOTE_DEBUGGING", "1")
    assert consent.auto_allow_enabled() is True


def test_default_true_without_env_or_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("WINDOWS_MCP_AUTO_ALLOW_REMOTE_DEBUGGING", raising=False)
    fake_config = types.ModuleType("windows_mcp.infrastructure.config")

    class _Browser:
        auto_allow_remote_debugging = True

    class _Cfg:
        browser = _Browser()

    fake_config.discover_config_path = lambda _p: None  # type: ignore[attr-defined]
    fake_config.load_config = lambda _p: _Cfg()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "windows_mcp.infrastructure.config", fake_config)
    assert consent.auto_allow_enabled() is True
