# Mutmut MCP

A Model Context Protocol (MCP) server for managing mutation testing with [mutmut](https://mutmut.readthedocs.io/). This tool provides a set of programmatic APIs for running mutation tests, analyzing results, and improving test coverage in Python projects.

## Features

- Run mutation testing sessions on any Python module or package
- Show overall mutation testing results and surviving mutations
- Suggest areas needing better test coverage
- Rerun mutmut on specific survivors or all survivors
- Clean mutmut cache
- Designed for automation and integration with other MCP tools

## Installation

1. **Clone the repository:**
   ```sh
   git clone https://github.com/wdm0006/mutmut-mcp.git
   cd mutmut-mcp
   ```
2. **Install dependencies:**
   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -U pip
   pip install mcp[cli] mutmut
   ```

## Usage

You can run the MCP server directly:

```sh
python mutmut_mcp.py
```

Or use with [uv](https://github.com/astral-sh/uv):

```sh
uv run --with mcp --with mutmut mutmut_mcp.py
```

### Example: Run Mutation Testing

```python
from mcp.client import MCPClient
client = MCPClient("http://localhost:8000")
result = client.call("run_mutmut", target="your_module")
print(result)
```

### Example: Show Results

```python
result = client.call("show_results")
print(result)
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
