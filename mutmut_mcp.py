#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pip",
#   "fastmcp>=3.4.5,<4.0.0",
#   "mutmut>=3,<4"
# ]
# ///

"""
Mutmut MCP Server

This script provides a Model Context Protocol (MCP) server for managing mutation testing
with mutmut. It offers tools to run mutation tests, analyze results, and guide users
on improving test coverage.

Dependencies for standalone execution with uv run:
# uv run --with fastmcp --with mutmut mutmut_mcp.py
"""

import os
import shutil
import subprocess
import threading
from typing import List, Optional

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Mutmut Manager")

# mutmut 3.x keeps its state in a `mutants/` directory; older mutmut used a `.mutmut-cache` file.
MUTMUT_STATE_DIR = "mutants"
MUTMUT_LEGACY_CACHE_PATH = ".mutmut-cache"

# `mutmut results` statuses this server reasons about.
STATUS_SURVIVED = "survived"
STATUS_NO_TESTS = "no tests"
STATUS_NOT_CHECKED = "not checked"
STATUS_INTERRUPTED = "check was interrupted by user"

_project_locks: dict[str, threading.Lock] = {}
_project_locks_lock = threading.Lock()


def _canonical_project_path(project_path: Optional[str]) -> str:
    """Return one absolute path for all spellings of a project directory."""
    return os.path.abspath(project_path or ".")


def _project_lock(project_path: str) -> threading.Lock:
    """Return the lock for a canonical project path."""
    with _project_locks_lock:
        return _project_locks.setdefault(project_path, threading.Lock())


def _busy_error(project_path: str) -> str:
    return (
        f"Error: a mutmut operation is already in progress for {project_path}. "
        "Wait for it to finish before starting another."
    )


def _run_command(command: List[str], cwd: Optional[str] = None) -> str:
    """Helper function to run a shell command and return its output plus any diagnostics.

    stdout is always preserved: mutmut exits non-zero when it finds survivors, which is a
    result rather than a failure. stderr is appended when present, but only a command that
    actually failed gets the `Error:` label — a successful command's stderr (a mutmut
    deprecation warning, for example) is labelled `Warning:` so callers do not treat it as
    a failed call.
    """
    try:
        result = subprocess.run(command, shell=False, capture_output=True, text=True, cwd=cwd)
        if result.stderr:
            separator = "" if not result.stdout or result.stdout.endswith("\n") else "\n"
            label = "Error" if result.returncode != 0 else "Warning"
            return f"{result.stdout}{separator}{label}: {result.stderr}"
        return result.stdout
    except Exception as e:
        return f"Exception occurred: {str(e)}"


def _get_mutmut_path(venv_path: str) -> str:
    """Get the path to the mutmut binary in a virtual environment."""
    if os.name != "nt":
        return os.path.join(venv_path, "bin", "mutmut")
    return os.path.join(venv_path, "Scripts", "mutmut.exe")


def _validate_project_path(project_path: Optional[str]) -> str:
    """Return an error string when `project_path` is set but is not an existing directory."""
    if project_path and not os.path.isdir(project_path):
        return f"Error: project_path {project_path} is not an existing directory."
    return ""


def _resolve_venv_path(venv_path: str, project_path: Optional[str]) -> str:
    """Resolve a relative `venv_path` (e.g. '.venv') against `project_path` when both are given."""
    if project_path and not os.path.isabs(venv_path):
        return os.path.join(project_path, venv_path)
    return venv_path


def _run_mutmut_cli(args: list, venv_path: Optional[str] = None, project_path: Optional[str] = None) -> str:
    """Run mutmut CLI with given arguments, using venv if provided, from `project_path` if given."""
    error = _validate_project_path(project_path)
    if error:
        return error
    project_path = _canonical_project_path(project_path)
    if venv_path:
        mutmut_path = _get_mutmut_path(_resolve_venv_path(venv_path, project_path))
        if not os.path.exists(mutmut_path):
            return f"Error: mutmut not found in the specified venv at {mutmut_path}. Please ensure mutmut is installed in the venv."
        command = [mutmut_path] + args
    else:
        command = ["mutmut"] + args
    return _run_command(command, cwd=project_path)


