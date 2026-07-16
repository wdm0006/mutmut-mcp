import os
from unittest.mock import MagicMock, patch

from mutmut_mcp import (
    _get_mutmut_path,
    _parse_results,
    _run_command,
    _run_mutmut_cli,
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

# ---------------------------------------------------------------------------
# _run_command
# ---------------------------------------------------------------------------


class TestRunCommand:
    @patch("mutmut_mcp.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        assert _run_command(["echo", "ok"]) == "ok\n"

    @patch("mutmut_mcp.subprocess.run")
    def test_nonzero_exit_with_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="survived: 2\n", stderr="")
        assert _run_command(["mutmut", "run"]) == "survived: 2\n"

    @patch("mutmut_mcp.subprocess.run")
    def test_nonzero_exit_with_stderr_only(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fail\n")
        assert _run_command(["false"]) == "Error: fail\n"

    @patch("mutmut_mcp.subprocess.run", side_effect=FileNotFoundError("not found"))
    def test_exception(self, mock_run):
        result = _run_command(["nonexistent_binary"])
        assert "Exception" in result


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
        mock_cmd.return_value = "results"
        result = _run_mutmut_cli(["results"])
        mock_cmd.assert_called_once_with(["mutmut", "results"], cwd=None)
        assert result == "results"

    @patch("mutmut_mcp.os.path.exists", return_value=True)
    @patch("mutmut_mcp._run_command")
    def test_with_venv(self, mock_cmd, mock_exists):
        mock_cmd.return_value = "results"
        _run_mutmut_cli(["results"], venv_path="/my/venv")
        # Should use venv mutmut binary
        call_args = mock_cmd.call_args[0][0]
        assert "mutmut" in call_args[0]
        assert "results" in call_args

    @patch("mutmut_mcp.os.path.exists", return_value=False)
    def test_with_missing_venv(self, mock_exists):
        result = _run_mutmut_cli(["results"], venv_path="/missing/venv")
        assert "Error" in result
        assert "not found" in result


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
        mock_cmd.return_value = "results"
        _run_mutmut_cli(["results"], project_path=str(tmp_path))
        assert mock_cmd.call_args.kwargs["cwd"] == str(tmp_path)

    @patch("mutmut_mcp._run_command")
    def test_relative_venv_resolved_against_project_path(self, mock_cmd, tmp_path):
        bin_dir = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        bin_dir.mkdir(parents=True)
        binary = bin_dir / ("mutmut.exe" if os.name == "nt" else "mutmut")
        binary.write_text("")
        mock_cmd.return_value = "results"

        _run_mutmut_cli(["results"], venv_path=".venv", project_path=str(tmp_path))

        assert mock_cmd.call_args[0][0][0] == str(binary)
        assert mock_cmd.call_args.kwargs["cwd"] == str(tmp_path)

    @patch("mutmut_mcp._run_command")
    @patch("mutmut_mcp.os.path.exists", return_value=True)
    def test_absolute_venv_not_rebased_on_project_path(self, mock_exists, mock_cmd, tmp_path):
        mock_cmd.return_value = "results"
        _run_mutmut_cli(["results"], venv_path="/abs/venv", project_path=str(tmp_path))
        assert mock_cmd.call_args[0][0][0].startswith("/abs/venv")

    @patch("mutmut_mcp._run_command")
    def test_missing_project_path_errors_without_running(self, mock_cmd, tmp_path):
        result = _run_mutmut_cli(["results"], project_path=str(tmp_path / "nope"))
        assert "Error" in result
        assert "not an existing directory" in result
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
        mock_cmd.return_value = RESULTS_OUTPUT
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


# ---------------------------------------------------------------------------
# run_mutmut  (mutmut 3.x: `mutmut run [MUTANT_NAMES]...`)
# ---------------------------------------------------------------------------


class TestRunMutmut:
    @patch("mutmut_mcp._run_command")
    def test_run_all(self, mock_cmd):
        mock_cmd.return_value = "7/7"
        run_mutmut()
        assert mock_cmd.call_args[0][0] == ["mutmut", "run"]

    @patch("mutmut_mcp._run_command")
    def test_run_with_mutant_filter(self, mock_cmd):
        mock_cmd.return_value = "1/1"
        run_mutmut("mymodule.x_add__mutmut_1")
        call_args = mock_cmd.call_args[0][0]
        assert call_args == ["mutmut", "run", "mymodule.x_add__mutmut_1"]

    @patch("mutmut_mcp._run_command")
    def test_run_with_options(self, mock_cmd):
        mock_cmd.return_value = "done"
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


# ---------------------------------------------------------------------------
# show_results / show_survivors
# ---------------------------------------------------------------------------


class TestShowResults:
    @patch("mutmut_mcp._run_command")
    def test_show_results_uses_results_command(self, mock_cmd):
        mock_cmd.return_value = "    mod.x__mutmut_1: survived\n"
        result = show_results()
        assert mock_cmd.call_args[0][0] == ["mutmut", "results"]
        assert "survived" in result


class TestShowSurvivors:
    @patch("mutmut_mcp._run_command")
    def test_lists_only_survived(self, mock_cmd):
        mock_cmd.return_value = RESULTS_OUTPUT
        result = show_survivors()
        # Derived from `mutmut results`, never the removed `survivors` command.
        assert mock_cmd.call_args[0][0] == ["mutmut", "results"]
        assert "mymodule.x_core_logic__mutmut_1" in result
        assert "mymodule.x_logger_setup__mutmut_1" in result
        # "no tests" mutants are not survivors.
        assert "x_helper__mutmut_2" not in result

    @patch("mutmut_mcp._run_command")
    def test_no_survivors(self, mock_cmd):
        mock_cmd.return_value = "    mod.x__mutmut_1: no tests\n"
        assert "No surviving mutants" in show_survivors()

    @patch("mutmut_mcp._run_command")
    def test_error_passthrough(self, mock_cmd):
        mock_cmd.return_value = "Error: boom"
        assert show_survivors() == "Error: boom"


# ---------------------------------------------------------------------------
# rerun_mutmut_on_survivor  (mutmut 3.x: `mutmut run <mutant_name>`)
# ---------------------------------------------------------------------------


class TestRerunMutmut:
    @patch("mutmut_mcp._run_command")
    def test_rerun_specific(self, mock_cmd):
        mock_cmd.return_value = "done"
        rerun_mutmut_on_survivor(mutation_id="mymodule.x_add__mutmut_1")
        call_args = mock_cmd.call_args[0][0]
        assert call_args == ["mutmut", "run", "mymodule.x_add__mutmut_1"]
        # 2.x rerun flags must never be emitted.
        assert "--rerun" not in call_args
        assert "--rerun-all" not in call_args

    @patch("mutmut_mcp._run_command")
    def test_rerun_all_survivors(self, mock_cmd):
        # First call: `mutmut results` (to find survivors); second: `mutmut run <names>`.
        mock_cmd.side_effect = [RESULTS_OUTPUT, "done"]
        rerun_mutmut_on_survivor()
        run_call = mock_cmd.call_args_list[-1][0][0]
        assert run_call[:2] == ["mutmut", "run"]
        assert "mymodule.x_core_logic__mutmut_1" in run_call
        assert "mymodule.x_logger_setup__mutmut_1" in run_call
        assert "--rerun-all" not in run_call

    @patch("mutmut_mcp._run_command")
    def test_rerun_all_no_survivors(self, mock_cmd):
        mock_cmd.return_value = ""
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
        mock_cmd.return_value = "--- a/mod.py\n+++ b/mod.py\n-  x = 1\n+  x = 2"
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
    @patch("mutmut_mcp.show_results")
    def test_no_survivors(self, mock_results):
        mock_results.return_value = "    mod.x__mutmut_1: no tests\n"
        result = prioritize_survivors()
        assert result["prioritized"] == []

    @patch("mutmut_mcp.show_results")
    def test_prioritizes_correctly(self, mock_results):
        mock_results.return_value = RESULTS_OUTPUT
        result = prioritize_survivors()
        # Only the two `survived` mutants are considered.
        assert len(result["prioritized"]) == 2
        # Core logic ranks above the logger survivor (log/debug is deprioritized).
        top = result["prioritized"][0]
        assert top["mutant_id"] == "mymodule.x_core_logic__mutmut_1"
        assert top["score"] == 1
        scores = [p["score"] for p in result["prioritized"]]
        assert scores[0] >= scores[1]

    @patch("mutmut_mcp.show_results")
    def test_empty_output(self, mock_results):
        mock_results.return_value = ""
        result = prioritize_survivors()
        assert result["prioritized"] == []

    @patch("mutmut_mcp.show_results")
    def test_error_passthrough(self, mock_results):
        mock_results.return_value = "Error: boom"
        result = prioritize_survivors()
        assert result["prioritized"] == []
        assert "boom" in result["message"]
