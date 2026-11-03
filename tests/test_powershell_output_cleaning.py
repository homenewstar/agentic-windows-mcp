"""Issue #5 — PowerShell output hygiene: UTF-8 round-trip, no CLIXML noise,
stdout/stderr returned as separate fields.

Issue #11 — text the child shell double-encoded (UTF-8 decoded as cp1252,
e.g. PS 5.1 Invoke-RestMethod on a charset-less JSON body) is repaired."""

import sys

import pytest

from agentic_windows_mcp.powershell.utils import repair_double_encoded_text, strip_clixml

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


def _mangle(text: str) -> str:
    """Reproduce the damage: UTF-8 bytes decoded with the cp1252 fallback.

    Sample strings must avoid characters whose UTF-8 bytes land on
    cp1252-undefined positions (0x81, 0x8D, 0x8F, 0x90, 0x9D — e.g. the
    closing smart quote U+201D): Python's strict cp1252 decoder rejects
    those bytes outright, while the real Windows decoder substitutes a
    replacement character (unrecoverable either way).
    """
    return text.encode("utf-8").decode("cp1252")


class TestRepairDoubleEncodedText:
    def test_repairs_the_exact_string_observed_in_issue_11(self):
        # Captured live from a PS 5.1 Invoke-RestMethod call against a
        # charset-less JSON API on 2026-11-03.
        assert repair_double_encoded_text("Cuá»™c trÃ² chuyá»‡n má»›i") == "Cuộc trò chuyện mới"

    def test_repairs_generated_mojibake(self):
        for original in (
            "Tiếng Việt có dấu",
            "Xin chào, đây là kiểm tra — dấu ‘ngoặc đơn’ và gạch ngang",
            "Réunion des écoles",
        ):
            assert repair_double_encoded_text(_mangle(original)) == original

    def test_repairs_latin1_style_mojibake(self):
        # The shape PowerShell 5.1 actually produces: UTF-8 bytes mapped
        # byte->codepoint (Latin-1), leaving C1 controls in the string.
        for original in (
            "Cuộc trò chuyện mới",
            "Tôi đang kiểm tra các sự kiện trên Google Lịch",
            "Tiếng Việt có dấu",
        ):
            mangled = original.encode("utf-8").decode("latin-1")
            assert repair_double_encoded_text(mangled) == original

    def test_repairs_the_exact_string_captured_live(self):
        # Codepoints read off desktop-ddpatmg on 2026-11-03 from an
        # Invoke-RestMethod result: byte 0x99/0x87/0x9B became the C1
        # controls U+0099/U+0087/U+009B, so the string cannot re-encode
        # under cp1252 at all — only the latin-1 candidate can undo it.
        mangled = "".join(
            chr(c)
            for c in (
                0x43, 0x75, 0xE1, 0xBB, 0x99, 0x63, 0x20, 0x74, 0x72, 0xC3,
                0xB2, 0x20, 0x63, 0x68, 0x75, 0x79, 0xE1, 0xBB, 0x87, 0x6E,
                0x20, 0x6D, 0xE1, 0xBB, 0x9B, 0x69,
            )
        )
        assert repair_double_encoded_text(mangled) == "Cuộc trò chuyện mới"

    def test_correct_text_is_untouched(self):
        for text in (
            "Cuộc trò chuyện mới",
            "Tiếng Việt có dấu\nsecond line",
            "plain ascii output",
            "café © 2026 — Réunion",
            "",
        ):
            assert repair_double_encoded_text(text) == text

    def test_repairs_per_line_and_leaves_good_lines_alone(self):
        good = "Dòng này đúng sẵn ✓"  # ✓ cannot re-encode as cp1252 either
        text = _mangle("Dòng này bị sai") + "\n" + good + "\n" + "ascii line"
        fixed = repair_double_encoded_text(text)
        assert fixed.split("\n") == ["Dòng này bị sai", good, "ascii line"]

    def test_line_with_unencodable_characters_is_left_alone(self):
        # An emoji on the same line makes the cp1252 re-encode fail, so the
        # line is returned as-is rather than partially rewritten.
        line = _mangle("Xin chào") + " 🎉"
        assert repair_double_encoded_text(line) == line


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
