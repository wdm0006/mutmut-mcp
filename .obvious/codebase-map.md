# Codebase map — wdm0006/mutmut-mcp

Tiny repo (2 top-level dirs, no sub-apps): one server module plus tests. Depth cap 2.

| Path | Kind | Notes |
|---|---|---|
| `mutmut_mcp.py` | code | Entire MCP server: `FastMCP("Mutmut Manager")`, 7 `@mcp.tool()` handlers (run_mutmut, show_results, show_survivors, rerun_mutmut_on_survivor, clean_mutmut_cache, show_mutant, prioritize_survivors), per-project threading locks, mutmut subprocess wrapper, `main()` entry |
| `tests/` | tests | 3 files, 90 tests total |
| `tests/test_mutmut_api.py` | tests | Unit tests per tool with mocked subprocess layer (801 lines) |
| `tests/test_integration.py` | tests | End-to-end against the real mutmut CLI; fixture projects in tmp dirs; skipped when `mutmut` is not on PATH |
| `tests/test_dependency_declarations.py` | tests | Guards pyproject + PEP-723 script dependency metadata |
| `.github/workflows/ci.yml` | CI | ruff check + format, pytest matrix (3.10/3.11/3.12), uv with cache |
| `Makefile` | build | `install` (uv venv + `uv pip install -e ".[dev]"`), lint/format/test targets |
| `pyproject.toml` | manifest | hatchling; deps fastmcp + mutmut; dev extras ruff/pytest/pre-commit; script `mutmut-mcp = mutmut_mcp:main`; ruff config |
| `uv.lock` | lock | Committed lockfile, `requires-python = ">=3.10"` |
| `Dockerfile` | deploy | Smithery build: python:3.11-slim, `python mutmut_mcp.py` |
| `smithery.yaml` | deploy | stdio start command, empty config schema |
| `.pre-commit-config.yaml` | tooling | pre-commit-hooks v5.0.0 + ruff-pre-commit v0.9.7 |
| `README.md` | docs | Install (uvx / uv / Smithery), MCP client config, tool API reference |
| `LICENSE` | docs | MIT |
