"""Background `start_mutmut_run` / `mutmut_run_status` behaviour."""

import asyncio
import threading
from unittest.mock import patch

import pytest

import mutmut_mcp
from mutmut_mcp import _CommandOutcome, mutmut_run_status, run_mutmut, start_mutmut_run

FRAMES = "⠙ 2/3  \U0001f389 2 \U0001faa5 0\n⠹ 3/3  \U0001f389 3 \U0001faa5 0\ndone\n"


@pytest.fixture(autouse=True)
def _clean_registry():
    with mutmut_mcp._jobs_lock:
        mutmut_mcp._jobs.clear()
    yield
    with mutmut_mcp._jobs_lock:
        mutmut_mcp._jobs.clear()


def _wait_done(run_id, timeout=5):
    deadline = threading.Event()
    for _ in range(int(timeout * 100)):
        status = mutmut_run_status(run_id)
        if status["state"] != "running":
            return status
        deadline.wait(0.01)
    raise AssertionError("job did not finish")


class _Gate:
    """A fake `_run_mutmut_cli` that blocks until released."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def __call__(self, args, venv_path, project_path):
        self.calls.append((args, venv_path, project_path))
        self.started.set()
        assert self.release.wait(5)
        return self.outcome


def test_returns_before_subprocess_finishes_and_blocks_other_writes(tmp_path):
    gate = _Gate(_CommandOutcome(FRAMES, failed=False))
    with patch("mutmut_mcp._run_mutmut_cli", gate):
        started = start_mutmut_run(
            target="a b", options="--max-children 1", venv_path="/some/venv", project_path=str(tmp_path)
        )
        assert started["state"] == "running"
        assert len(started["run_id"]) == 12
        assert gate.started.wait(5)

        running = mutmut_run_status(started["run_id"])
        assert running["state"] == "running"
        assert running["exit_failed"] is None
        assert running["output_tail"] == ""
        assert running["progress"] is None

        busy = run_mutmut(project_path=str(tmp_path))
        assert busy.startswith("Error: a mutmut operation is already in progress for ")
        second = start_mutmut_run(project_path=str(tmp_path))
        assert second["run_id"] is None
        assert second["state"] == "failed"
        assert "already in progress" in second["error"]
        assert len(mutmut_mcp._jobs) == 1

        gate.release.set()
        done = _wait_done(started["run_id"])
    assert gate.calls == [(["run", "a", "b", "--max-children", "1"], "/some/venv", str(tmp_path.resolve()))]
    assert done["state"] == "finished"
    assert done["exit_failed"] is False
    assert done["output_tail"] == FRAMES
    assert done["progress"] == "3/3"
    assert 0 <= done["elapsed_s"] < 60
    # lock released: another write now proceeds
    with patch("mutmut_mcp._run_mutmut_cli", return_value=_CommandOutcome("ok", failed=False)):
        assert run_mutmut(project_path=str(tmp_path)) == "ok"


def test_failed_run_reports_failed_and_releases_lock(tmp_path):
    outcome = _CommandOutcome("boom\nError: bad", failed=True)
    with patch("mutmut_mcp._run_mutmut_cli", return_value=outcome):
        run_id = start_mutmut_run(project_path=str(tmp_path))["run_id"]
        done = _wait_done(run_id)
    assert done["state"] == "failed"
    assert done["exit_failed"] is True
    assert done["output_tail"] == "boom\nError: bad"
    assert done["progress"] is None
    assert mutmut_mcp._project_lock(str(tmp_path.resolve())).acquire(blocking=False)


def test_worker_exception_marks_failed_and_releases_lock(tmp_path):
    with patch("mutmut_mcp._run_mutmut_cli", side_effect=RuntimeError("kaput")):
        run_id = start_mutmut_run(project_path=str(tmp_path))["run_id"]
        done = _wait_done(run_id)
    assert done["state"] == "failed"
    assert done["output_tail"] == "Exception occurred: kaput"
    assert mutmut_mcp._project_lock(str(tmp_path.resolve())).acquire(blocking=False)


def test_launch_exception_releases_lock_and_leaves_no_job(tmp_path):
    with patch("mutmut_mcp.threading.Thread", side_effect=RuntimeError("no threads")):
        result = start_mutmut_run(project_path=str(tmp_path))
    assert result == {"run_id": None, "state": "failed", "error": "Error: could not start background run: no threads"}
    assert mutmut_mcp._jobs == {}
    assert mutmut_mcp._project_lock(str(tmp_path.resolve())).acquire(blocking=False)


def test_invalid_project_path_starts_nothing(tmp_path):
    missing = str(tmp_path / "nope")
    result = start_mutmut_run(project_path=missing)
    assert result == {
        "run_id": None,
        "state": "failed",
        "error": f"Error: project_path {missing} is not an existing directory.",
    }
    assert mutmut_mcp._jobs == {}


def test_unknown_run_id_is_a_clear_error():
    assert mutmut_run_status("nope") == {"state": "unknown", "error": "Error: unknown run_id 'nope'."}


def test_output_tail_is_truncated_to_the_end():
    text = "x" * (mutmut_mcp.OUTPUT_TAIL_CHARS + 10) + "END"
    with patch("mutmut_mcp._run_mutmut_cli", return_value=_CommandOutcome(text, failed=False)):
        run_id = start_mutmut_run()["run_id"]
        done = _wait_done(run_id)
    assert done["output_tail"] == text[-mutmut_mcp.OUTPUT_TAIL_CHARS :]
    assert done["output_tail"].endswith("END")


def test_registry_keeps_only_newest_finished_jobs(tmp_path):
    ids = []
    with patch("mutmut_mcp._run_mutmut_cli", return_value=_CommandOutcome("ok", failed=False)):
        for _ in range(mutmut_mcp.MAX_JOBS + 3):
            run_id = start_mutmut_run(project_path=str(tmp_path))["run_id"]
            _wait_done(run_id)
            # `_wait_done` can observe the outcome before the worker prunes; settle on the count
            ids.append(run_id)
    with mutmut_mcp._jobs_lock:
        kept = list(mutmut_mcp._jobs)
    assert kept == ids[-mutmut_mcp.MAX_JOBS :]


def test_running_jobs_are_never_evicted(tmp_path):
    gate = _Gate(_CommandOutcome("ok", failed=False))
    other = tmp_path / "other"
    other.mkdir()
    with mutmut_mcp._jobs_lock:
        for i in range(mutmut_mcp.MAX_JOBS):
            job = mutmut_mcp._Job()
            job.done = (_CommandOutcome("old", failed=False), 0.0)
            mutmut_mcp._jobs[f"old{i}"] = job
    with patch("mutmut_mcp._run_mutmut_cli", gate):
        running_id = start_mutmut_run(project_path=str(tmp_path))["run_id"]
        gate.started.wait(5)
        second = start_mutmut_run(project_path=str(other))["run_id"]
        with mutmut_mcp._jobs_lock:
            assert running_id in mutmut_mcp._jobs and second in mutmut_mcp._jobs
        gate.release.set()
        _wait_done(running_id)
        _wait_done(second)


def test_tool_schemas_list_eleven_tools_with_documented_signatures():
    async def names():
        return await mutmut_mcp.mcp.list_tools()

    tools = asyncio.run(names())
    by_name = {t.name: t for t in tools}
    assert len(by_name) == 11
    assert {"start_mutmut_run", "mutmut_run_status"} <= set(by_name)
    assert set(by_name["start_mutmut_run"].parameters["properties"]) == {
        "target",
        "options",
        "venv_path",
        "project_path",
    }
    assert by_name["mutmut_run_status"].parameters["required"] == ["run_id"]
    assert "write lock" in by_name["start_mutmut_run"].description


SLEEPER = ["python3", "-c", "import time; print('started', flush=True); time.sleep(60)"]


def _start_sleeper(tmp_path):
    """Start a real job whose subprocess is a long sleep, through the real `_run_command` path."""
    ready = threading.Event()
    real = mutmut_mcp._run_tracked

    def tracked(job, command, cwd):
        ready.set()
        return real(job, SLEEPER, cwd)

    patcher = patch("mutmut_mcp._run_tracked", tracked)
    patcher.start()
    run_id = start_mutmut_run(project_path=str(tmp_path))["run_id"]
    assert ready.wait(5)
    for _ in range(500):
        if mutmut_mcp._jobs[run_id].proc is not None:
            break
        threading.Event().wait(0.01)
    return run_id, patcher


def test_cancel_stops_subprocess_frees_lock_and_reports_cancelled(tmp_path):
    with patch("mutmut_mcp._run_mutmut_cli", side_effect=lambda a, v, p: mutmut_mcp._run_command(["x"], cwd=p)):
        run_id, patcher = _start_sleeper(tmp_path)
        try:
            proc = mutmut_mcp._jobs[run_id].proc
            assert proc is not None and proc.poll() is None
            assert run_mutmut(project_path=str(tmp_path)).startswith("Error: a mutmut operation is already")

            result = mutmut_mcp.cancel_mutmut_run(run_id)
        finally:
            patcher.stop()
    assert result["state"] == "cancelled"
    assert "may be partial" in result["message"]
    assert proc.poll() is not None
    status = mutmut_run_status(run_id)
    assert status["state"] == "cancelled"
    assert status["exit_failed"] is True
    assert "may be partial" in status["note"]
    assert status["elapsed_s"] < 30
    with patch("mutmut_mcp._run_mutmut_cli", return_value=_CommandOutcome("ok", failed=False)):
        assert run_mutmut(project_path=str(tmp_path)) == "ok"


def test_cancel_escalates_to_sigkill_when_sigterm_is_ignored(tmp_path):
    ignorer = [
        "python3",
        "-c",
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('r', flush=True); time.sleep(60)",
    ]
    real = mutmut_mcp._run_tracked
    with (
        patch("mutmut_mcp._run_tracked", lambda job, command, cwd: real(job, ignorer, cwd)),
        patch("mutmut_mcp._run_mutmut_cli", side_effect=lambda a, v, p: mutmut_mcp._run_command(["x"], cwd=p)),
        patch("mutmut_mcp.CANCEL_GRACE_S", 0.5),
    ):
        run_id = start_mutmut_run(project_path=str(tmp_path))["run_id"]
        for _ in range(500):
            proc = mutmut_mcp._jobs[run_id].proc
            if proc is not None:
                break
            threading.Event().wait(0.01)
        threading.Event().wait(0.5)
        result = mutmut_mcp.cancel_mutmut_run(run_id)
    assert result["state"] == "cancelled"
    assert proc is not None and proc.returncode == -9


def test_cancel_finished_job_is_a_noop_with_message(tmp_path):
    with patch("mutmut_mcp._run_mutmut_cli", return_value=_CommandOutcome(FRAMES, failed=False)):
        run_id = start_mutmut_run(project_path=str(tmp_path))["run_id"]
        _wait_done(run_id)
    before = mutmut_run_status(run_id)
    result = mutmut_mcp.cancel_mutmut_run(run_id)
    assert result == {"state": "finished", "message": f"Run {run_id} already ended (finished); nothing to cancel."}
    assert mutmut_run_status(run_id) == before


def test_cancel_unknown_run_id():
    assert mutmut_mcp.cancel_mutmut_run("nope") == {
        "state": "unknown",
        "message": "Error: unknown run_id 'nope'.",
    }


def test_cancel_before_subprocess_spawns_still_stops_it(tmp_path):
    job = mutmut_mcp._Job()
    job.cancelled = True
    proc = None
    try:
        real_popen = mutmut_mcp.subprocess.Popen
        with patch("mutmut_mcp.subprocess.Popen", lambda *a, **k: real_popen(SLEEPER, **k)):
            result = mutmut_mcp._run_tracked(job, ["x"], str(tmp_path))
        proc = job.proc
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
    assert result.returncode == -15


def test_cancel_tool_is_registered_with_run_id_only():
    tool = asyncio.run(mutmut_mcp.mcp.get_tool("cancel_mutmut_run"))
    assert tool is not None
    assert tool.parameters["required"] == ["run_id"]
    assert tool.description is not None
    assert "write lock" in tool.description
    assert "partial" in tool.description


def test_cancel_reports_still_running_when_process_will_not_die(tmp_path):
    gate = _Gate(_CommandOutcome(FRAMES, failed=False))
    with patch("mutmut_mcp._run_mutmut_cli", gate), patch("mutmut_mcp.CANCEL_GRACE_S", 0.05):
        run_id = start_mutmut_run(project_path=str(tmp_path))["run_id"]
        assert gate.started.wait(5)
        result = mutmut_mcp.cancel_mutmut_run(run_id)
        gate.release.set()
    assert result == {"state": "running", "message": f"Run {run_id} did not stop yet; call cancel_mutmut_run again."}


def test_run_tracked_returns_what_subprocess_run_would(tmp_path):
    import sys

    job = mutmut_mcp._Job()
    code = "import os,sys; print(os.getcwd()); print('oops', file=sys.stderr); sys.exit(3)"
    command = [sys.executable, "-c", code]
    result = mutmut_mcp._run_tracked(job, command, str(tmp_path))
    assert result.args == command
    assert result.returncode == 3
    assert result.stdout.decode().strip() == str(tmp_path.resolve())
    assert result.stderr.decode().strip() == "oops"
    assert job.proc is not None and job.proc.returncode == 3
    assert job.cancelled is False


def test_run_command_uses_tracked_path_only_inside_a_job_worker(tmp_path):
    import sys

    command = [sys.executable, "-c", "import os; print(os.getcwd())"]
    job = mutmut_mcp._Job()
    mutmut_mcp._worker.job = job
    try:
        outcome = mutmut_mcp._run_command(command, cwd=str(tmp_path))
    finally:
        del mutmut_mcp._worker.job
    assert outcome == _CommandOutcome(str(tmp_path.resolve()) + "\n", failed=False)
    assert job.proc is not None
    assert mutmut_mcp._run_command(command, cwd=str(tmp_path)).output == str(tmp_path.resolve()) + "\n"
