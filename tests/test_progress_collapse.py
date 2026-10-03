"""Collapsing mutmut's carriage-return progress frames out of `mutmut run` output.

`tests/fixtures/mutmut_run_output.bin` is the byte-exact stdout of a real `mutmut run`
(3 mutants, captured to a pipe). It must be read in binary: text mode rewrites `\\r`.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mutmut_mcp import _collapse_progress, _decode, _run_command, _run_mutmut_cli, rerun_mutmut_on_survivor, run_mutmut

FIXTURE = Path(__file__).parent / "fixtures" / "mutmut_run_output.bin"
RAW = FIXTURE.read_bytes()


def test_fixture_is_real_cr_framed_output():
    assert RAW.count(b"\r") > 20
    assert b"\r\n" not in RAW
    assert any(line.endswith(b" ") for line in RAW.split(b"\r"))


def test_collapse_keeps_last_frame_per_line():
    out = _collapse_progress(RAW.decode())
    lines = out.split("\n")
    assert "\r" not in out
    assert lines[0] == "⠇ Generating mutants" or lines[0].endswith("Generating mutants")
    assert lines[1].startswith("    done in ") and lines[1].endswith("ms (1 files mutated, 0 ignored, 0 unmodified)")
    assert lines[2].endswith("Running stats") and lines[3] == "    done"
    assert lines[4].endswith("Running clean tests") and lines[5] == "    done"
    assert lines[6].endswith("Running forced fail test") and lines[7] == "    done"
    assert lines[8] == "Running mutation testing"
    assert lines[9].endswith("3/3  🎉 3 🫥 0  ⏰ 0  🤔 0  🙁 0  🔇 0  🧙 0")
    assert lines[10].endswith("mutations/second")
    assert out.endswith("\n")
    assert len(out) < len(RAW.decode()) / 4


def test_collapse_strips_frame_padding():
    assert _collapse_progress("\rfirst   \rsecond    ") == "second"


def test_collapse_leaves_text_without_carriage_returns_untouched():
    text = "    a.x_f__mutmut_1: survived  \n    b: no tests\n\n"
    assert _collapse_progress(text) == text


def test_collapse_does_not_swallow_crlf_content():
    diff = "--- a.py\r\n+++ a.py\r\n@@ -1 +1 @@\r\n-return a + b \r\n+return a - b\r\n"
    assert _collapse_progress(diff) == diff.replace("\r", "")


def _fake_completed(stdout: bytes, stderr: bytes = b"", returncode: int = 0):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


@patch("mutmut_mcp.subprocess.run")
def test_run_command_collapses_only_when_asked(mock_run):
    mock_run.return_value = _fake_completed(RAW)
    collapsed = _run_command(["mutmut", "run"], collapse_progress=True).output
    assert "\r" not in collapsed and collapsed.count("\n") == RAW.count(b"\n")
    assert "3/3  🎉 3" in collapsed and "mutations/second" in collapsed


@patch("mutmut_mcp.subprocess.run")
def test_run_command_without_collapse_matches_text_mode_newlines(mock_run):
    mock_run.return_value = _fake_completed(b"a\r\nb\rc\n  x\n")
    assert _run_command(["mutmut", "show", "m"]).output == "a\nb\nc\n  x\n"


@patch("mutmut_mcp.subprocess.run")
def test_run_mutmut_returns_clean_tally(mock_run):
    mock_run.return_value = _fake_completed(RAW)
    result = run_mutmut()
    assert "\r" not in result
    assert "    done\n" in result
    assert "3/3  🎉 3 🫥 0  ⏰ 0  🤔 0  🙁 0" in result
    assert "mutations/second" in result


@patch("mutmut_mcp.subprocess.run")
def test_rerun_on_survivor_returns_clean_tally(mock_run):
    mock_run.return_value = _fake_completed(RAW)
    result = rerun_mutmut_on_survivor("pkg.x_f__mutmut_1")
    assert "\r" not in result
    assert "mutations/second" in result


@patch("mutmut_mcp.subprocess.run")
def test_stderr_frames_are_collapsed_too(mock_run):
    mock_run.return_value = _fake_completed(b"out\n", b"\rwarn 1   \rwarn 2   \n", returncode=0)
    assert _run_command(["mutmut", "run"], collapse_progress=True).output == "out\nWarning: warn 2\n"


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX python child")
def test_real_subprocess_keeps_cr_frames_until_collapsed():
    code = r"import sys; sys.stdout.write('\rf1   \rf2   \nline\r\n'); sys.stdout.flush()"
    out = _run_command([sys.executable, "-c", code], collapse_progress=True).output
    assert out == "f2\nline\n"


def test_collapse_strips_only_space_padding():
    assert _collapse_progress("\rfX  \r\tframeX \t\n") == "\tframeX \t\n"
    assert _collapse_progress("\rpadX   ") == "padX"


def test_collapse_keeps_only_trailing_cr_distinct_from_frames():
    assert _collapse_progress("x\r \r") == ""
    assert _collapse_progress("a  \r") == "a  "
    assert _collapse_progress("one\r\rtwo \r") == "two"


def test_decode_replaces_invalid_utf8_and_passes_str_through():
    assert _decode(b"ok \xff\xfe end") == "ok �� end"
    assert _decode("already text") == "already text"
    assert _decode("café".encode()) == "café"


@pytest.mark.parametrize(
    "args, collapse",
    [(["run"], True), (["run", "pkg.x_f__mutmut_1"], True), (["results"], False), (["show", "run"], False)],
)
@patch("mutmut_mcp._run_command")
def test_cli_collapses_only_for_run(mock_cmd, args, collapse):
    _run_mutmut_cli(args)
    assert mock_cmd.call_args.kwargs.get("collapse_progress", False) is collapse


def test_collapse_strips_only_carriage_returns_from_line_end():
    assert _collapse_progress("fooX\r") == "fooX"
    assert _collapse_progress("XX\r\r") == "XX"
