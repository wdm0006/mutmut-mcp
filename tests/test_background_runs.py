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


def test_tool_schemas_list_ten_tools_with_documented_signatures():
    async def names():
        return await mutmut_mcp.mcp.list_tools()

    tools = asyncio.run(names())
    by_name = {t.name: t for t in tools}
    assert len(by_name) == 10
    assert {"start_mutmut_run", "mutmut_run_status"} <= set(by_name)
    assert set(by_name["start_mutmut_run"].parameters["properties"]) == {
        "target",
        "options",
        "venv_path",
        "project_path",
    }
    assert by_name["mutmut_run_status"].parameters["required"] == ["run_id"]
    assert "write lock" in by_name["start_mutmut_run"].description
