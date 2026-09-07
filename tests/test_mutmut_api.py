import asyncio
import os
import threading
from unittest.mock import MagicMock, patch

import pytest

import mutmut_mcp
from mutmut_mcp import (
    _canonical_project_path,
    _CommandOutcome,
    _get_mutmut_path,
    _names_by_status,
    _parse_results,
    _project_lock,
    _result_summary,
    _run_command,
    _run_mutmut_cli,
    _status_counts,
    _survivor_names,
    clean_mutmut_cache,
    prioritize_survivors,
    rerun_mutmut_on_survivor,
    run_mutmut,
    show_mutant,
    show_results,
    show_survivors,
)

# A realistic snippet of `mutmut results` output (mutmut 3.x format:
# "    <mutant_name>: <status>", killed mutants omitted by default).
RESULTS_OUTPUT = (
    "    mymodule.x_core_logic__mutmut_1: survived\n"
    "    mymodule.x_logger_setup__mutmut_1: survived\n"
    "    mymodule.x_helper__mutmut_2: no tests\n"
)

# A multi-line traceback as `_run_command` appends it: only the first line carries the
# label, so the body lines arrive indented and can contain ": ".
TRACEBACK_STDERR = (
    "Error: Traceback (most recent call last):\n"
    '  File "/tmp/proj/run.py", line 3, in <module>\n'
    '    raise ValueError("bad: thing")\n'
    "ValueError: bad: thing\n"
)

# ---------------------------------------------------------------------------
# _run_command
# ---------------------------------------------------------------------------


class TestRunCommand:
    @patch("mutmut_mcp.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        outcome = _run_command(["echo", "ok"])
        assert outcome == _CommandOutcome("ok\n", failed=False)

    @patch("mutmut_mcp.subprocess.run")
    def test_nonzero_exit_with_stdout(self, mock_run):
        # mutmut exits non-zero when survivors are found; that is a result, not a failure
        # of the call — but the exit code is still carried as explicit status for callers.
        mock_run.return_value = MagicMock(returncode=1, stdout="survived: 2\n", stderr="")
        outcome = _run_command(["mutmut", "run"])
        assert outcome == _CommandOutcome("survived: 2\n", failed=True)

    @patch("mutmut_mcp.subprocess.run")
    def test_nonzero_exit_with_stderr_only(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fail\n")
        outcome = _run_command(["false"])
        assert outcome == _CommandOutcome("Error: fail\n", failed=True)

    @patch("mutmut_mcp.subprocess.run")
    def test_nonzero_exit_with_stdout_and_stderr_is_an_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="partial\n", stderr="fail\n")
        outcome = _run_command(["mutmut", "results"])
        assert outcome == _CommandOutcome("partial\nError: fail\n", failed=True)

    @patch("mutmut_mcp.subprocess.run")
    def test_success_with_stderr_is_a_warning_not_an_error(self, mock_run):
        # mutmut 3.7 warns on stderr about deprecated config while still succeeding; the
        # stdout must survive and the result must not read as a failed call.
        mock_run.return_value = MagicMock(returncode=0, stdout=RESULTS_OUTPUT, stderr="deprecated config\n")
        outcome = _run_command(["mutmut", "results"])
        assert not outcome.output.startswith("Error")
        assert outcome == _CommandOutcome(f"{RESULTS_OUTPUT}Warning: deprecated config\n", failed=False)

    @patch("mutmut_mcp.subprocess.run")
    def test_success_with_stderr_only_is_not_an_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="deprecated config\n")
        outcome = _run_command(["mutmut", "results"])
        assert outcome == _CommandOutcome("Warning: deprecated config\n", failed=False)

    @patch("mutmut_mcp.subprocess.run", side_effect=FileNotFoundError("not found"))
    def test_exception(self, mock_run):
        outcome = _run_command(["nonexistent_binary"])
        assert "Exception" in outcome.output
        assert outcome.failed


# ---------------------------------------------------------------------------
# _get_mutmut_path
# ---------------------------------------------------------------------------


class TestGetMutmutPath:
    def test_unix_path(self):
        with patch("mutmut_mcp.os.name", "posix"):
            path = _get_mutmut_path("/some/venv")
            assert path == "/some/venv/bin/mutmut"

    def test_windows_path(self):
        with patch("mutmut_mcp.os.name", "nt"):
            path = _get_mutmut_path("C:\\venv")
            assert path.endswith("mutmut.exe")


