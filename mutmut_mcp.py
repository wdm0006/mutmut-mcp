#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pip",
#   "fastmcp>=2.14.0,<3.0.0",
#   "mutmut"
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
from typing import List, Optional

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Mutmut Manager")

# mutmut 3.x keeps its state in a `mutants/` directory; older mutmut used a `.mutmut-cache` file.
MUTMUT_STATE_DIR = "mutants"
MUTMUT_LEGACY_CACHE_PATH = ".mutmut-cache"


def _run_command(command: List[str]) -> str:
    """Helper function to run a shell command and return output or error."""
    try:
        result = subprocess.run(command, shell=False, capture_output=True, text=True)
        if result.stderr:
            separator = "" if not result.stdout or result.stdout.endswith("\n") else "\n"
            return f"{result.stdout}{separator}Error: {result.stderr}"
        return result.stdout
    except Exception as e:
        return f"Exception occurred: {str(e)}"


def _get_mutmut_path(venv_path: str) -> str:
    """Get the path to the mutmut binary in a virtual environment."""
    if os.name != "nt":
        return os.path.join(venv_path, "bin", "mutmut")
    return os.path.join(venv_path, "Scripts", "mutmut.exe")


def _run_mutmut_cli(args: list, venv_path: Optional[str] = None) -> str:
    """Run mutmut CLI with given arguments, using venv if provided."""
    if venv_path:
        mutmut_path = _get_mutmut_path(venv_path)
        if not os.path.exists(mutmut_path):
            return f"Error: mutmut not found in the specified venv at {mutmut_path}. Please ensure mutmut is installed in the venv."
        command = [mutmut_path] + args
    else:
        command = ["mutmut"] + args
    return _run_command(command)


def run_mutmut(target: str = "", options: str = "", venv_path: Optional[str] = None) -> str:
    """
    Run a mutation testing session with `mutmut run`.

    In mutmut 3.x the files to mutate are configured via `[mutmut] paths_to_mutate=` in
    setup.cfg / pyproject.toml, not passed on the command line. `mutmut run` instead accepts
    an optional list of mutant-name filters (e.g. 'mypkg.module.x_func__mutmut_1'); leaving
    `target` empty runs the full suite. If a virtual environment path is provided, mutmut is
    run from that environment.

    Args:
        target (str): Optional space-separated mutant-name filter(s) to run. Empty runs all mutants.
        options (str): Additional `mutmut run` flags (e.g., '--max-children 4'). Defaults to empty.
        venv_path (Optional[str]): Path to the project's virtual environment to use for running mutmut. Defaults to None.

    Returns:
        str: Summary of the mutation testing run, or error message if the run fails.
    """
    args = ["run"]
    if target:
        args += target.split()
    if options:
        args += options.split()
    return _run_mutmut_cli(args, venv_path)


def show_results(venv_path: Optional[str] = None) -> str:
    """
    Display overall results from the last mutmut run using the mutmut CLI.
    Returns the plain text output.
    """
    return _run_mutmut_cli(["results"], venv_path)


def _parse_results(output: str) -> List[tuple]:
    """Parse `mutmut results` output into (mutant_name, status) pairs.

    mutmut 3.x prints one indented line per mutant: '    <mutant_name>: <status>'
    where status is one of killed / survived / no tests / timeout / suspicious /
    skipped / segfault.
    """
    parsed = []
    for line in output.splitlines():
        stripped = line.strip()
        if ": " not in stripped:
            continue
        name, _, status = stripped.rpartition(": ")
        name, status = name.strip(), status.strip()
        if name:
            parsed.append((name, status))
    return parsed


def _survivor_names(venv_path: Optional[str] = None) -> tuple:
    """Return (survivor_names, error). Survivors are mutants with status 'survived'.

    `error` is a non-empty string when the underlying `mutmut results` call failed;
    in that case `survivor_names` is empty.
    """
    output = show_results(venv_path)
    if output.startswith("Error") or output.startswith("Exception"):
        return [], output
    names = [name for name, status in _parse_results(output) if status == "survived"]
    return names, ""


