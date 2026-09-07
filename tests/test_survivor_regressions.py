"""Regression tests written to kill specific mutation survivors.

Each test names the mutants it kills (mutmut 3.x names). Together with
tests/test_mutmut_api.py they keep the campaign's survivor count at the
waivered floor enforced by scripts/mutation_budget.py in CI; the six
equivalent survivors are recorded in docs/mutation-waivers.md.
"""

from unittest.mock import MagicMock, call, patch

import mutmut_mcp
from mutmut_mcp import (
    _CommandOutcome,
    _get_mutmut_path,
    _parse_results,
    _resolve_venv_path,
    _result_summary,
    _run_command,
    _survivor_names,
    _unresolved_note,
    clean_mutmut_cache,
    rerun_mutmut_on_survivor,
    run_mutmut,
)


class TestRunCommandSurvivors:
    @patch("mutmut_mcp.subprocess.run")
    def test_stderr_separator_after_stdout_without_trailing_newline(self, mock_run):
        # Kills mutmut_mcp.x__run_command__mutmut_21: stderr is appended on its
        # own line even when stdout does not end with a newline.
        mock_run.return_value = MagicMock(returncode=0, stdout="out-no-newline", stderr="deprecated config\n")
        outcome = _run_command(["mutmut", "results"])
        assert outcome == _CommandOutcome("out-no-newline\nWarning: deprecated config\n", failed=False)

    @patch("mutmut_mcp.subprocess.run", side_effect=FileNotFoundError("not found"))
    def test_exception_message_is_preserved(self, mock_run):
        # Kills mutmut_mcp.x__run_command__mutmut_43: the exception's text must
        # reach the caller, not a placeholder.
        outcome = _run_command(["nonexistent_binary"])
        assert outcome.output == "Exception occurred: not found"
        assert outcome.failed

    def test_arguments_reach_the_binary_without_shell_interpretation(self):
        # Kills mutmut_mcp.x__run_command__mutmut_12: argv elements reach the
        # binary verbatim (shell=False). Under a shell the second element would
        # become $0 instead of echo's argument and the output would be empty.
        outcome = _run_command(["echo", "hello; world"])
        assert outcome.output == "hello; world\n"
        assert not outcome.failed


class TestGetMutmutPathSurvivors:
    def test_windows_path(self):
        # Kills mutmut_mcp.x__get_mutmut_path__mutmut_{17,18,20,21,22}: the
        # Windows branch joins venv_path, 'Scripts', 'mutmut.exe', in order.
        with patch("mutmut_mcp.os.name", "nt"):
            path = _get_mutmut_path("/some/venv")
        assert path == "/some/venv/Scripts/mutmut.exe"


class TestResolveVenvPathSurvivors:
    def test_relative_venv_without_project_is_returned_unchanged(self):
        # Kills mutmut_mcp.x__resolve_venv_path__mutmut_1: with no project_path
        # the relative venv_path is returned as-is (the mutant joins onto None).
        assert _resolve_venv_path(".venv", None) == ".venv"

    def test_absolute_venv_with_project_is_returned_unchanged(self):
        # Pins the documented absolute-path semantic (harmless on this mutant).
        assert _resolve_venv_path("/abs/venv", "/proj") == "/abs/venv"


class TestParseResultsSurvivors:
    def test_single_space_indent_is_parsed(self):
        # Kills mutmut_mcp.x__parse_results__mutmut_3: any whitespace-indented
        # line is a candidate, not only multi-space indentation.
        assert _parse_results(" x: killed\n") == [("x", "killed")]

    def test_continues_past_indented_lines_without_status_separator(self):
        # Kills mutmut_mcp.x__parse_results__mutmut_8: a non-mutant indented
        # line is skipped, not treated as end of results.
        output = "    (progress frame)\n    m1: killed\n"
        assert _parse_results(output) == [("m1", "killed")]

    def test_name_side_with_status_colons_is_rejected(self):
        # Kills mutmut_mcp.x__parse_results__mutmut_11: splitting at the LAST
        # ': ' keeps the name free of whitespace; a line whose name side would
        # contain whitespace is not a mutant line.
        assert _parse_results("    a.b: killed: extra\n") == []

    def test_continues_past_empty_name_lines(self):
        # Kills mutmut_mcp.x__parse_results__mutmut_17: an indented ': ' line
        # with no name is skipped, not treated as end of results.
        output = "    : killed\n    m1: killed\n"
        assert _parse_results(output) == [("m1", "killed")]


