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
import re
import shutil
import subprocess
import threading
from typing import NamedTuple, Optional, TypedDict

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Mutmut Manager")

# mutmut 3.x keeps its state in a `mutants/` directory; older mutmut used a `.mutmut-cache` file.
MUTMUT_STATE_DIR = "mutants"
MUTMUT_LEGACY_CACHE_PATH = ".mutmut-cache"

# `mutmut results` statuses this server reasons about.
STATUS_SURVIVED = "survived"
STATUS_NO_TESTS = "no tests"
STATUS_KILLED = "killed"
STATUS_NOT_CHECKED = "not checked"
STATUS_INTERRUPTED = "check was interrupted by user"

_project_locks: dict[str, threading.Lock] = {}
_project_locks_lock = threading.Lock()


class _CommandOutcome(NamedTuple):
    """One command invocation's display text plus its explicit failure status.

    `failed` comes from the process exit code (or a local validation failure), never
    from the output text: stdout may legitimately begin with 'Error' and still be a
    successful call's content.
    """

    output: str
    failed: bool


class _RankedMutant(TypedDict):
    """One entry of the ranking `prioritize_survivors` returns."""

    mutant_id: str
    score: int
    reason: str
    raw: str
    status: str


class _FunctionGroup(TypedDict):
    """One function's entry in the `by_function` list `prioritize_survivors` returns."""

    function: str
    survived: int
    no_tests: int
    mutants: list[str]


def _function_key(name: str) -> str:
    """Return the function a mutant belongs to by dropping a trailing `__mutmut_<n>`.

    Heuristic over mutmut 3.x names (`pkg.mod.x_<func>__mutmut_<n>`): only the final
    suffix is removed, so `x_Class__method__mutmut_3` keeps its inner `__`. Names
    without the suffix are returned unchanged.
    """
    return re.sub(r"__mutmut_[0-9]+$", "", name)


def _by_function(survivors: list[str], uncovered: list[str]) -> list[_FunctionGroup]:
    """Group survivors and uncovered mutants by function, largest group first (stable on ties)."""
    groups: dict[str, _FunctionGroup] = {}
    for names, field in ((survivors, "survived"), (uncovered, "no_tests")):
        for name in names:
            key = _function_key(name)
            group = groups.setdefault(key, {"function": key, "survived": 0, "no_tests": 0, "mutants": []})
            group[field] += 1  # type: ignore[literal-required]
            group["mutants"].append(name)
    return sorted(groups.values(), key=lambda g: g["survived"] + g["no_tests"], reverse=True)


def _canonical_project_path(project_path: Optional[str]) -> str:
    """Return one absolute path for all spellings of a project directory."""
    return os.path.realpath(os.path.abspath(project_path or "."))


def _project_lock(project_path: str) -> threading.Lock:
    """Return the lock for a canonical project path."""
    with _project_locks_lock:
        return _project_locks.setdefault(project_path, threading.Lock())


def _busy_error(project_path: str) -> str:
    """Return the serialized error for a concurrent mutmut call on the same project.

    All tools take this branch instead of racing: mutmut 3.x keeps per-project
    state in `mutants/`, so two simultaneous runs against one project_path
    would corrupt each other's scratch tree.
    """
    return (
        f"Error: a mutmut operation is already in progress for {project_path}. "
        "Wait for it to finish before starting another."
    )


def _collapse_progress(text: str) -> str:
    """Reduce carriage-return progress frames to what a terminal would have shown.

    mutmut redraws its spinner with `\\r` and no newline, so one captured line holds every
    frame. Per `\\n`-delimited line, drop trailing `\\r` (CRLF content, not a frame), keep the
    text after the last remaining `\\r`, and strip the padding mutmut appends to frames.
    """
    lines = []
    for line in text.split("\n"):
        body = line.rstrip("\r")
        if "\r" in body:
            body = body.rsplit("\r", 1)[1].rstrip(" ")
        lines.append(body)
    return "\n".join(lines)


def _decode(stream: "bytes | str") -> str:
    """Decode a captured stream without letting text mode rewrite `\\r` into `\\n`."""
    return stream.decode(errors="replace") if isinstance(stream, bytes) else stream