@mcp.tool()
def run_mutmut(
    target: str = "", options: str = "", venv_path: Optional[str] = None, project_path: Optional[str] = None
) -> str:
    """
    Run a mutation testing session with `mutmut run`.

    In mutmut 3.x the files to mutate are configured via `[mutmut] source_paths=` in
    setup.cfg / pyproject.toml, not passed on the command line. `mutmut run` instead accepts
    an optional list of mutant-name filters (e.g. 'mypkg.module.x_func__mutmut_1'); leaving
    `target` empty runs the full suite. If a virtual environment path is provided, mutmut is
    run from that environment.

    Only one mutating operation may be in flight per project. If another `run_mutmut`,
    `rerun_mutmut_on_survivor` or `clean_mutmut_cache` call is already running for the same
    project, this returns `Error: a mutmut operation is already in progress for <project>.`
    without starting anything — wait for that operation to finish and call again.

    Args:
        target (str): Optional space-separated mutant-name filter(s) to run. Empty runs all mutants.
        options (str): Additional `mutmut run` flags (e.g., '--max-children 4'). Defaults to empty.
        venv_path (Optional[str]): Path to the project's virtual environment to use for running mutmut.
            A relative path (e.g. '.venv') is resolved against `project_path`. Defaults to None.
        project_path (Optional[str]): Directory to run mutmut in — the directory holding the project's
            mutmut configuration, source and tests. Defaults to the server's working directory.

    Returns:
        str: Summary of the mutation testing run, or error message if the run fails.
    """
    args = ["run"]
    if target:
        args += target.split()
    if options:
        args += options.split()
    error = _validate_project_path(project_path)
    if error:
        return error
    canonical_project = _canonical_project_path(project_path)
    lock = _project_lock(canonical_project)
    if not lock.acquire(blocking=False):
        return _busy_error(canonical_project)
    try:
        return _run_mutmut_cli(args, venv_path, canonical_project)
    finally:
        lock.release()


