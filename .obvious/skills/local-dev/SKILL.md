---
name: local-dev
description: Bring up and verify the mutmut-mcp dev environment (uv venv, ruff, pytest, live MCP stdio session)
---

# Local dev — mutmut-mcp

Durable record of the onboarding run (2026-09-06). Everything below was executed and
verified in the sandbox; re-run it the same way.

## Environment

- Python >= 3.10 required; the sandbox venv uses 3.13.14 and deps + all tests pass on it.
- uv 0.12.10 at `~/.local/bin/uv` (bootstrap: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- No external services, no env vars, no ports — stdio MCP server.

## Bring-up

```bash
export PATH="$HOME/.local/bin:$PATH"
make install   # uv venv .venv --seed && uv pip install -e ".[dev]"  — ONCE
```

Quirk: every `make` target depends on `install`, and `uv venv` errors when `.venv`
already exists. After the first install, run the tools directly:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -v
```

or `UV_VENV_CLEAR=1 make test` to rebuild the venv every time.

## Start the server

```bash
uv run mutmut_mcp.py   # stdio JSON-RPC; equivalent: .venv/bin/mutmut-mcp
```

No HTTP port — clients speak MCP over stdin/stdout. Smithery and the Dockerfile use
`python mutmut_mcp.py` the same way.

## Primary-flow verification (MCP session)

Fixture pattern (mirrors `tests/test_integration.py`): `foo.py` with 3 functions,
`test_foo.py` testing two of them, `setup.cfg` containing `[mutmut]` +
`source_paths=foo.py`. Then drive the spawned server with fastmcp's client:

```python
import os
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

transport = StdioTransport(command=".venv/bin/python", args=["mutmut_mcp.py"], cwd=".")
async with Client(transport) as client:
    tools = await client.list_tools()          # expect the 7 tools
    await client.call_tool("run_mutmut", {
        "options": "--max-children 1",         # no target = full run
        "project_path": "<fixture dir>",
        "venv_path": ".venv",                  # repo venv holds mutmut + pytest
    })
    print(await client.call_tool("show_results", {"project_path": "<fixture dir>", "venv_path": ".venv"}))
```

Verified outcome: 5/5 mutants checked at ~19 mutations/s; run summary 1 killed, 2
no-tests, 2 survived; `show_results` lists `foo.x_scale__mutmut_1/2: survived` and
`foo.x_untested_discount__mutmut_1/2: no tests` (killed mutants are omitted).
A raw JSON-RPC variant (printf initialize + tools/list into `.venv/bin/mutmut-mcp`)
also works and proves the installed console script.

## Evidence from the onboarding run

- `uv run ruff check .` — All checks passed
- `uv run ruff format --check .` — 5 files already formatted
- `uv run pytest tests/ -v` — 90 passed in ~12s (includes real-CLI integration tests)
- Full MCP session: initialize → tools/list (7) → run_mutmut → show_results →
  show_survivors → prioritize_survivors → show_mutant (real diff) →
  rerun_mutmut_on_survivor → clean_mutmut_cache (mutants/ dir removed) — all
  `is_error=False`.

## Gotchas

- `make <any target>` fails once `.venv` exists (see Bring-up).
- mutmut writes its `mutants/` state dir inside the *project_path* fixture, not the repo.
- Passing a `target` to `run_mutmut` can leave mutants "not checked" (observed with
  `target="foo.py"`); the repo's own integration tests exercise the no-target form.
- survived/killed statuses can flake (segfault) on some platforms even with
  `--max-children 1`; `no tests` is stable (noted in `tests/test_integration.py`).
- fastmcp prints an "Update available: 4.0.3" banner to stderr — harmless; stay on <4.
