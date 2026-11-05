"""Issue #22 — protected-process guard.

kill_process is the single shared termination path; it refuses
session-critical images by EXACT image name (case-insensitive) before
any termination attempt, while lookalike names stay closable.
"""

import psutil
import pytest

from agentic_windows_mcp.process.service import (
    PROTECTED_PROCESSES,
    kill_process,
    protected_process_refusal,
)


class FakeProcess:
    def __init__(self, pid, name):
        self._pid = pid
        self._name = name
        self.info = {"pid": pid, "name": name}
        self.terminated = False
        self.killed = False

    def name(self):
        return self._name

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_psutil(monkeypatch):
    procs = {
        100: FakeProcess(100, "explorer.exe"),
        101: FakeProcess(101, "csrss.exe"),
        102: FakeProcess(102, "notepad.exe"),
        103: FakeProcess(103, "explorer-notes.exe"),
    }
    monkeypatch.setattr(
        psutil, "Process", lambda pid: procs[pid]
    )
    monkeypatch.setattr(
        psutil, "process_iter",
        lambda attrs=None: list(procs.values()),
    )
    return procs


class TestRefusals:
    def test_explorer_by_name_is_refused(self, fake_psutil):
        result = kill_process(name="explorer.exe")
        assert "PROTECTED_PROCESS" in result
        assert "explorer.exe" in result
        assert fake_psutil[100].terminated is False
        assert fake_psutil[100].killed is False

    def test_explorer_by_pid_is_refused(self, fake_psutil):
        result = kill_process(pid=100)
        assert "PROTECTED_PROCESS" in result
        assert fake_psutil[100].terminated is False

    def test_protected_match_is_case_insensitive(self, fake_psutil):
        assert "PROTECTED_PROCESS" in kill_process(name="EXPLORER.EXE")
        assert "PROTECTED_PROCESS" in kill_process(pid=101)  # csrss.exe

    def test_every_listed_image_is_protected(self):
        for image in PROTECTED_PROCESSES:
            assert protected_process_refusal(image) is not None
        assert "explorer.exe" in PROTECTED_PROCESSES


class TestLookalikesStayClosable:
    def test_substring_lookalike_terminates(self, fake_psutil):
        result = kill_process(name="explorer-notes.exe")
        assert "PROTECTED_PROCESS" not in result
        assert result.startswith("Terminated")
        assert fake_psutil[103].terminated is True

    def test_normal_process_by_pid_terminates(self, fake_psutil):
        result = kill_process(pid=102)
        assert result.startswith("Terminated")
        assert "notepad.exe" in result
        assert fake_psutil[102].terminated is True

    def test_refusal_helper_passes_unprotected_names(self):
        assert protected_process_refusal("notepad.exe") is None
        assert protected_process_refusal("my-explorer.exe") is None
        assert protected_process_refusal(None) is None
        assert protected_process_refusal("") is None
