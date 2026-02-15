# Mutmut MCP
[![smithery badge](https://smithery.ai/badge/@wdm0006/mutmut-mcp)](https://smithery.ai/server/@wdm0006/mutmut-mcp)

A Model Context Protocol (MCP) server for managing mutation testing with [mutmut](https://mutmut.readthedocs.io/). This tool provides a set of programmatic APIs for running mutation tests, analyzing results, and improving test coverage in Python projects.

## Features

- Run mutation testing sessions on any Python module or package
- Show overall mutation testing results and surviving mutations
- Suggest areas needing better test coverage
- Rerun mutmut on specific survivors or all survivors
- Clean mutmut cache
- Designed for automation and integration with other MCP tools

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

- `run_mutmut(target, test_command="pytest", options="", venv_path=None)` – Run mutation testing
- `show_results(venv_path=None)` – Show overall results
- `show_survivors(venv_path=None)` – List surviving mutations
- `generate_test_suggestion(venv_path=None)` – Suggest areas needing better test coverage
- `rerun_mutmut_on_survivor(mutation_id=None, venv_path=None)` – Rerun mutmut on survivors
- `clean_mutmut_cache(venv_path=None)` – Clean mutmut cache

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
