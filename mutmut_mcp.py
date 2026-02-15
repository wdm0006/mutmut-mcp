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
import subprocess
from typing import List, Optional

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Mutmut Manager")

# Path to mutmut cache or results file (adjust if mutmut uses a different location)
MUTMUT_CACHE_PATH = ".mutmut-cache"


def _run_command(command: List[str]) -> str:
    """Helper function to run a shell command and return output or error."""
    try:
        result = subprocess.run(command, shell=False, capture_output=True, text=True)
        if result.returncode != 0:
            return f"Error: {result.stderr}"
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


def run_mutmut(target: str, options: str = "", venv_path: Optional[str] = None) -> str:
    """
    Run a full mutation testing session with mutmut on the specified target.

    This tool initiates mutation testing on the given module or package. You can provide
    additional mutmut options as needed. The output includes a summary of mutations tested,
    including counts of killed, survived, and timed-out mutations. If a virtual environment
    path is provided, mutmut will be run using the binaries from that environment to ensure
    compatibility with project-specific dependencies.

    Args:
        target (str): The module or package to run mutation testing on.
        options (str): Additional command-line options for mutmut (e.g., '--use-coverage'). Defaults to empty.
        venv_path (Optional[str]): Path to the project's virtual environment to use for running mutmut. Defaults to None.

    Returns:
        str: Summary of the mutation testing run, or error message if the run fails.
    """
    if venv_path:
        mutmut_path = _get_mutmut_path(venv_path)
        if not os.path.exists(mutmut_path):
            return f"Error: mutmut not found in the specified venv at {mutmut_path}. Please ensure mutmut is installed in the venv."
        command = [mutmut_path, "run", target] + options.split()
    else:
        command = ["mutmut", "run", target] + options.split()
    return _run_command(command)


def show_results(venv_path: Optional[str] = None) -> str:
    """
    Display overall results from the last mutmut run using the mutmut CLI.
    Returns the plain text output.
    """
    return _run_mutmut_cli(["results"], venv_path)


def show_survivors(venv_path: Optional[str] = None) -> str:
    """
    List details of surviving mutations from the last mutmut run using the mutmut CLI.
    Returns the plain text output.
    """
    return _run_mutmut_cli(["survivors"], venv_path)


def rerun_mutmut_on_survivor(mutation_id: Optional[str] = None, venv_path: Optional[str] = None) -> str:
    """
    Rerun mutmut on specific surviving mutations or all survivors after test updates using the mutmut CLI.
    Returns the plain text output.
    """
    if mutation_id:
        return _run_mutmut_cli(["run", "--rerun", mutation_id], venv_path)
    else:
        return _run_mutmut_cli(["run", "--rerun-all"], venv_path)


def clean_mutmut_cache(venv_path: Optional[str] = None) -> str:
    """
    Clean mutmut cache using the mutmut CLI (if available), otherwise remove .mutmut-cache file.
    Returns the plain text output or confirmation message.
    """
    # Try CLI first
    result = _run_mutmut_cli(["clean"], venv_path)
    if "Error" not in result:
        return result
    # Fallback: remove .mutmut-cache file
    try:
        if os.path.exists(MUTMUT_CACHE_PATH):
            os.remove(MUTMUT_CACHE_PATH)
            return "Mutmut cache cleared successfully."
        else:
            return "No mutmut cache found to clear."
    except Exception as e:
        return f"Failed to clear mutmut cache: {str(e)}"


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
    survivors_output = show_survivors(venv_path)
    if not survivors_output or "no surviving mutants" in survivors_output.lower():
        return {"prioritized": [], "message": "No surviving mutants found."}
    prioritized = []
    for line in survivors_output.splitlines():
        if not line.strip() or line.startswith("SURVIVED:") is False:
            continue
        # Example line: SURVIVED: mypackage.module.function_name:42 (some description)
        mutant_id = line.split(":", 1)[-1].strip()
        # Heuristic: deprioritize if log/debug, prioritize if in core logic
        if any(kw in line.lower() for kw in ["log", "debug", "print", "logger", "logging"]):
            reason = "Likely log/debug only, deprioritized."
            score = 0
        else:
            reason = "Potentially material logic, prioritize."
            score = 1
        prioritized.append({"mutant_id": mutant_id, "score": score, "reason": reason, "raw": line})
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