def show_survivors(venv_path: Optional[str] = None) -> str:
    """
    List surviving mutants from the last mutmut run.

    mutmut 3.x has no `survivors` command, so this derives survivors from `mutmut results`
    (the mutants whose status is 'survived'). Returns one mutant name per line, or a message
    when there are none.
    """
    names, error = _survivor_names(venv_path)
    if error:
        return error
    if not names:
        return "No surviving mutants found."
    return "\n".join(names)


def rerun_mutmut_on_survivor(mutation_id: Optional[str] = None, venv_path: Optional[str] = None) -> str:
    """
    Rerun mutmut on a specific surviving mutant, or on all current survivors.

    mutmut 3.x has no `--rerun`/`--rerun-all` flags; `mutmut run <mutant_name>` reruns a
    single mutant. When no `mutation_id` is given, this reruns every currently-surviving
    mutant by passing their names to `mutmut run`.
    """
    if mutation_id:
        return _run_mutmut_cli(["run", mutation_id], venv_path)
    names, error = _survivor_names(venv_path)
    if error:
        return error
    if not names:
        return "No surviving mutants found."
    return _run_mutmut_cli(["run", *names], venv_path)


def clean_mutmut_cache(venv_path: Optional[str] = None) -> str:
    """
    Remove mutmut's on-disk state so the next run starts fresh.

    mutmut 3.x has no `clean` command and stores state in a `mutants/` directory; this removes
    that directory (and a legacy `.mutmut-cache` file if present). Returns a confirmation message.
    """
    removed = []
    try:
        if os.path.isdir(MUTMUT_STATE_DIR):
            shutil.rmtree(MUTMUT_STATE_DIR)
            removed.append(f"{MUTMUT_STATE_DIR}/")
        if os.path.exists(MUTMUT_LEGACY_CACHE_PATH):
            os.remove(MUTMUT_LEGACY_CACHE_PATH)
            removed.append(MUTMUT_LEGACY_CACHE_PATH)
    except Exception as e:
        return f"Failed to clear mutmut state: {str(e)}"
    if removed:
        return f"Mutmut state cleared successfully ({', '.join(removed)})."
    return "No mutmut state found to clear."


def show_mutant(mutation_id: str, venv_path: Optional[str] = None) -> str:
    """
    Show the code diff and details for a specific mutant using mutmut show.
    Args:
        mutation_id (str): The ID of the mutant to show.
        venv_path (Optional[str]): Path to the virtual environment, if any.
    Returns:
        str: The output of 'mutmut show <mutation_id>'.
    """
    if not mutation_id:
        return "Error: mutation_id is required."
    return _run_mutmut_cli(["show", mutation_id], venv_path)


def prioritize_survivors(venv_path: Optional[str] = None) -> dict:
    """
    Prioritize surviving mutants by likely materiality, filtering out log/debug-only changes and ranking by potential impact.
    Returns a sorted list of survivors with reasons for prioritization.
    """
    names, error = _survivor_names(venv_path)
    if error:
        return {"prioritized": [], "message": error}
    if not names:
        return {"prioritized": [], "message": "No surviving mutants found."}
    noise_tokens = {"log", "debug", "print", "logger", "logging"}
    prioritized = []
    for name in names:
        # Heuristic: deprioritize survivors in log/debug code, prioritize likely-material logic.
        # Match whole name tokens (split on '.'/'_') so "logic" isn't mistaken for "log".
        tokens = set(name.lower().replace(".", "_").split("_"))
        if tokens & noise_tokens:
            reason = "Likely log/debug only, deprioritized."
            score = 0
        else:
            reason = "Potentially material logic, prioritize."
            score = 1
        prioritized.append({"mutant_id": name, "score": score, "reason": reason, "raw": name})
    # Sort by score descending (material first)
    prioritized.sort(key=lambda x: x["score"], reverse=True)
    return {"prioritized": prioritized, "message": "Survivors prioritized by likely materiality."}


# --- Register tools with MCP server (explicit registration keeps functions callable) ---
mcp.tool()(run_mutmut)
mcp.tool()(show_results)
mcp.tool()(show_survivors)
mcp.tool()(rerun_mutmut_on_survivor)
mcp.tool()(clean_mutmut_cache)
mcp.tool()(show_mutant)
mcp.tool()(prioritize_survivors)


def main():
    """Entry point for the Mutmut MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
