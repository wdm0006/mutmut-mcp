"""Cross-process advisory lock on `<project>/.mutmut-mcp.lock` (real subprocess holder)."""

import errno
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

import mutmut_mcp
from mutmut_mcp import (
    LOCK_FILE_NAME,
    _CommandOutcome,
    _project_lock,
    clean_mutmut_cache,
    rerun_mutmut_on_survivor,
    run_mutmut,
    start_mutmut_run,
)

HOLDER = (
    "import fcntl, sys\n"
    "h = open(sys.argv[1], 'a+')\n"
    "fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "print('held', flush=True)\n"
    "sys.stdin.read()\n"
)

posix_only = pytest.mark.skipif(os.name == "nt", reason="holder script uses fcntl")


@pytest.fixture
def other_process(tmp_path):
    """Start a separate Python process that holds the project's file lock until closed."""
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(tmp_path / LOCK_FILE_NAME)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    stdin, stdout = proc.stdin, proc.stdout
    assert stdin is not None and stdout is not None
    assert stdout.readline().strip() == "held"
    yield proc
    stdin.close()
    proc.wait(timeout=5)
    stdout.close()


@posix_only
@pytest.mark.parametrize(
    "operation",
    [
        lambda project: run_mutmut(project_path=project),
        lambda project: rerun_mutmut_on_survivor("mod.x__mutmut_1", project_path=project),
        lambda project: clean_mutmut_cache(project_path=project),
        lambda project: start_mutmut_run(project_path=project)["error"],
    ],
    ids=["run", "rerun", "clean", "start"],
)
def test_other_process_holding_lock_returns_busy_without_work(operation, other_process, tmp_path):
    state = tmp_path / "mutants"
    state.mkdir()
    (state / "meta.json").write_text("{}")
    with patch("mutmut_mcp._run_mutmut_cli") as cli, patch("mutmut_mcp._run_command") as command:
        result = operation(str(tmp_path))
    assert result == (
        f"Error: a mutmut operation is already in progress for {tmp_path.resolve()} "
        "(in this server or another process using the same project). "
        "Wait for it to finish before starting another."
    )
    cli.assert_not_called()
    command.assert_not_called()
    assert (state / "meta.json").read_text() == "{}"
    assert _project_lock(str(tmp_path.resolve())).acquire(blocking=False)
    _project_lock(str(tmp_path.resolve())).release()


@posix_only
def test_lock_file_lives_outside_state_dir_and_survives_clean(tmp_path):
    (tmp_path / "mutants").mkdir()
    assert clean_mutmut_cache(project_path=str(tmp_path)).startswith("Mutmut state cleared")
    assert (tmp_path / LOCK_FILE_NAME).exists()
    assert LOCK_FILE_NAME == ".mutmut-mcp.lock"


@posix_only
def test_lock_released_after_failing_run(tmp_path):
    with patch("mutmut_mcp._run_mutmut_cli", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            run_mutmut(project_path=str(tmp_path))
    release = mutmut_mcp._acquire_project(str(tmp_path.resolve()))
    assert release is not None
    release()


@posix_only
def test_lock_released_after_run_returns(tmp_path):
    with patch("mutmut_mcp._run_mutmut_cli", return_value=_CommandOutcome("ok", failed=False)):
        assert run_mutmut(project_path=str(tmp_path)) == "ok"
    probe = subprocess.run(
        [sys.executable, "-c", HOLDER.replace("sys.stdin.read()", ""), str(tmp_path / LOCK_FILE_NAME)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.stdout.strip() == "held"


@posix_only
def test_thread_lock_not_leaked_when_file_lock_is_busy(other_process, tmp_path):
    assert mutmut_mcp._acquire_project(str(tmp_path.resolve())) is None
    lock = _project_lock(str(tmp_path.resolve()))
    assert lock.acquire(blocking=False)
    lock.release()


def test_unopenable_lock_file_falls_back_to_thread_lock(tmp_path):
    with patch("builtins.open", side_effect=PermissionError("read-only")):
        release = mutmut_mcp._acquire_project(str(tmp_path.resolve()))
        assert release is not None
        assert mutmut_mcp._acquire_project(str(tmp_path.resolve())) is None
    release()
    again = mutmut_mcp._acquire_project(str(tmp_path.resolve()))
    assert again is not None
    again()


@posix_only
def test_platform_lock_error_falls_back_to_thread_lock(tmp_path):
    with patch("mutmut_mcp.fcntl.flock", side_effect=OSError(errno.ENOLCK, "no locks")):
        release = mutmut_mcp._acquire_project(str(tmp_path.resolve()))
    assert release is not None
    release()


@posix_only
def test_second_thread_lock_acquirer_in_process_is_busy_with_file_lock_held(tmp_path):
    first = mutmut_mcp._acquire_project(str(tmp_path.resolve()))
    assert first is not None
    assert mutmut_mcp._acquire_project(str(tmp_path.resolve())) is None
    first()


@posix_only
def test_try_file_lock_outcomes(other_process, tmp_path):
    with open(tmp_path / LOCK_FILE_NAME, "a+") as contended:
        assert mutmut_mcp._try_file_lock(contended) is False
    with open(tmp_path / "free.lock", "a+") as free:
        assert mutmut_mcp._try_file_lock(free) is True
        with open(tmp_path / "free.lock", "a+") as again:
            assert mutmut_mcp._try_file_lock(again) is False
    with open(tmp_path / "x.lock", "a+") as unsupported:
        with patch("mutmut_mcp.fcntl.flock", side_effect=OSError(errno.ENOLCK, "no locks")):
            assert mutmut_mcp._try_file_lock(unsupported) is True
