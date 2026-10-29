"""Issue #4 — WaitFor system conditions: window_title, tcp_port_open,
process_running, and absent=True semantics."""

import socket
from types import SimpleNamespace

import pytest

from agentic_windows_mcp.tools import input as input_tools
from agentic_windows_mcp.tools.input import (
    _check_process,
    _check_tcp_port,
    _matches_wait_condition,
    _validate_wait_for_args,
)


class TestValidation:
    def test_new_conditions_and_aliases(self):
        assert _validate_wait_for_args("window_title", "X", None, 10, 0.5) == "window_title"
        assert _validate_wait_for_args("title", None, "X", 10, 0.5) == "window_title"
        assert _validate_wait_for_args("tcp_port_open", None, None, 10, 0.5, port=8000) == "tcp_port_open"
        assert _validate_wait_for_args("port", None, None, 10, 0.5, port=8000) == "tcp_port_open"
        assert _validate_wait_for_args("process_running", None, None, 10, 0.5, process_name="uvicorn") == "process_running"
        assert _validate_wait_for_args("process", "uvicorn", None, 10, 0.5) == "process_running"

    def test_missing_arguments_are_clear_errors(self):
        with pytest.raises(ValueError, match="port"):
            _validate_wait_for_args("tcp_port_open", None, None, 10, 0.5)
        with pytest.raises(ValueError, match="port"):
            _validate_wait_for_args("tcp_port_open", None, None, 10, 0.5, port=70000)
        with pytest.raises(ValueError, match="process_name"):
            _validate_wait_for_args("process_running", None, None, 10, 0.5)
        with pytest.raises(ValueError, match="window_title"):
            _validate_wait_for_args("window_title", None, None, 10, 0.5)


class TestTcpPort:
    def test_open_port(self):
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            found, detail = _check_tcp_port("127.0.0.1", port)
            assert found is True
            assert str(port) in detail

    def test_closed_port(self):
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            port = srv.getsockname()[1]
        found, _ = _check_tcp_port("127.0.0.1", port)
        assert found is False


class TestProcess:
    def test_matches_with_or_without_exe_suffix(self, monkeypatch):
        fake = [SimpleNamespace(info={"name": "uvicorn.exe", "pid": 4321})]
        monkeypatch.setattr(input_tools.psutil, "process_iter", lambda attrs: fake)
        found, detail = _check_process("uvicorn")
        assert found is True
        assert "4321" in detail
        found, _ = _check_process("uvicorn.exe")
        assert found is True
        found, _ = _check_process("nginx")
        assert found is False


class TestWindowTitle:
    def _state(self, active, windows):
        return SimpleNamespace(
            active_window=SimpleNamespace(name=active) if active else None,
            windows=[SimpleNamespace(name=w) for w in windows],
            tree_state=None,
        )

    def test_matches_any_top_level_window(self):
        state = self._state("Editor", ["Browser — Settings", "Terminal"])
        found, detail = _matches_wait_condition(state, "window_title", "settings", None)
        assert found is True
        assert "Settings" in detail

    def test_no_match(self):
        state = self._state("Editor", ["Terminal"])
        found, _ = _matches_wait_condition(state, "window_title", "settings", None)
        assert found is False
