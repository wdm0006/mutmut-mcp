"""Keep the PEP 723 inline script block in sync with pyproject.toml.

`mutmut_mcp.py` declares its dependencies twice: once in `[project]` of pyproject.toml
(used by `pip install .`, `uvx`, and the Dockerfile) and once in the PEP 723 `# /// script`
header (used by the README's `uv run mutmut_mcp.py`). Nothing in CI installs the second one,
so a bump applied only to pyproject leaves the standalone entry point silently on the old
release — which is how it ended up serving fastmcp 2.x, where FastMCP awaits a sync tool
inline on the event loop and a long `mutmut run` blocks every other request.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_SOURCE = (REPO_ROOT / "mutmut_mcp.py").read_text()
PYPROJECT_SOURCE = (REPO_ROOT / "pyproject.toml").read_text()

# The reference regex from PEP 723 for locating an inline metadata block.
PEP723_BLOCK = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")

REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*(?P<specifier>.*)$")


def _inline_metadata() -> str:
    """Return the decommented body of `mutmut_mcp.py`'s PEP 723 script block."""
    match = next(m for m in PEP723_BLOCK.finditer(MODULE_SOURCE) if m.group("type") == "script")
    return "".join(line[2:] if line.startswith("# ") else line[1:] for line in match.group("content").splitlines(True))


def _dependencies(toml_text: str) -> dict:
    """Map requirement name -> version specifier from the first `dependencies = [...]` array."""
    array = re.search(r"^dependencies\s*=\s*\[(.*?)\]", toml_text, re.MULTILINE | re.DOTALL)
    assert array, "no dependencies array found"
    found = {}
    for requirement in re.findall(r"[\"']([^\"']+)[\"']", array.group(1)):
        parsed = REQUIREMENT.match(requirement.strip())
        found[parsed.group("name").lower()] = parsed.group("specifier").replace(" ", "")
    return found


def test_inline_block_and_pyproject_agree_on_shared_dependencies():
    inline = _dependencies(_inline_metadata())
    packaged = _dependencies(PYPROJECT_SOURCE)
    shared = set(inline) & set(packaged)
    assert "fastmcp" in shared and "mutmut" in shared
    mismatched = {name: (inline[name], packaged[name]) for name in shared if inline[name] != packaged[name]}
    assert not mismatched, f"inline script block and pyproject.toml disagree: {mismatched}"


def test_inline_block_requires_fastmcp_3():
    """FastMCP 3 is what runs a sync tool off the event loop; 2.x blocks the whole server."""
    specifier = _dependencies(_inline_metadata())["fastmcp"]
    assert specifier.startswith(">=3."), specifier


def test_inline_block_and_pyproject_agree_on_requires_python():
    inline = re.search(r'^requires-python\s*=\s*"([^"]+)"', _inline_metadata(), re.MULTILINE)
    packaged = re.search(r'^requires-python\s*=\s*"([^"]+)"', PYPROJECT_SOURCE, re.MULTILINE)
    assert inline and packaged
    assert inline.group(1) == packaged.group(1)
