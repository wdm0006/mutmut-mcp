# Mutmut MCP
[![smithery badge](https://smithery.ai/badge/@wdm0006/mutmut-mcp)](https://smithery.ai/server/@wdm0006/mutmut-mcp)

A Model Context Protocol (MCP) server for managing mutation testing with [mutmut](https://mutmut.readthedocs.io/). This tool provides a set of programmatic APIs for running mutation tests, analyzing results, and improving test coverage in Python projects. It targets **mutmut 3.x**: mutants are named (`mymodule.x_add__mutmut_1`), reported per mutant with a status (`killed` / `survived` / `no tests` / `timeout` / `suspicious` / `skipped` / `segfault`), and configured through `[tool.mutmut]` in `pyproject.toml`.

## Features

- Run mutation testing sessions against the projects mutmut 3.x is configured for (`source_paths` in `[tool.mutmut]`), not per-target CLI flags
- Show results per mutant name and status, and list surviving mutations, including a labelled section for mutants no test covers
- Show the code diff for a single mutant and rerun one mutant, all survivors, or the survivors no test covers
- Rank survivors and uncovered mutants by likely materiality so an agent knows what to look at first
- Clean mutmut's per-project state (`mutants/` and the legacy cache) without touching other projects
- Serialize concurrent runs: two mutmut operations against the same project never race; the second gets a structured busy error
- Classify every failure by its explicit channel — non-zero exit, exception, or local validation — never by sniffing output text
- Practices what it preaches: this repository's own CI runs the server against itself with `source_paths = ["mutmut_mcp.py"]` and fails on mutation-strength regressions (see [How this server tests itself](#how-this-server-tests-itself))

## Install

```bash
# Run directly from GitHub (no install needed)
uvx --from git+https://github.com/wdm0006/mutmut-mcp mutmut-mcp

# Or install from source
git clone https://github.com/wdm0006/mutmut-mcp
cd mutmut-mcp
uv sync
uv run mutmut_mcp.py
```

### Installing via Smithery

To install mutmut-mcp for Claude Desktop automatically via [Smithery](https://smithery.ai/server/@wdm0006/mutmut-mcp):

```bash
npx -y @smithery/cli install @wdm0006/mutmut-mcp --client claude
```

## MCP Client Configuration

```json
{
  "mcpServers": {
    "mutmut": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/wdm0006/mutmut-mcp", "mutmut-mcp"]
    }
  }
}
```

## API / Tools

The following tools are available:

- `run_mutmut(target, options="", venv_path=None, project_path=None)` – Run a mutation testing session (`mutmut run`) for the project; survivors are expected and are a result, not a failure
- `show_results(venv_path=None, project_path=None)` – Show the last campaign's results per mutant name and status
- `show_survivors(venv_path=None, project_path=None)` – List surviving mutations, plus a labelled section for mutants no test covers
- `rerun_mutmut_on_survivor(mutation_id=None, venv_path=None, project_path=None)` – Rerun a specific mutant (`mutmut run <name>`) or all surviving mutants
- `clean_mutmut_cache(venv_path=None, project_path=None)` – Remove mutmut's `mutants/` state directory and legacy cache for the project
- `show_mutant(mutation_id, venv_path=None, project_path=None)` – Show the code diff and details for one named mutant
- `prioritize_survivors(venv_path=None, project_path=None)` – Rank survivors and uncovered mutants by likely materiality, highest score first

### `project_path`

Every tool accepts an optional `project_path`: the directory containing the project's mutmut
configuration (`[tool.mutmut]` in `pyproject.toml`), its source, its tests, and mutmut's
`mutants/` state directory. Mutmut runs with that directory as its working directory, and
`clean_mutmut_cache` removes state only from inside it.

Pass it whenever the server was not launched from the project directory — which is typical for
desktop MCP clients and `uvx`. Omitted, the tools fall back to the server process's working
directory, so existing calls behave as before. A `project_path` that is not an existing directory
returns an error without running mutmut or deleting anything.

### `venv_path`

`venv_path` points at the virtual environment holding the project's `mutmut` (the tools use
`<venv>/bin/mutmut`, or `<venv>\Scripts\mutmut.exe` on Windows); omitted, `mutmut` is taken from
`PATH`. A relative `venv_path` is resolved against `project_path` when both are given, so the
common `.venv` form works:

```json
{ "project_path": "/home/me/src/myproject", "venv_path": ".venv" }
```

## How this server tests itself

The repository's own `[tool.mutmut]` configuration points mutmut at the server module
(`source_paths = ["mutmut_mcp.py"]`), so CI runs the mutation-testing server against itself.
The campaign result is scored by `scripts/mutation_budget.py`, which reuses the server's own
strict results parser and fails the build when the number of undetected mutants (survived or
no tests) exceeds a fixed budget. The six allowed survivors are equivalent mutants, each
documented in [docs/mutation-waivers.md](docs/mutation-waivers.md); anything new is a
regression and must be killed by a named test or waived with a written reason.

Locally, the same loop is available via `make mutate`; `make coverage` enforces the measured
100% line-coverage floor, and `make typecheck` runs mypy.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
