# mutmut-mcp — Agent Onboarding

Repository: `wdm0006/mutmut-mcp` — a Model Context Protocol (MCP) server for managing
mutation testing with [mutmut](https://mutmut.readthedocs.io/). Stdio transport: no web
port, no database, no external services.

## Stack

| Layer | Choice |
|---|---|
| Language | Python >= 3.10 (CI matrix: 3.10 / 3.11 / 3.12; sandbox venv runs 3.13.14) |
| Package manager | uv (`uv.lock` committed); build backend hatchling |
| Runtime deps | fastmcp >=3.4.5,<4 (installed 3.4.7), mutmut >=3,<4 (installed 3.7.0) |
| Dev deps | ruff (lint + format, line-length 120), pytest, pre-commit |
| App | Single-module MCP server `mutmut_mcp.py` — 7 tools, FastMCP stdio transport |
| Services | None. No DB, no Redis; Docker not needed for dev (Dockerfile is Smithery deploy only) |
| Env vars | None required |

## Commands

uv lives at `~/.local/bin/uv` (0.12.10) in the sandbox — prefix fresh shells with
`export PATH="$HOME/.local/bin:$PATH"`.

| Task | Command |
|---|---|
| Install deps (first time) | `make install` (runs `uv venv .venv --seed` + `uv pip install -e ".[dev]"`) |
| Lint | `uv run ruff check .` |
| Format check | `uv run ruff format --check .` |
| Tests | `uv run pytest tests/ -v` |
| Start the MCP server | `uv run mutmut_mcp.py` (stdio; console script: `.venv/bin/mutmut-mcp`) |

**Quirk:** every `make` target depends on `install`, and `uv venv` errors once `.venv`
exists. After the first install, call the underlying tools directly (`uv run ...`) or
use `UV_VENV_CLEAR=1 make <target>`.

## Codebase map

Tiny repo (2 top-level dirs, no sub-apps) — full table in [codebase-map.md](codebase-map.md).

| Path | What it is |
|---|---|
| `mutmut_mcp.py` | The whole server: FastMCP instance, 7 `@mcp.tool()` handlers, per-project locking, mutmut subprocess wrapper, `main()` |
| `tests/` | 3 test files, 90 tests (unit with mocked subprocess + real-CLI integration) |
| `.github/workflows/ci.yml` | CI: ruff lint/format + pytest matrix 3.10-3.12 on uv |
| `Makefile`, `pyproject.toml`, `uv.lock` | Build/test entry points and lockfile |
| `Dockerfile`, `smithery.yaml` | Smithery deployment (python:3.11-slim, stdio start) |

## Local verification (how to prove the stack works)

1. `export PATH="$HOME/.local/bin:$PATH"` then `make install` (once).
2. `uv run ruff check .` — expect "All checks passed!"
3. `uv run ruff format --check .` — expect "5 files already formatted"
4. `uv run pytest tests/ -v` — expect 90 passed
5. MCP protocol check: speak JSON-RPC over stdio — initialize + tools/list must return
   the 7 tools (run_mutmut, show_results, show_survivors, rerun_mutmut_on_survivor,
   clean_mutmut_cache, show_mutant, prioritize_survivors). Worked examples in
   `.obvious/skills/local-dev/SKILL.md`.

## Local Verification Summary (onboarding run, 2026-09-06 UTC)

- `uv pip install -e ".[dev]"` — ok (fastmcp 3.4.7, mutmut 3.7.0, Python 3.13.14)
- `uv run ruff check .` — All checks passed
- `uv run ruff format --check .` — 5 files already formatted
- `uv run pytest tests/ -v` — **90 passed** in ~12s
- End-to-end MCP stdio session (fastmcp Client → spawned `mutmut_mcp.py`): initialize
  handshake ok, 7 tools listed; `run_mutmut` (no target, `--max-children 1`) on a
  3-function fixture checked 5/5 mutants at ~19 mutations/s; `show_results` returned
  2 survived + 2 no-tests with the third function's mutant killed — exactly what
  `tests/test_integration.py` predicts; `show_survivors`, `prioritize_survivors`,
  `show_mutant` (real diff), `rerun_mutmut_on_survivor`, `clean_mutmut_cache` (state
  dir removed) all returned `is_error=False`.
- Raw JSON-RPC probe of the installed `.venv/bin/mutmut-mcp` console script:
  initialize + tools/list ok (serverInfo "Mutmut Manager").

## Sandbox snapshot

- Captured live session (sandbox): `itoapbj81kv2jrcqgnt8g`
- Template: `bi6id75b8m19v4j98edk:default`
- Built at: `2026-09-06T20:23:34.519Z` (UTC)
- State at capture: `main` @ 1672bbb clean, `.venv` with deps installed, uv 0.12.10 at `~/.local/bin`

## Conventions

- Ruff: line-length 120, double quotes, rules E/W/F/I/C/B (E501, C901 ignored).
- pre-commit: trailing-whitespace, end-of-file-fixer, check-yaml, check-added-large-files, ruff --fix, ruff-format.
- PRs target `main`; history merges via merge commits.