@mcp.tool()
def show_results(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> str:
    """
    Display overall results from the last mutmut run using the mutmut CLI.

    Args:
        venv_path (Optional[str]): Path to the project's virtual environment. A relative path is
            resolved against `project_path`. Defaults to None.
        project_path (Optional[str]): Directory holding the project's mutmut configuration and state.
            Defaults to the server's working directory.

    Returns the plain text output.
    """
    return _run_mutmut_cli(["results"], venv_path, project_path)


def _parse_results(output: str) -> List[tuple]:
    """Parse `mutmut results` output into (mutant_name, status) pairs.

    mutmut 3.x prints one indented line per mutant: '    <mutant_name>: <status>'
    where status is one of killed / survived / no tests / timeout / suspicious /
    skipped / segfault. Only lines of that exact shape are parsed: the raw line
    must start with mutmut's indentation and the mutant name must contain no
    whitespace, so annotated stderr and traceback text mixed into the output
    cannot be read as a mutant.
    """
    parsed = []
    for line in output.splitlines():
        if not line[:1].isspace():
            continue
        stripped = line.strip()
        if ": " not in stripped:
            continue
        name, _, status = stripped.rpartition(": ")
        name, status = name.strip(), status.strip()
        if not name or any(char.isspace() for char in name):
            continue
        parsed.append((name, status))
    return parsed


def _status_counts(results: List[tuple]) -> dict:
    """Count the statuses in parsed `mutmut results` pairs."""
    counts: dict = {}
    for _, status in results:
        counts[status] = counts.get(status, 0) + 1
    return counts


def _result_summary(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> tuple:
    """Return (names_by_status, status_counts, error) from one results call."""
    output = show_results(venv_path, project_path)
    if output.startswith("Error") or output.startswith("Exception"):
        return {}, {}, output
    results = _parse_results(output)
    grouped: dict = {}
    for name, status in results:
        grouped.setdefault(status, []).append(name)
    return grouped, _status_counts(results), ""


def _names_by_status(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> tuple:
    """Return (names_by_status, error) from a single `mutmut results` call.

    `names_by_status` maps each status mutmut reported ('survived', 'no tests', ...) to the
    mutant names carrying it, in the order mutmut printed them. `error` is a non-empty string
    when the underlying call failed; in that case the mapping is empty.
    """
    grouped, _, error = _result_summary(venv_path, project_path)
    return grouped, error


def _unresolved_note(status_counts: dict) -> str:
    """Describe incomplete results, or return an empty string when all are resolved."""
    not_checked = status_counts.get(STATUS_NOT_CHECKED, 0)
    interrupted = status_counts.get(STATUS_INTERRUPTED, 0)
    parts = []
    if not_checked:
        subject = "mutant is" if not_checked == 1 else "mutants are"
        parts.append(f"{not_checked} {subject} not checked")
    if interrupted:
        subject = "mutant check was" if interrupted == 1 else "mutant checks were"
        parts.append(f"{interrupted} {subject} interrupted by the user")
    if not parts:
        return ""
    return f"{' and '.join(parts)} — run mutmut again for a complete picture."


def _survivor_names(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> tuple:
    """Return (survivor_names, error). Survivors are mutants with status 'survived'.

    `error` is a non-empty string when the underlying `mutmut results` call failed;
    in that case `survivor_names` is empty.
    """
    grouped, _, error = _result_summary(venv_path, project_path)
    return grouped.get(STATUS_SURVIVED, []), error


@mcp.tool()
def show_survivors(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> str:
    """
    List surviving and uncovered mutants from the last mutmut run.

    mutmut 3.x has no `survivors` command, so this derives everything from `mutmut results`.
    Survivors (status 'survived') are listed first, one name per line. Mutants that no test
    exercises at all (status 'no tests') follow in a separate labelled section — they are not
    survivors, but they are the strongest signal of a coverage gap, so they are never dropped.
    Unchecked or interrupted mutants add an incomplete-results note. The plain
    'No surviving mutants found.' message means every reported mutant has a definite result.

    Args:
        venv_path (Optional[str]): Path to the project's virtual environment. A relative path is
            resolved against `project_path`. Defaults to None.
        project_path (Optional[str]): Directory holding the project's mutmut configuration and state.
            Defaults to the server's working directory.
    """
    grouped, status_counts, error = _result_summary(venv_path, project_path)
    if error:
        return error
    survivors = grouped.get(STATUS_SURVIVED, [])
    uncovered = grouped.get(STATUS_NO_TESTS, [])
    unresolved_note = _unresolved_note(status_counts)
    if not survivors and not uncovered:
        message = "No surviving mutants found."
        if unresolved_note:
            message = f"No surviving mutants found, but {unresolved_note}"
        return message
    sections = []
    if survivors:
        sections.append("\n".join(survivors))
    if uncovered:
        sections.append(f"Not covered by any test ({len(uncovered)}):\n" + "\n".join(uncovered))
    if unresolved_note:
        sections.append(f"Incomplete results: {unresolved_note}")
    return "\n\n".join(sections)


@mcp.tool()
def rerun_mutmut_on_survivor(
    mutation_id: Optional[str] = None, venv_path: Optional[str] = None, project_path: Optional[str] = None
) -> str:
    """
    Rerun mutmut on a specific surviving mutant, or on all current survivors.

    mutmut 3.x has no `--rerun`/`--rerun-all` flags; `mutmut run <mutant_name>` reruns a
    single mutant. When no `mutation_id` is given, this reruns every currently-surviving
    mutant by passing their names to `mutmut run`.

    Only one mutating operation may be in flight per project. If another `run_mutmut`,
    `rerun_mutmut_on_survivor` or `clean_mutmut_cache` call is already running for the same
    project, this returns `Error: a mutmut operation is already in progress for <project>.`
    without starting anything — wait for that operation to finish and call again.

    Args:
        mutation_id (Optional[str]): Mutant to rerun. None reruns every current survivor.
        venv_path (Optional[str]): Path to the project's virtual environment. A relative path is
            resolved against `project_path`. Defaults to None.
        project_path (Optional[str]): Directory holding the project's mutmut configuration and state.
            Defaults to the server's working directory.
    """
    error = _validate_project_path(project_path)
    if error:
        return error
    canonical_project = _canonical_project_path(project_path)
    lock = _project_lock(canonical_project)
    if not lock.acquire(blocking=False):
        return _busy_error(canonical_project)
    try:
        if mutation_id:
            return _run_mutmut_cli(["run", mutation_id], venv_path, canonical_project)
        names, error = _survivor_names(venv_path, canonical_project)
        if error:
            return error
        if not names:
            return "No surviving mutants found."
        return _run_mutmut_cli(["run", *names], venv_path, canonical_project)
    finally:
        lock.release()


@mcp.tool()
def clean_mutmut_cache(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> str:
    """
    Remove mutmut's on-disk state so the next run starts fresh.

    mutmut 3.x has no `clean` command and stores state in a `mutants/` directory; this removes
    that directory (and a legacy `.mutmut-cache` file if present). Returns a confirmation message.

    Only one mutating operation may be in flight per project. If another `run_mutmut`,
    `rerun_mutmut_on_survivor` or `clean_mutmut_cache` call is already running for the same
    project, this returns `Error: a mutmut operation is already in progress for <project>.`
    without removing anything — wait for that operation to finish and call again.

    Args:
        venv_path (Optional[str]): Unused; accepted for signature symmetry with the other tools.
        project_path (Optional[str]): Directory to clean state in — only state inside this directory
            is removed. Defaults to the server's working directory.
    """
    error = _validate_project_path(project_path)
    if error:
        return error
    base = _canonical_project_path(project_path)
    lock = _project_lock(base)
    if not lock.acquire(blocking=False):
        return _busy_error(base)
    removed = []
    try:
        state_dir = os.path.normpath(os.path.join(base, MUTMUT_STATE_DIR))
        legacy_cache = os.path.normpath(os.path.join(base, MUTMUT_LEGACY_CACHE_PATH))
        if os.path.isdir(state_dir):
            shutil.rmtree(state_dir)
            removed.append(f"{state_dir}/")
        if os.path.exists(legacy_cache):
            os.remove(legacy_cache)
            removed.append(legacy_cache)
    except Exception as e:
        return f"Failed to clear mutmut state: {str(e)}"
    finally:
        lock.release()
    if removed:
        return f"Mutmut state cleared successfully ({', '.join(removed)})."
    return "No mutmut state found to clear."


@mcp.tool()
def show_mutant(mutation_id: str, venv_path: Optional[str] = None, project_path: Optional[str] = None) -> str:
    """
    Show the code diff and details for a specific mutant using mutmut show.
    Args:
        mutation_id (str): The ID of the mutant to show.
        venv_path (Optional[str]): Path to the virtual environment, if any. A relative path is
            resolved against `project_path`.
        project_path (Optional[str]): Directory holding the project's mutmut configuration and state.
            Defaults to the server's working directory.
    Returns:
        str: The output of 'mutmut show <mutation_id>'.
    """
    if not mutation_id:
        return "Error: mutation_id is required."
    return _run_mutmut_cli(["show", mutation_id], venv_path, project_path)


@mcp.tool()
def prioritize_survivors(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> dict:
    """
    Rank the mutants worth acting on from the last mutmut run, highest score first.

    Each entry carries a `status`: 'no tests' for a mutant no test exercises at all, or
    'survived' for one a test ran but failed to detect. Scores are a rank, not a flag —
    2 = uncovered, 1 = likely-material survivor, 0 = likely log/debug-only survivor — so
    coverage gaps sort above survivors and log/debug names sort last.
    The response also includes `status_counts` for every status mutmut reported; unchecked
    or interrupted mutants add an incomplete-results note without entering the ranking.

    Args:
        venv_path (Optional[str]): Path to the project's virtual environment. A relative path is
            resolved against `project_path`. Defaults to None.
        project_path (Optional[str]): Directory holding the project's mutmut configuration and state.
            Defaults to the server's working directory.
    """
    grouped, status_counts, error = _result_summary(venv_path, project_path)
    if error:
        return {"prioritized": [], "message": error, "status_counts": {}}
    survivors = grouped.get(STATUS_SURVIVED, [])
    uncovered = grouped.get(STATUS_NO_TESTS, [])
    if not survivors and not uncovered:
        message = "No surviving mutants found."
        unresolved_note = _unresolved_note(status_counts)
        if unresolved_note:
            message = f"No surviving mutants found, but {unresolved_note}"
        return {"prioritized": [], "message": message, "status_counts": status_counts}
    noise_tokens = {"log", "debug", "print", "logger", "logging"}
    prioritized = []
    for name in uncovered:
        prioritized.append(
            {
                "mutant_id": name,
                "score": 2,
                "reason": "No test covers this mutant.",
                "raw": name,
                "status": STATUS_NO_TESTS,
            }
        )
    for name in survivors:
        # Heuristic: deprioritize survivors in log/debug code, prioritize likely-material logic.
        # Match whole name tokens (split on '.'/'_') so "logic" isn't mistaken for "log".
        tokens = set(name.lower().replace(".", "_").split("_"))
        if tokens & noise_tokens:
            reason = "Likely log/debug only, deprioritized."
            score = 0
        else:
            reason = "Potentially material logic, prioritize."
            score = 1
        prioritized.append(
            {"mutant_id": name, "score": score, "reason": reason, "raw": name, "status": STATUS_SURVIVED}
        )
    # Sort by score descending (uncovered first, then material survivors); stable, so the
    # order within each score band is the order mutmut reported.
    prioritized.sort(key=lambda x: x["score"], reverse=True)
    if uncovered:
        message = (
            f"{len(uncovered)} mutant(s) not covered by any test, ranked above "
            f"{len(survivors)} survivor(s) prioritized by likely materiality."
        )
    else:
        message = "Survivors prioritized by likely materiality."
    unresolved_note = _unresolved_note(status_counts)
    if unresolved_note:
        message = f"{message} Incomplete results: {unresolved_note}"
    return {"prioritized": prioritized, "message": message, "status_counts": status_counts}


def main():
    """Entry point for the Mutmut MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
