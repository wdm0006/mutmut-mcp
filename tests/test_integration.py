"""End-to-end checks against the real mutmut 3.x CLI.

The unit tests mock the subprocess layer, so they lock in argv without proving the
commands exist. These tests actually invoke the installed `mutmut` binary to confirm
the tools speak a CLI that mutmut 3.4.0 accepts. They are skipped when `mutmut` is not
on PATH.
"""

import os
import shutil

import pytest

from mutmut_mcp import (
    _parse_results,
    clean_mutmut_cache,
    prioritize_survivors,
    run_mutmut,
    show_results,
    show_survivors,
)

pytestmark = pytest.mark.skipif(shutil.which("mutmut") is None, reason="mutmut CLI not installed")


@pytest.fixture()
def mutmut_project(tmp_path, monkeypatch):
    """A minimal, config-driven mutmut project in a temp working directory."""
    (tmp_path / "foo.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_foo.py").write_text("from foo import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    (tmp_path / "setup.cfg").write_text("[mutmut]\npaths_to_mutate=foo.py\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_show_results_runs_against_real_cli(mutmut_project):
    # `mutmut results` is a valid 3.x command; with no prior run it returns cleanly (empty),
    # proving the tool invokes a real subcommand rather than the removed 2.x ones.
    result = show_results()
    assert not result.startswith("Error")
    assert not result.startswith("Exception")


def test_show_survivors_runs_against_real_cli(mutmut_project):
    result = show_survivors()
    # No run performed yet -> no survivors, but the command must succeed end-to-end.
    assert result == "No surviving mutants found."


def test_clean_removes_real_state_dir(mutmut_project):
    os.makedirs("mutants", exist_ok=True)
    with open(os.path.join("mutants", "meta.json"), "w") as f:
        f.write("{}")
    result = clean_mutmut_cache()
    assert not os.path.isdir("mutants")
    assert "cleared" in result.lower()


@pytest.fixture()
def partly_tested_project(tmp_path, monkeypatch):
    """A project mixing a well-tested function, a weakly-tested one, and an untested one.

    `add` is killed, `scale` survives (the assertion holds for every mutant of `x * 2`),
    and nothing imports `untested_discount` at all, so its mutants come back as `no tests`.
    """
    (tmp_path / "foo.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n\n\n"
        "def scale(x):\n"
        "    return x * 2\n\n\n"
        "def untested_discount(price):\n"
        "    return price * 0.9\n"
    )
    (tmp_path / "test_foo.py").write_text(
        "from foo import add, scale\n\n\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n\n\n"
        "def test_scale():\n"
        "    assert scale(0) == 0\n"
    )
    (tmp_path / "setup.cfg").write_text("[mutmut]\npaths_to_mutate=foo.py\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_uncovered_mutants_reported_after_real_run(partly_tested_project):
    """A real `mutmut run`: mutants of a function no test touches must not be dropped.

    Only the `no tests` mutants are asserted on. Statuses that require forking a test
    worker (`survived`/`killed`) are intermittently reported as `segfault` on some
    platforms even at --max-children 1, whereas an uncovered mutant never forks one and
    is stable. The exact ranking of survivors is pinned by the unit tests instead.
    """
    run_mutmut(options="--max-children 1")

    results = show_results()
    uncovered = [name for name, status in _parse_results(results) if status == "no tests"]
    assert sorted(uncovered) == ["foo.x_untested_discount__mutmut_1", "foo.x_untested_discount__mutmut_2"], results

    survivors_output = show_survivors()
    assert survivors_output != "No surviving mutants found."
    assert f"Not covered by any test ({len(uncovered)}):" in survivors_output
    for name in uncovered:
        assert name in survivors_output

    prioritized = prioritize_survivors()["prioritized"]
    uncovered_entries = [p for p in prioritized if p["status"] == "no tests"]
    assert sorted(p["mutant_id"] for p in uncovered_entries) == sorted(uncovered)
    assert all(p["score"] == 2 for p in uncovered_entries)
    # Uncovered mutants occupy the top of the ranking, ahead of any survivor mutmut found.
    assert prioritized[: len(uncovered_entries)] == uncovered_entries


def test_show_results_against_explicit_project_path(mutmut_project, tmp_path_factory, monkeypatch):
    # Run from a directory that is NOT the project, proving project_path (not the CWD)
    # is what mutmut is pointed at.
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)
    result = show_results(project_path=str(mutmut_project))
    assert not result.startswith("Error")
    assert not result.startswith("Exception")
