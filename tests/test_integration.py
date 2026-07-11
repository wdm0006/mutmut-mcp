"""End-to-end checks against the real mutmut 3.x CLI.

The unit tests mock the subprocess layer, so they lock in argv without proving the
commands exist. These tests actually invoke the installed `mutmut` binary to confirm
the tools speak a CLI that mutmut 3.4.0 accepts. They are skipped when `mutmut` is not
on PATH.
"""

import os
import shutil

import pytest

from mutmut_mcp import clean_mutmut_cache, show_results, show_survivors

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
