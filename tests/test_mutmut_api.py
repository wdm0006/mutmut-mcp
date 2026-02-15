import tempfile
from unittest.mock import MagicMock, patch

from mutmut_mcp import (
    _get_mutmut_path,
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

# ---------------------------------------------------------------------------
# _run_command
# ---------------------------------------------------------------------------


class TestRunCommand:
    @patch("mutmut_mcp.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        assert _run_command(["echo", "ok"]) == "ok\n"

    @patch("mutmut_mcp.subprocess.run")
    def test_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fail\n")
        result = _run_command(["false"])
        assert "Error" in result
        assert "fail" in result

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
        mock_cmd.assert_called_once_with(["mutmut", "results"])
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
# run_mutmut
# ---------------------------------------------------------------------------


class TestRunMutmut:
    @patch("mutmut_mcp._run_command")
    def test_basic_run(self, mock_cmd):
        mock_cmd.return_value = "5 mutants generated"
        run_mutmut("mymodule.py")
        call_args = mock_cmd.call_args[0][0]
        assert "mutmut" in call_args
        assert "run" in call_args
        assert "mymodule.py" in call_args

    @patch("mutmut_mcp._run_command")
    def test_run_with_options(self, mock_cmd):
        mock_cmd.return_value = "done"
        run_mutmut("pkg", options="--use-coverage --timeout=10")
        call_args = mock_cmd.call_args[0][0]
        assert "--use-coverage" in call_args
        assert "--timeout=10" in call_args

    @patch("mutmut_mcp.os.path.exists", return_value=False)
    def test_run_with_missing_venv(self, mock_exists):
        result = run_mutmut("mod.py", venv_path="/bad/venv")
        assert "Error" in result


# ---------------------------------------------------------------------------
# show_results / show_survivors
# ---------------------------------------------------------------------------


class TestShowResults:
    @patch("mutmut_mcp._run_command")
    def test_show_results(self, mock_cmd):
        mock_cmd.return_value = "Killed: 10  Survived: 2"
        result = show_results()
        assert "Killed" in result

    @patch("mutmut_mcp._run_command")
    def test_show_survivors(self, mock_cmd):
        mock_cmd.return_value = "SURVIVED: mod:42\nSURVIVED: mod:99\n"
        result = show_survivors()
        assert "SURVIVED" in result


# ---------------------------------------------------------------------------
# rerun_mutmut_on_survivor
# ---------------------------------------------------------------------------


class TestRerunMutmut:
    @patch("mutmut_mcp._run_command")
    def test_rerun_specific(self, mock_cmd):
        mock_cmd.return_value = "done"
        rerun_mutmut_on_survivor(mutation_id="42")
        call_args = mock_cmd.call_args[0][0]
        assert "--rerun" in call_args
        assert "42" in call_args

    @patch("mutmut_mcp._run_command")
    def test_rerun_all(self, mock_cmd):
        mock_cmd.return_value = "done"
        rerun_mutmut_on_survivor()
        call_args = mock_cmd.call_args[0][0]
        assert "--rerun-all" in call_args


# ---------------------------------------------------------------------------
# clean_mutmut_cache
# ---------------------------------------------------------------------------


class TestCleanMutmutCache:
    @patch("mutmut_mcp._run_mutmut_cli")
    def test_cli_clean_success(self, mock_cli):
        mock_cli.return_value = "Cache cleared"
        result = clean_mutmut_cache()
        assert "Cache cleared" in result

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_fallback_removes_file(self, mock_cli):
        mock_cli.return_value = "Error: unknown command"
        # Create a temp cache file to simulate .mutmut-cache
        with patch("mutmut_mcp.MUTMUT_CACHE_PATH", tempfile.mktemp()) as tmp:
            with open(tmp, "w") as f:
                f.write("cache")
            result = clean_mutmut_cache()
            assert "cleared" in result.lower() or "No mutmut cache" in result

    @patch("mutmut_mcp._run_mutmut_cli")
    def test_fallback_no_cache(self, mock_cli):
        mock_cli.return_value = "Error: unknown command"
        with patch("mutmut_mcp.MUTMUT_CACHE_PATH", "/tmp/nonexistent_mutmut_cache_xyz"):
            result = clean_mutmut_cache()
            assert "no mutmut cache" in result.lower()


# ---------------------------------------------------------------------------
# show_mutant
# ---------------------------------------------------------------------------


class TestShowMutant:
    @patch("mutmut_mcp._run_command")
    def test_show_mutant(self, mock_cmd):
        mock_cmd.return_value = "--- a/mod.py\n+++ b/mod.py\n-  x = 1\n+  x = 2"
        result = show_mutant("42")
        assert "mod.py" in result

    def test_empty_mutation_id(self):
        result = show_mutant("")
        assert "Error" in result


# ---------------------------------------------------------------------------
# prioritize_survivors
# ---------------------------------------------------------------------------


class TestPrioritizeSurvivors:
    @patch("mutmut_mcp.show_survivors")
    def test_no_survivors(self, mock_surv):
        mock_surv.return_value = "No surviving mutants found."
        result = prioritize_survivors()
        assert result["prioritized"] == []

    @patch("mutmut_mcp.show_survivors")
    def test_prioritizes_correctly(self, mock_surv):
        mock_surv.return_value = (
            "SURVIVED: mymodule.core_logic:42 (changed operator)\n"
            "SURVIVED: mymodule.logger_setup:10 (changed logging call)\n"
        )
        result = prioritize_survivors()
        assert len(result["prioritized"]) == 2
        # Core logic should score higher than logging
        scores = [p["score"] for p in result["prioritized"]]
        assert scores[0] >= scores[1]

    @patch("mutmut_mcp.show_survivors")
    def test_empty_output(self, mock_surv):
        mock_surv.return_value = ""
        result = prioritize_survivors()
        assert result["prioritized"] == []