def _run_command(command: list[str], cwd: Optional[str] = None, collapse_progress: bool = False) -> _CommandOutcome:
    """Helper function to run a shell command and return its output plus any diagnostics.

    stdout is always preserved: mutmut exits non-zero when it finds survivors, which is a
    result rather than a failure. stderr is appended when present, but only a command that
    actually failed gets the `Error:` label — a successful command's stderr (a mutmut
    deprecation warning, for example) is labelled `Warning:` so callers do not treat it as
    a failed call. `failed` carries the exit code (or an exception) as explicit status so
    callers never have to sniff the text.

    Streams are captured as bytes so mutmut's `\\r` progress frames survive until
    `collapse_progress` can collapse them; otherwise text mode would turn each frame into
    its own line. Without it, newlines are normalized exactly as text mode would.
    """
    try:
        result = subprocess.run(command, shell=False, capture_output=True, cwd=cwd)
        streams = []
        for raw in (result.stdout, result.stderr):
            text = _decode(raw)
            if collapse_progress:
                text = _collapse_progress(text)
            else:
                text = text.replace("\r\n", "\n").replace("\r", "\n")
            streams.append(text)
        stdout, stderr = streams
        if stderr:
            separator = "" if not stdout or stdout.endswith("\n") else "\n"
            label = "Error" if result.returncode != 0 else "Warning"
            output = f"{stdout}{separator}{label}: {stderr}"
        else:
            output = stdout
        return _CommandOutcome(output, failed=result.returncode != 0)
    except Exception as e:
        return _CommandOutcome(f"Exception occurred: {str(e)}", failed=True)


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


def _run_mutmut_cli(
    args: list[str], venv_path: Optional[str] = None, project_path: Optional[str] = None
) -> _CommandOutcome:
    """Run mutmut CLI with given arguments, using venv if provided, from `project_path` if given.

    Returns the display output plus the explicit failure status: a non-zero exit, an
    exception, or a local validation error — never a judgement based on the text.
    """
    error = _validate_project_path(project_path)
    if error:
        return _CommandOutcome(error, failed=True)
    project_path = _canonical_project_path(project_path)
    if venv_path:
        mutmut_path = _get_mutmut_path(_resolve_venv_path(venv_path, project_path))
        if not os.path.exists(mutmut_path):
            return _CommandOutcome(
                f"Error: mutmut not found in the specified venv at {mutmut_path}. Please ensure mutmut is installed in the venv.",
                failed=True,
            )
        command = [mutmut_path] + args
    else:
        command = ["mutmut"] + args
    if args[:1] == ["run"]:
        return _run_command(command, cwd=project_path, collapse_progress=True)
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
        return _run_mutmut_cli(args, venv_path, canonical_project).output
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
    return _run_mutmut_cli(["results"], venv_path, project_path).output


def _parse_results(output: str) -> list[tuple[str, str]]:
    """Parse `mutmut results` output into (mutant_name, status) pairs.

    mutmut 3.x prints one indented line per mutant: '    <mutant_name>: <status>'
    where status is one of killed / survived / no tests / timeout / suspicious /
    skipped / segfault. Only lines of that exact shape are parsed: the raw line
    must start with mutmut's indentation and the mutant name must contain no
    whitespace, so annotated stderr and traceback text mixed into the output
    cannot be read as a mutant.
    """
    parsed: list[tuple[str, str]] = []
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


def _status_counts(results: list[tuple[str, str]]) -> dict[str, int]:
    """Count the statuses in parsed `mutmut results` pairs."""
    counts: dict[str, int] = {}
    for _, status in results:
        counts[status] = counts.get(status, 0) + 1
    return counts


def _result_summary(
    venv_path: Optional[str] = None, project_path: Optional[str] = None, include_killed: bool = False
) -> tuple[dict[str, list[str]], dict[str, int], str]:
    """Return (names_by_status, status_counts, error) from one results call.

    With `include_killed` the call is `mutmut results --all true`, so counts cover the
    whole campaign rather than only the mutants the default view lists.

    `error` is a non-empty string only when the underlying call failed by its explicit
    status — a non-zero exit, an exception, or a local validation error — never because
    of how the output text begins.
    """
    args = ["results", "--all", "true"] if include_killed else ["results"]
    outcome = _run_mutmut_cli(args, venv_path, project_path)
    if outcome.failed:
        return {}, {}, outcome.output
    results = _parse_results(outcome.output)
    grouped: dict[str, list[str]] = {}
    for name, status in results:
        grouped.setdefault(status, []).append(name)
    return grouped, _status_counts(results), ""