class TestResultSummarySurvivors:
    @patch("mutmut_mcp._run_mutmut_cli")
    def test_venv_and_project_are_propagated(self, mock_cli):
        # Kills mutmut_mcp.x__result_summary__mutmut_3: results must be read
        # from the caller's venv, not whatever mutmut is on PATH.
        mock_cli.return_value = _CommandOutcome("", failed=False)
        _result_summary(venv_path="v", project_path="p")
        assert mock_cli.call_args == call(["results"], "v", "p")


class TestSurvivorNamesSurvivors:
    @patch("mutmut_mcp._result_summary")
    def test_venv_and_project_are_propagated(self, mock_summary):
        # Kills mutmut_mcp.x__survivor_names__mutmut_{2,3,5}.
        mock_summary.return_value = ({"survived": ["m1"]}, {}, "")
        names, error = _survivor_names("v", "p")
        assert mock_summary.call_args == call("v", "p")
        assert names == ["m1"]
        assert error == ""

    @patch("mutmut_mcp._result_summary")
    def test_no_survivors_returns_empty_list_not_none(self, mock_summary):
        # Kills mutmut_mcp.x__survivor_names__mutmut_{7,9}: callers iterate the
        # names; a missing 'survived' group must read as [].
        mock_summary.return_value = ({}, {}, "")
        names, error = _survivor_names("v", "p")
        assert names == []
        assert error == ""


class TestUnresolvedNoteSurvivors:
    def test_pluralizes_multiple_interrupted_checks(self):
        # Kills mutmut_mcp.x__unresolved_note__mutmut_{27,28}.
        counts = {mutmut_mcp.STATUS_INTERRUPTED: 2}
        assert _unresolved_note(counts) == (
            "2 mutant checks were interrupted by the user — run mutmut again for a complete picture."
        )

    def test_joins_both_notes_with_and(self):
        # Kills mutmut_mcp.x__unresolved_note__mutmut_{33,34}.
        counts = {mutmut_mcp.STATUS_NOT_CHECKED: 1, mutmut_mcp.STATUS_INTERRUPTED: 1}
        assert _unresolved_note(counts) == (
            "1 mutant is not checked and 1 mutant check was interrupted by the user"
            " — run mutmut again for a complete picture."
        )


# ---------------------------------------------------------------------------
# Coverage-completion tests: explicit failure and entry-point paths
# ---------------------------------------------------------------------------


class TestExplicitFailurePaths:
    def test_run_mutmut_reports_missing_project(self):
        result = run_mutmut(venv_path=None, project_path="/definitely/not/a/project")
        assert result == "Error: project_path /definitely/not/a/project is not an existing directory."

    def test_rerun_mutmut_reports_missing_project(self):
        result = rerun_mutmut_on_survivor(mutation_id="m1", project_path="/definitely/not/a/project")
        assert result == "Error: project_path /definitely/not/a/project is not an existing directory."

    @patch("mutmut_mcp._survivor_names", return_value=([], "survivor listing failed"))
    def test_rerun_mutmut_propagates_survivor_listing_error(self, _mock_summary):
        result = rerun_mutmut_on_survivor()
        assert result == "survivor listing failed"

    def test_clean_mutmut_cache_reports_state_removal_failure(self, tmp_path):
        state_dir = tmp_path / "mutants"
        state_dir.mkdir()
        with (
            patch("mutmut_mcp.MUTMUT_STATE_DIR", str(state_dir)),
            patch("mutmut_mcp.MUTMUT_LEGACY_CACHE_PATH", str(tmp_path / ".mutmut-cache")),
            patch("mutmut_mcp.shutil.rmtree", side_effect=OSError("disk busy")),
        ):
            result = clean_mutmut_cache()
        assert "Failed to clear mutmut state" in result
        assert "disk busy" in result


class TestEntryPoint:
    def test_main_runs_the_server(self):
        with patch.object(mutmut_mcp.mcp, "run") as mock_run:
            mutmut_mcp.main()
        assert mock_run.call_count == 1
