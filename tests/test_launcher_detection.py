"""Issue #32 — the start launcher probes for a working interpreter.

Incident #31: start-server.cmd hardcoded the interpreter that ran
`install`; when that Python's executable later vanished, every
supervised relaunch failed instantly while the task reported success.
The shipped launcher must therefore, at every start: try the install
venv first, then each supported `py` tag, verifying candidates by
running them and importing the package; rebuild the venv at the
recorded version when nothing can import it; and fail loudly when no
usable interpreter exists. These tests pin that contract on the
generated assets (the probe itself runs in PowerShell on the target
machine; its live behaviour is verified in the deployment round).
"""

from pathlib import Path

import agentic_windows_mcp.__main__ as cli


def _launcher() -> str:
    return cli._launcher_script()


class TestProbeOrder:
    def test_venv_is_probed_before_py_tags(self):
        script = _launcher()
        venv_probe = script.index("Test-ImportsPackage $venvPy")
        tag_probe = script.index("foreach ($tag in $pyTags)")
        assert venv_probe < tag_probe

    def test_rebuild_comes_after_all_import_probes(self):
        script = _launcher()
        tag_probe = script.index("foreach ($tag in $pyTags)")
        rebuild = script.index("rebuilding venv")
        assert tag_probe < rebuild

    def test_fatal_error_is_the_last_resort(self):
        script = _launcher()
        rebuild = script.index("rebuilding venv")
        fatal = script.index("FATAL: no usable Python")
        assert rebuild < fatal

    def test_candidates_are_verified_by_import_not_existence(self):
        script = _launcher()
        assert 'import agentic_windows_mcp' in script
        assert "Test-ImportsPackage" in script
        # Existence alone is never the venv verdict: the Test-Path is
        # paired with the import check on the same line.
        assert "(Test-Path $venvPy) -and (Test-ImportsPackage $venvPy @())" in script

    def test_supported_tags_newest_first_and_at_least_312(self):
        assert cli._LAUNCHER_PY_TAGS == ("3.14", "3.13", "3.12")
        script = _launcher()
        for tag in cli._LAUNCHER_PY_TAGS:
            assert f"'{tag}'" in script


class TestRebuildContract:
    def test_rebuild_installs_the_recorded_version_not_latest(self):
        script = _launcher()
        assert "version.txt" in script
        assert 'agentic-windows-mcp==$recorded' in script
        assert "-m venv" in script
        assert "-m pip install $spec" in script

    def test_every_start_logs_the_chosen_interpreter(self):
        script = _launcher()
        assert "interpreter: $path (Python $version) source=$source" in script
        # All three selection outcomes funnel through the one serve
        # entry that logs: venv, py tag, rebuilt venv.
        assert script.count("Invoke-Server") >= 4  # definition + 3 call sites

    def test_fatal_path_writes_to_the_error_log_and_exits_nonzero(self):
        script = _launcher()
        assert "Add-Content -Path $errorLog" in script
        assert "server.error.log" in script
        assert "exit 1" in script


class TestStartScript:
    def test_cmd_delegates_to_the_launcher_with_serve_params(
        self, monkeypatch, tmp_path: Path
    ):
        monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cli, "_LAUNCHER_SCRIPT_PATH", tmp_path / "start-server.ps1")
        cmd = cli._build_start_script("streamable-http", "0.0.0.0", 8000)
        assert cmd.startswith("@echo off")
        assert "powershell.exe" in cmd
        assert "-NoProfile" in cmd
        assert str(tmp_path / "start-server.ps1") in cmd
        assert "-Transport streamable-http" in cmd
        assert "-BindHost 0.0.0.0" in cmd
        assert "-Port 8000" in cmd
        assert "server.log" in cmd and "server.error.log" in cmd

    def test_cmd_contains_no_interpreter_path(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cli, "_LAUNCHER_SCRIPT_PATH", tmp_path / "start-server.ps1")
        cmd = cli._build_start_script("sse", "127.0.0.1", 9000)
        assert "python.exe" not in cmd
        assert "python" not in cmd.lower().replace("powershell.exe", "")