def _unresolved_note(status_counts: dict[str, int]) -> str:
    """Describe incomplete results, or return an empty string when all are resolved."""
    not_checked = status_counts.get(STATUS_NOT_CHECKED, 0)
    interrupted = status_counts.get(STATUS_INTERRUPTED, 0)
    parts: list[str] = []
    if not_checked:
        subject = "mutant is" if not_checked == 1 else "mutants are"
        parts.append(f"{not_checked} {subject} not checked")
    if interrupted:
        subject = "mutant check was" if interrupted == 1 else "mutant checks were"
        parts.append(f"{interrupted} {subject} interrupted by the user")
    if not parts:
        return ""
    return f"{' and '.join(parts)} — run mutmut again for a complete picture."


def _survivor_names(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> tuple[list[str], str]:
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
    by_function = _by_function(survivors, uncovered)
    lines = [f"{g['function']}: {g['survived']} survived, {g['no_tests']} no tests" for g in by_function]
    sections.append(f"By function ({len(by_function)}):\n" + "\n".join(lines))
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
            return _run_mutmut_cli(["run", mutation_id], venv_path, canonical_project).output
        names, error = _survivor_names(venv_path, canonical_project)
        if error:
            return error
        if not names:
            return "No surviving mutants found."
        return _run_mutmut_cli(["run", *names], venv_path, canonical_project).output
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
    return _run_mutmut_cli(["show", mutation_id], venv_path, project_path).output


# The return annotation stays a bare `dict`: fastmcp derives the tool's output
# schema from it, and a TypedDict here would change the declared MCP schema.
# The entry shape is pinned by _RankedMutant instead.
@mcp.tool()
def prioritize_survivors(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> dict:
    """
    Rank the mutants worth acting on from the last mutmut run, highest score first.

    Each entry carries a `status`: 'no tests' for a mutant no test exercises at all, or
    'survived' for one a test ran but failed to detect. Scores are a rank, not a flag —
    2 = uncovered, 1 = likely-material survivor, 0 = likely log/debug-only survivor — so
    coverage gaps sort above survivors and log/debug names sort last.
    `by_function` groups the same mutants per function (name heuristic), largest group first,
    as `{function, survived, no_tests, mutants}`.
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
        return {"prioritized": [], "message": error, "status_counts": {}, "by_function": []}
    survivors = grouped.get(STATUS_SURVIVED, [])
    uncovered = grouped.get(STATUS_NO_TESTS, [])
    if not survivors and not uncovered:
        message = "No surviving mutants found."
        unresolved_note = _unresolved_note(status_counts)
        if unresolved_note:
            message = f"No surviving mutants found, but {unresolved_note}"
        return {"prioritized": [], "message": message, "status_counts": status_counts, "by_function": []}
    noise_tokens = {"log", "debug", "print", "logger", "logging"}
    prioritized: list[_RankedMutant] = []
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
    return {
        "prioritized": prioritized,
        "message": message,
        "status_counts": status_counts,
        "by_function": _by_function(survivors, uncovered),
    }


@mcp.tool()
def mutation_score(venv_path: Optional[str] = None, project_path: Optional[str] = None) -> dict:
    """
    Report the campaign's mutation score from one `mutmut results --all true` read.

    Returns `total` (every parsed mutant), `status_counts`, `killed`, `undetected`
    ('survived' plus 'no tests'), `score` (killed / total, rounded to 4 places) and a
    `message`. `score` is null when there are no results yet or when results are
    incomplete (unchecked or interrupted mutants), so a partial campaign never reports a
    misleading number. Use it to check whether the score improved after test edits.

    Args:
        venv_path (Optional[str]): Path to the project's virtual environment. A relative path is
            resolved against `project_path`. Defaults to None.
        project_path (Optional[str]): Directory holding the project's mutmut configuration and state.
            Defaults to the server's working directory.
    """
    grouped, status_counts, error = _result_summary(venv_path, project_path, include_killed=True)
    if error:
        return {"total": 0, "status_counts": {}, "killed": 0, "undetected": 0, "score": None, "message": error}
    total = sum(status_counts.values())
    killed = status_counts.get(STATUS_KILLED, 0)
    undetected = status_counts.get(STATUS_SURVIVED, 0) + status_counts.get(STATUS_NO_TESTS, 0)
    result: dict = {
        "total": total,
        "status_counts": status_counts,
        "killed": killed,
        "undetected": undetected,
        "score": None,
    }
    if total == 0:
        result["message"] = "No mutation results yet - run a campaign first."
        return result
    unresolved_note = _unresolved_note(status_counts)
    if unresolved_note:
        result["message"] = f"Incomplete results, score not computed: {unresolved_note}"
        return result
    result["score"] = round(killed / total, 4)
    result["message"] = f"{killed} of {total} mutants killed; {undetected} undetected."
    return result


def main():
    """Entry point for the Mutmut MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
