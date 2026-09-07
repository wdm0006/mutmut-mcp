"""Unit tests for the CI survival-budget gate (scripts/mutation_budget.py)."""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mutation_budget.py"
_spec = importlib.util.spec_from_file_location("mutation_budget", _SCRIPT)
assert _spec is not None and _spec.loader is not None, f"could not load {_SCRIPT}"
mutation_budget = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutation_budget)


def _results(*pairs: tuple[str, str]) -> list[tuple[str, str]]:
    return list(pairs)


def test_within_budget_passes():
    results = _results(("m1", "killed"), ("m2", "killed"), ("m3", "survived"), ("m4", "timeout"))
    code, message = mutation_budget.evaluate(results, max_undetected=1)
    assert code == mutation_budget.EXIT_OK
    assert "Within budget" in message
    assert "3/4" in message  # timeout counts as detected


def test_budget_exceeded_fails():
    results = _results(("m1", "killed"), ("m2", "survived"), ("m3", "survived"))
    code, message = mutation_budget.evaluate(results, max_undetected=1)
    assert code == mutation_budget.EXIT_BUDGET
    assert "EXCEEDED" in message


def test_no_tests_counts_as_undetected():
    results = _results(("m1", "killed"), ("m2", "no tests"))
    code, _ = mutation_budget.evaluate(results, max_undetected=0)
    assert code == mutation_budget.EXIT_BUDGET


def test_incomplete_run_fails_without_scoring():
    results = _results(("m1", "killed"), ("m2", "not checked"))
    code, message = mutation_budget.evaluate(results, max_undetected=5)
    assert code == mutation_budget.EXIT_BROKEN
    assert "incomplete" in message


def test_suspicious_run_fails_without_scoring():
    results = _results(("m1", "suspicious"))
    code, message = mutation_budget.evaluate(results, max_undetected=5)
    assert code == mutation_budget.EXIT_BROKEN


def test_zero_reported_mutants_fails():
    code, message = mutation_budget.evaluate([], max_undetected=0)
    assert code == mutation_budget.EXIT_BROKEN
    assert "0 mutants" in message
