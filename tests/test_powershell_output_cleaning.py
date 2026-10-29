"""Issue #5 — PowerShell output hygiene: UTF-8 round-trip, no CLIXML noise,
stdout/stderr returned as separate fields."""

import sys

import pytest

from agentic_windows_mcp.powershell.utils import strip_clixml

CLIXML_SAMPLE = (
    "#< CLIXML\n"
    '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    "<S S=\"Error\">Write-Error: something failed_x000D__x000A_</S>"
    "<S S=\"Error\"> + CategoryInfo          : NotSpecified_x000D__x000A_</S>"
    "</Objs>"
)


class TestStripClixml:
    def test_extracts_readable_strings_and_drops_envelope(self):
        cleaned = strip_clixml(CLIXML_SAMPLE)
        assert "Write-Error: something failed" in cleaned
        assert "CategoryInfo" in cleaned
        assert "<Objs" not in cleaned
        assert "#< CLIXML" not in cleaned
        assert "_x000D_" not in cleaned

    def test_plain_text_passes_through(self):
        assert strip_clixml("Tiếng Việt có dấu\nsecond line") == "Tiếng Việt có dấu\nsecond line"

    def test_legitimate_xml_without_clixml_markers_passes_through(self):
        xml = '<Objs><Item Name="x"/></Objs>'
        assert strip_clixml(xml) == xml

    def test_empty_and_none_like(self):
        assert strip_clixml("") == ""


@pytest.mark.skipif(sys.platform != "win32", reason="needs a real Windows PowerShell")
class TestDetailedExecution:
    def test_utf8_round_trip_byte_exact(self):
        from agentic_windows_mcp.powershell.service import PowerShellExecutor

        stdout, stderr, code = PowerShellExecutor.execute_command_detailed(
            "Write-Output 'Tiếng Việt có dấu'"
        )
        assert code == 0
        assert stdout.strip() == "Tiếng Việt có dấu"

    def test_stderr_is_a_separate_field(self):
        from agentic_windows_mcp.powershell.service import PowerShellExecutor

        stdout, stderr, code = PowerShellExecutor.execute_command_detailed(
            "Write-Output 'out-text'; [Console]::Error.WriteLine('err-text')"
        )
        assert "out-text" in stdout
        assert "err-text" not in stdout
        assert "err-text" in stderr

    def test_progress_records_do_not_pollute_output(self):
        from agentic_windows_mcp.powershell.service import PowerShellExecutor

        stdout, stderr, code = PowerShellExecutor.execute_command_detailed(
            "Write-Progress -Activity 'working' -Status 'half' -PercentComplete 50; "
            "Write-Output 'done'"
        )
        assert "done" in stdout
        assert "CLIXML" not in stdout
        assert "CLIXML" not in stderr