# ---------------------------------------------------------------------------
# _run_mutmut_cli
# ---------------------------------------------------------------------------


class TestRunMutmutCli:
    @patch("mutmut_mcp._run_command")
    def test_without_venv(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome("results", failed=False)
        outcome = _run_mutmut_cli(["results"])
        mock_cmd.assert_called_once_with(["mutmut", "results"], cwd=os.path.abspath("."))
        assert outcome == _CommandOutcome("results", failed=False)

    @patch("mutmut_mcp.os.path.exists", return_value=True)
    @patch("mutmut_mcp._run_command")
    def test_with_venv(self, mock_cmd, mock_exists):
        mock_cmd.return_value = _CommandOutcome("results", failed=False)
        _run_mutmut_cli(["results"], venv_path="/my/venv")
        # Should use venv mutmut binary
        call_args = mock_cmd.call_args[0][0]
        assert "mutmut" in call_args[0]
        assert "results" in call_args

    @patch("mutmut_mcp.os.path.exists", return_value=False)
    def test_with_missing_venv(self, mock_exists):
        outcome = _run_mutmut_cli(["results"], venv_path="/missing/venv")
        assert "Error" in outcome.output
        assert "not found" in outcome.output
        assert outcome.failed


# ---------------------------------------------------------------------------
# project_path scoping
# ---------------------------------------------------------------------------


class TestProjectPath:
    @patch("mutmut_mcp.subprocess.run")
    def test_run_command_passes_cwd(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        _run_command(["mutmut", "results"], cwd=str(tmp_path))
        assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)

    @patch("mutmut_mcp.subprocess.run")
    def test_run_command_defaults_to_no_cwd(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        _run_command(["mutmut", "results"])
        assert mock_run.call_args.kwargs["cwd"] is None

    @patch("mutmut_mcp._run_command")
    def test_cli_runs_in_project_path(self, mock_cmd, tmp_path):
        mock_cmd.return_value = _CommandOutcome("results", failed=False)
        _run_mutmut_cli(["results"], project_path=str(tmp_path))
        assert mock_cmd.call_args.kwargs["cwd"] == str(tmp_path)

    @patch("mutmut_mcp._run_command")
    def test_cli_canonicalizes_default_project_path(self, mock_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_cmd.return_value = _CommandOutcome("results", failed=False)

        _run_mutmut_cli(["results"])

        assert mock_cmd.call_args.kwargs["cwd"] == str(tmp_path)

    @patch("mutmut_mcp._run_command")
    def test_relative_venv_resolved_against_project_path(self, mock_cmd, tmp_path):
        bin_dir = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        bin_dir.mkdir(parents=True)
        binary = bin_dir / ("mutmut.exe" if os.name == "nt" else "mutmut")
        binary.write_text("")
        mock_cmd.return_value = _CommandOutcome("results", failed=False)

        _run_mutmut_cli(["results"], venv_path=".venv", project_path=str(tmp_path))

        assert mock_cmd.call_args[0][0][0] == str(binary)
        assert mock_cmd.call_args.kwargs["cwd"] == str(tmp_path)

    @pytest.mark.skipif(os.name == "nt", reason="test executable uses a POSIX shell script")
    def test_relative_project_and_venv_share_absolute_project_base(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        bin_dir = project / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        binary = bin_dir / "mutmut"
        binary.write_text("#!/bin/sh\npwd\n")
        binary.chmod(0o755)
        monkeypatch.chdir(tmp_path)

        result = _run_mutmut_cli(["results"], venv_path=".venv", project_path="project")

        assert result.output.strip() == str(project)

    @patch("mutmut_mcp._run_command")
    @patch("mutmut_mcp.os.path.exists", return_value=True)
    def test_absolute_venv_not_rebased_on_project_path(self, mock_exists, mock_cmd, tmp_path):
        mock_cmd.return_value = _CommandOutcome("results", failed=False)
        _run_mutmut_cli(["results"], venv_path="/abs/venv", project_path=str(tmp_path))
        assert mock_cmd.call_args[0][0][0].startswith("/abs/venv")

    @patch("mutmut_mcp._run_command")
    def test_missing_project_path_errors_without_running(self, mock_cmd, tmp_path):
        outcome = _run_mutmut_cli(["results"], project_path=str(tmp_path / "nope"))
        assert "Error" in outcome.output
        assert "not an existing directory" in outcome.output
        assert outcome.failed
        mock_cmd.assert_not_called()

    @patch("mutmut_mcp._run_command")
    def test_file_project_path_errors_without_running(self, mock_cmd, tmp_path):
        a_file = tmp_path / "setup.cfg"
        a_file.write_text("[mutmut]\n")
        result = show_results(project_path=str(a_file))
        assert "Error" in result
        mock_cmd.assert_not_called()

    @patch("mutmut_mcp._run_command")
    def test_tools_thread_project_path(self, mock_cmd, tmp_path):
        mock_cmd.return_value = _CommandOutcome(RESULTS_OUTPUT, failed=False)
        project = str(tmp_path)
        run_mutmut(project_path=project)
        show_results(project_path=project)
        show_survivors(project_path=project)
        show_mutant("mymodule.x_add__mutmut_1", project_path=project)
        rerun_mutmut_on_survivor(mutation_id="mymodule.x_add__mutmut_1", project_path=project)
        prioritize_survivors(project_path=project)
        assert mock_cmd.call_count == 6
        assert all(call.kwargs["cwd"] == project for call in mock_cmd.call_args_list)


# ---------------------------------------------------------------------------
# _parse_results
# ---------------------------------------------------------------------------


class TestParseResults:
    def test_parses_name_and_status(self):
        parsed = _parse_results(RESULTS_OUTPUT)
        assert ("mymodule.x_core_logic__mutmut_1", "survived") in parsed
        # "no tests" status has a space but the mutant name has none, so it splits cleanly.
        assert ("mymodule.x_helper__mutmut_2", "no tests") in parsed

    def test_ignores_blank_and_headerless_lines(self):
        assert _parse_results("\n\nGenerating mutants\n") == []

    def test_parses_every_real_mutmut_status_unchanged(self):
        # A results block in mutmut's own shape, including the two statuses whose text
        # carries whitespace while the mutant name does not.
        block = (
            "    mymodule.x_a__mutmut_1: survived\n"
            "    mymodule.x_b__mutmut_1: no tests\n"
            "    mymodule.x_c__mutmut_1: not checked\n"
            "    mymodule.x_d__mutmut_1: check was interrupted by user\n"
        )
        assert _parse_results(block) == [
            ("mymodule.x_a__mutmut_1", "survived"),
            ("mymodule.x_b__mutmut_1", "no tests"),
            ("mymodule.x_c__mutmut_1", "not checked"),
            ("mymodule.x_d__mutmut_1", "check was interrupted by user"),
        ]

    def test_ignores_unindented_annotated_stderr(self):
        # `_run_command` appends stderr to stdout with a label; the label line is not
        # indented, and its extracted "name" ('Warning') carries no whitespace, so only
        # the indentation check rejects it.
        output = RESULTS_OUTPUT + "Warning: some_config is deprecated\n"
        assert _parse_results(output) == _parse_results(RESULTS_OUTPUT)

    def test_ignores_traceback_lines_whose_name_contains_whitespace(self):
        # Only the first stderr line gets the label, so a traceback's body arrives
        # indented; its extracted "name" contains whitespace, which is what rejects it.
        output = RESULTS_OUTPUT + TRACEBACK_STDERR
        assert _parse_results(output) == _parse_results(RESULTS_OUTPUT)

    def test_junk_never_reaches_the_parsed_pairs(self):
        parsed = _parse_results(RESULTS_OUTPUT + "Warning: some_config is deprecated\n" + TRACEBACK_STDERR)
        assert parsed == [
            ("mymodule.x_core_logic__mutmut_1", "survived"),
            ("mymodule.x_logger_setup__mutmut_1", "survived"),
            ("mymodule.x_helper__mutmut_2", "no tests"),
        ]


# ---------------------------------------------------------------------------
# _names_by_status / _survivor_names
# ---------------------------------------------------------------------------


class TestNamesByStatus:
    @patch("mutmut_mcp._run_mutmut_cli")
    def test_groups_every_status(self, mock_cli):
        mock_cli.return_value = _CommandOutcome(
            RESULTS_OUTPUT + "    mymodule.x_slow__mutmut_1: timeout\n", failed=False
        )
        grouped, error = _names_by_status()
        assert error == ""
        assert grouped == {
            "survived": ["mymodule.x_core_logic__mutmut_1", "mymodule.x_logger_setup__mutmut_1"],
            "no tests": ["mymodule.x_helper__mutmut_2"],
            "timeout": ["mymodule.x_slow__mutmut_1"],
        }

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_error_passthrough(self, mock_cli):
        mock_cli.return_value = _CommandOutcome("Error: boom", failed=True)
        assert _names_by_status() == ({}, "Error: boom")

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_warning_annotated_results_are_still_grouped(self, mock_cli):
        # A successful `mutmut results` that also warned on stderr must still be parsed,
        # and the warning text must not land in a status the tools act on.
        mock_cli.return_value = _CommandOutcome(
            RESULTS_OUTPUT + "Warning: some_config is deprecated. Please rename it\n", failed=False
        )
        grouped, error = _names_by_status()
        assert error == ""
        assert grouped["survived"] == ["mymodule.x_core_logic__mutmut_1", "mymodule.x_logger_setup__mutmut_1"]
        assert grouped["no tests"] == ["mymodule.x_helper__mutmut_2"]

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_annotated_stderr_contributes_no_status(self, mock_cli):
        # A successful `mutmut results` whose stderr was appended must summarize exactly
        # as the clean results block does — no fabricated status, no fabricated count.
        mock_cli.return_value = _CommandOutcome(
            RESULTS_OUTPUT + "Warning: some_config is deprecated\n" + TRACEBACK_STDERR, failed=False
        )
        grouped, counts, error = _result_summary()
        assert error == ""
        assert grouped == {
            "survived": ["mymodule.x_core_logic__mutmut_1", "mymodule.x_logger_setup__mutmut_1"],
            "no tests": ["mymodule.x_helper__mutmut_2"],
        }
        assert counts == {"survived": 2, "no tests": 1}

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_survivor_names_stays_survived_only(self, mock_cli):
        mock_cli.return_value = _CommandOutcome(RESULTS_OUTPUT, failed=False)
        assert _survivor_names() == (
            ["mymodule.x_core_logic__mutmut_1", "mymodule.x_logger_setup__mutmut_1"],
            "",
        )


# ---------------------------------------------------------------------------
# run_mutmut  (mutmut 3.x: `mutmut run [MUTANT_NAMES]...`)
# ---------------------------------------------------------------------------


class TestRunMutmut:
    @patch("mutmut_mcp._run_command")
    def test_run_all(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome("7/7", failed=False)
        run_mutmut()
        assert mock_cmd.call_args[0][0] == ["mutmut", "run"]

    @patch("mutmut_mcp._run_command")
    def test_run_with_mutant_filter(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome("1/1", failed=False)
        run_mutmut("mymodule.x_add__mutmut_1")
        call_args = mock_cmd.call_args[0][0]
        assert call_args == ["mutmut", "run", "mymodule.x_add__mutmut_1"]

    @patch("mutmut_mcp._run_command")
    def test_run_with_options(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome("done", failed=False)
        run_mutmut(options="--max-children 4")
        call_args = mock_cmd.call_args[0][0]
        assert "--max-children" in call_args
        assert "4" in call_args
        # 2.x-only flags must never be emitted.
        assert "--use-coverage" not in call_args
        assert "--timeout" not in call_args

    @patch("mutmut_mcp.os.path.exists", return_value=False)
    def test_run_with_missing_venv(self, mock_exists):
        result = run_mutmut("mod", venv_path="/bad/venv")
        assert "Error" in result


class TestMutatingOperationLocks:
    @pytest.mark.parametrize(
        "operation",
        [
            lambda project: run_mutmut(project_path=project),
            lambda project: rerun_mutmut_on_survivor("mod.x__mutmut_1", project_path=project),
            lambda project: clean_mutmut_cache(project_path=project),
        ],
        ids=["run", "rerun", "clean"],
    )
    def test_busy_project_returns_without_starting_work(self, operation, tmp_path):
        project = str(tmp_path)
        # Real state to guard: `clean_mutmut_cache` never shells out, so the "no subprocess"
        # assertions below are vacuous for it — only surviving state proves it did nothing.
        state = tmp_path / "mutants"
        state.mkdir()
        (state / "meta.json").write_text("{}")
        acquired = threading.Event()
        release = threading.Event()

        def hold_project_lock():
            lock = _project_lock(_canonical_project_path(project))
            lock.acquire()
            acquired.set()
            release.wait()
            lock.release()

        holder = threading.Thread(target=hold_project_lock)
        holder.start()
        assert acquired.wait(timeout=1)
        try:
            with (
                patch("mutmut_mcp._run_mutmut_cli") as mock_cli,
                patch("mutmut_mcp._run_command") as mock_command,
            ):
                result = operation(project)
        finally:
            release.set()
            holder.join(timeout=1)

        assert result == (
            f"Error: a mutmut operation is already in progress for {project}. "
            "Wait for it to finish before starting another."
        )
        mock_cli.assert_not_called()
        mock_command.assert_not_called()
        assert (state / "meta.json").read_text() == "{}"

    def test_different_projects_do_not_block_each_other(self, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        lock = _project_lock(_canonical_project_path(str(first)))
        lock.acquire()
        try:
            with patch("mutmut_mcp._run_mutmut_cli", return_value=_CommandOutcome("done", failed=False)) as mock_cli:
                assert run_mutmut(project_path=str(second)) == "done"
        finally:
            lock.release()

        mock_cli.assert_called_once_with(["run"], None, str(second))

    @pytest.mark.parametrize("project_spelling", [None, ".", "absolute"])
    def test_equivalent_project_spellings_share_a_lock(self, project_spelling, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        absolute = str(tmp_path)
        lock = _project_lock(_canonical_project_path(None))
        lock.acquire()
        try:
            spelling = absolute if project_spelling == "absolute" else project_spelling
            with patch("mutmut_mcp._run_mutmut_cli") as mock_cli:
                result = run_mutmut(project_path=spelling)
        finally:
            lock.release()

        assert "already in progress" in result
        mock_cli.assert_not_called()

    @patch("mutmut_mcp._run_command")
    def test_read_tools_remain_available_while_write_lock_is_held(self, mock_command, tmp_path):
        mock_command.return_value = _CommandOutcome(RESULTS_OUTPUT, failed=False)
        lock = _project_lock(_canonical_project_path(str(tmp_path)))
        lock.acquire()
        try:
            assert show_results(project_path=str(tmp_path)) == RESULTS_OUTPUT
            assert "mymodule.x_core_logic__mutmut_1" in show_survivors(project_path=str(tmp_path))
            assert show_mutant("mod.x__mutmut_1", project_path=str(tmp_path)) == RESULTS_OUTPUT
            assert prioritize_survivors(project_path=str(tmp_path))["prioritized"]
        finally:
            lock.release()

        assert mock_command.call_count == 4

    # FastMCP trims the `Args:`/`Returns:` sections out of a tool's schema description, so the
    # busy behaviour only reaches the agent if it is documented in the leading prose.
    @pytest.mark.parametrize(
        ("tool_name", "documented"),
        [
            ("run_mutmut", True),
            ("rerun_mutmut_on_survivor", True),
            ("clean_mutmut_cache", True),
            ("show_results", False),
            ("show_survivors", False),
            ("show_mutant", False),
            ("prioritize_survivors", False),
        ],
    )
    def test_busy_error_is_documented_in_the_mcp_schema(self, tool_name, documented):
        description = asyncio.run(mutmut_mcp.mcp.get_tool(tool_name)).description
        assert ("a mutmut operation is already in progress" in description) is documented


# ---------------------------------------------------------------------------
# show_results / show_survivors
# ---------------------------------------------------------------------------


class TestShowResults:
    @patch("mutmut_mcp._run_command")
    def test_show_results_uses_results_command(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome("    mod.x__mutmut_1: survived\n", failed=False)
        result = show_results()
        assert mock_cmd.call_args[0][0] == ["mutmut", "results"]
        assert "survived" in result


class TestShowSurvivors:
    @patch("mutmut_mcp._run_command")
    def test_lists_survivors_and_uncovered_in_labelled_sections(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome(RESULTS_OUTPUT, failed=False)
        result = show_survivors()
        # Derived from `mutmut results`, never the removed `survivors` command.
        assert mock_cmd.call_args[0][0] == ["mutmut", "results"]
        assert result == (
            "mymodule.x_core_logic__mutmut_1\n"
            "mymodule.x_logger_setup__mutmut_1\n"
            "\n"
            "Not covered by any test (1):\n"
            "mymodule.x_helper__mutmut_2"
        )

    @patch("mutmut_mcp._run_command")
    def test_survivors_only_output_is_unchanged(self, mock_cmd):
        # Every mutant has a definite, covered result -> plain newline-separated names.
        mock_cmd.return_value = _CommandOutcome(
            "    mod.x_a__mutmut_1: survived\n    mod.x_b__mutmut_1: survived\n", failed=False
        )
        assert show_survivors() == "mod.x_a__mutmut_1\nmod.x_b__mutmut_1"

    @patch("mutmut_mcp._run_command")
    def test_uncovered_without_survivors_is_reported(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome(
            "    mod.x_a__mutmut_1: no tests\n    mod.x_b__mutmut_1: no tests\n", failed=False
        )
        result = show_survivors()
        assert result != "No surviving mutants found."
        assert "No surviving mutants found." not in result
        assert result == "Not covered by any test (2):\nmod.x_a__mutmut_1\nmod.x_b__mutmut_1"

    @patch("mutmut_mcp._run_command")
    def test_no_survivors(self, mock_cmd):
        # No survivors and nothing uncovered -> the bare message.
        mock_cmd.return_value = _CommandOutcome("", failed=False)
        assert show_survivors() == "No surviving mutants found."

    @patch("mutmut_mcp._run_command")
    def test_other_statuses_do_not_count_as_survivors(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome(
            "    mod.x_a__mutmut_1: timeout\n    mod.x_b__mutmut_1: suspicious\n", failed=False
        )
        assert show_survivors() == "No surviving mutants found."

    @patch("mutmut_mcp._run_command")
    def test_unchecked_without_survivors_reports_incomplete_run(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome(
            "    mod.x_a__mutmut_1: not checked\n    mod.x_b__mutmut_1: not checked\n", failed=False
        )
        result = show_survivors()
        assert result.startswith("No surviving mutants found, but")
        assert "2 mutants are not checked" in result
        assert "run mutmut again" in result

    @patch("mutmut_mcp._run_command")
    def test_survivors_with_unresolved_mutants_include_note(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome(
            "    mod.x_a__mutmut_1: survived\n"
            "    mod.x_b__mutmut_1: not checked\n"
            "    mod.x_c__mutmut_1: check was interrupted by user\n",
            failed=False,
        )
        result = show_survivors()
        assert result.startswith("mod.x_a__mutmut_1")
        assert "Incomplete results:" in result
        assert "1 mutant is not checked" in result
        assert "1 mutant check was interrupted by the user" in result

    @patch("mutmut_mcp._run_command")
    def test_error_passthrough(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome("Error: boom", failed=True)
        assert show_survivors() == "Error: boom"


# ---------------------------------------------------------------------------
# rerun_mutmut_on_survivor  (mutmut 3.x: `mutmut run <mutant_name>`)
# ---------------------------------------------------------------------------


class TestRerunMutmut:
    @patch("mutmut_mcp._run_command")
    def test_rerun_specific(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome("done", failed=False)
        rerun_mutmut_on_survivor(mutation_id="mymodule.x_add__mutmut_1")
        call_args = mock_cmd.call_args[0][0]
        assert call_args == ["mutmut", "run", "mymodule.x_add__mutmut_1"]
        # 2.x rerun flags must never be emitted.
        assert "--rerun" not in call_args
        assert "--rerun-all" not in call_args

    @patch("mutmut_mcp._run_command")
    def test_rerun_all_survivors(self, mock_cmd):
        # First call: `mutmut results` (to find survivors); second: `mutmut run <names>`.
        mock_cmd.side_effect = [_CommandOutcome(RESULTS_OUTPUT, failed=False), _CommandOutcome("done", failed=False)]
        rerun_mutmut_on_survivor()
        run_call = mock_cmd.call_args_list[-1][0][0]
        assert run_call[:2] == ["mutmut", "run"]
        assert "mymodule.x_core_logic__mutmut_1" in run_call
        assert "mymodule.x_logger_setup__mutmut_1" in run_call
        assert "--rerun-all" not in run_call

    @patch("mutmut_mcp._run_command")
    def test_rerun_all_no_survivors(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome("", failed=False)
        result = rerun_mutmut_on_survivor()
        assert "No surviving mutants" in result
        # Only the `results` probe ran; no `run` was issued.
        assert mock_cmd.call_count == 1


# ---------------------------------------------------------------------------
# clean_mutmut_cache  (mutmut 3.x stores state in a `mutants/` directory)
# ---------------------------------------------------------------------------


class TestCleanMutmutCache:
    def test_removes_mutants_dir(self, tmp_path):
        state_dir = tmp_path / "mutants"
        state_dir.mkdir()
        (state_dir / "meta.json").write_text("{}")
        with patch("mutmut_mcp.MUTMUT_STATE_DIR", str(state_dir)):
            result = clean_mutmut_cache()
        assert not state_dir.exists()
        assert "cleared" in result.lower()

    def test_removes_legacy_cache_file(self, tmp_path):
        legacy = tmp_path / ".mutmut-cache"
        legacy.write_text("cache")
        with (
            patch("mutmut_mcp.MUTMUT_STATE_DIR", str(tmp_path / "mutants")),
            patch("mutmut_mcp.MUTMUT_LEGACY_CACHE_PATH", str(legacy)),
        ):
            result = clean_mutmut_cache()
        assert not legacy.exists()
        assert "cleared" in result.lower()

    def test_no_state(self, tmp_path):
        with (
            patch("mutmut_mcp.MUTMUT_STATE_DIR", str(tmp_path / "mutants")),
            patch("mutmut_mcp.MUTMUT_LEGACY_CACHE_PATH", str(tmp_path / ".mutmut-cache")),
        ):
            result = clean_mutmut_cache()
        assert "no mutmut state" in result.lower()

    def test_cleans_only_the_requested_project(self, tmp_path):
        # Two independent projects, each with their own mutmut state.
        target, other = tmp_path / "target", tmp_path / "other"
        for project in (target, other):
            (project / "mutants").mkdir(parents=True)
            (project / "mutants" / "meta.json").write_text("{}")
            (project / ".mutmut-cache").write_text("cache")

        result = clean_mutmut_cache(project_path=str(target))

        assert "cleared" in result.lower()
        assert not (target / "mutants").exists()
        assert not (target / ".mutmut-cache").exists()
        # The unrelated project's state must be untouched.
        assert (other / "mutants" / "meta.json").read_text() == "{}"
        assert (other / ".mutmut-cache").read_text() == "cache"

    def test_missing_project_path_errors_and_deletes_nothing(self, tmp_path, monkeypatch):
        # State exists in the server's CWD; a bad project_path must not fall back to it.
        (tmp_path / "mutants").mkdir()
        (tmp_path / "mutants" / "meta.json").write_text("{}")
        monkeypatch.chdir(tmp_path)

        result = clean_mutmut_cache(project_path=str(tmp_path / "does-not-exist"))

        assert "Error" in result
        assert (tmp_path / "mutants" / "meta.json").exists()


# ---------------------------------------------------------------------------
# show_mutant
# ---------------------------------------------------------------------------


class TestShowMutant:
    @patch("mutmut_mcp._run_command")
    def test_show_mutant(self, mock_cmd):
        mock_cmd.return_value = _CommandOutcome("--- a/mod.py\n+++ b/mod.py\n-  x = 1\n+  x = 2", failed=False)
        result = show_mutant("mymodule.x_add__mutmut_1")
        assert mock_cmd.call_args[0][0] == ["mutmut", "show", "mymodule.x_add__mutmut_1"]
        assert "mod.py" in result

    def test_empty_mutation_id(self):
        result = show_mutant("")
        assert "Error" in result


# ---------------------------------------------------------------------------
# prioritize_survivors
# ---------------------------------------------------------------------------


class TestPrioritizeSurvivors:
    @patch("mutmut_mcp._run_mutmut_cli")
    def test_unchecked_status_counts_and_message(self, mock_cli):
        mock_cli.return_value = _CommandOutcome(
            "    mod.x_a__mutmut_1: not checked\n    mod.x_b__mutmut_1: not checked\n    mod.x_c__mutmut_1: timeout\n",
            failed=False,
        )
        result = prioritize_survivors()
        assert result["prioritized"] == []
        assert result["status_counts"] == {"not checked": 2, "timeout": 1}
        assert "2 mutants are not checked" in result["message"]

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_unresolved_note_does_not_change_prioritized_entries(self, mock_cli):
        mock_cli.return_value = _CommandOutcome(
            "    mod.x_a__mutmut_1: survived\n    mod.x_b__mutmut_1: not checked\n", failed=False
        )
        result = prioritize_survivors()
        assert [entry["mutant_id"] for entry in result["prioritized"]] == ["mod.x_a__mutmut_1"]
        assert result["status_counts"] == {"survived": 1, "not checked": 1}
        assert "Incomplete results: 1 mutant is not checked" in result["message"]

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_uncovered_mutants_are_reported_without_survivors(self, mock_cli):
        mock_cli.return_value = _CommandOutcome("    mod.x__mutmut_1: no tests\n", failed=False)
        result = prioritize_survivors()
        assert result["prioritized"] == [
            {
                "mutant_id": "mod.x__mutmut_1",
                "score": 2,
                "reason": "No test covers this mutant.",
                "raw": "mod.x__mutmut_1",
                "status": "no tests",
            }
        ]
        assert result["message"] != "No surviving mutants found."

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_prioritizes_correctly(self, mock_cli):
        mock_cli.return_value = _CommandOutcome(RESULTS_OUTPUT, failed=False)
        result = prioritize_survivors()
        # Two `survived` mutants plus the uncovered one.
        assert [(p["mutant_id"], p["score"], p["status"]) for p in result["prioritized"]] == [
            # Uncovered ranks above every survivor.
            ("mymodule.x_helper__mutmut_2", 2, "no tests"),
            # Core logic still ranks above the logger survivor (log/debug is deprioritized).
            ("mymodule.x_core_logic__mutmut_1", 1, "survived"),
            ("mymodule.x_logger_setup__mutmut_1", 0, "survived"),
        ]
        scores = [p["score"] for p in result["prioritized"]]
        assert scores == sorted(scores, reverse=True)
        assert result["status_counts"] == {"survived": 2, "no tests": 1}

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_annotated_stderr_does_not_fabricate_a_status_count(self, mock_cli):
        mock_cli.return_value = _CommandOutcome(
            RESULTS_OUTPUT + "Warning: some_config is deprecated\n" + TRACEBACK_STDERR, failed=False
        )
        result = prioritize_survivors()
        assert result["status_counts"] == {"survived": 2, "no tests": 1}
        assert [p["mutant_id"] for p in result["prioritized"]] == [
            "mymodule.x_helper__mutmut_2",
            "mymodule.x_core_logic__mutmut_1",
            "mymodule.x_logger_setup__mutmut_1",
        ]

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_survivors_only_entries_are_unchanged_apart_from_status(self, mock_cli):
        mock_cli.return_value = _CommandOutcome(
            "    mymodule.x_core_logic__mutmut_1: survived\n    mymodule.x_logger_setup__mutmut_1: survived\n",
            failed=False,
        )
        result = prioritize_survivors()
        assert result["prioritized"] == [
            {
                "mutant_id": "mymodule.x_core_logic__mutmut_1",
                "score": 1,
                "reason": "Potentially material logic, prioritize.",
                "raw": "mymodule.x_core_logic__mutmut_1",
                "status": "survived",
            },
            {
                "mutant_id": "mymodule.x_logger_setup__mutmut_1",
                "score": 0,
                "reason": "Likely log/debug only, deprioritized.",
                "raw": "mymodule.x_logger_setup__mutmut_1",
                "status": "survived",
            },
        ]
        assert result["message"] == "Survivors prioritized by likely materiality."
        assert result["status_counts"] == {"survived": 2}

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_empty_output(self, mock_cli):
        mock_cli.return_value = _CommandOutcome("", failed=False)
        result = prioritize_survivors()
        assert result["prioritized"] == []
        assert result["message"] == "No surviving mutants found."
        assert result["status_counts"] == {}

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_error_passthrough(self, mock_cli):
        mock_cli.return_value = _CommandOutcome("Error: boom", failed=True)
        result = prioritize_survivors()
        assert result["prioritized"] == []
        assert "boom" in result["message"]
        assert result["status_counts"] == {}


def test_status_counts_counts_every_parsed_status():
    assert _status_counts([("a", "survived"), ("b", "not checked"), ("c", "not checked")]) == {
        "survived": 1,
        "not checked": 2,
    }
