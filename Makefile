.PHONY: install lint lint-check format format-check typecheck coverage mutate clean

VENV_DIR = .venv
UV = uv

install:
	$(UV) venv $(VENV_DIR) --seed
	$(UV) pip install -e ".[dev]"

lint: install
	$(UV) run ruff check --fix .

lint-check: install
	$(UV) run ruff check .

format: install
	$(UV) run ruff format .

format-check: install
	$(UV) run ruff format --check .

typecheck: install
	$(UV) run mypy .

test: install
	$(UV) run pytest tests/

# Measured floor: the suite covers the server module 100% (the __main__ guard
# is excluded in [tool.coverage.report]). Raise the floor, never lower it.
coverage: install
	$(UV) run pytest tests/ --cov=mutmut_mcp --cov-report=term --cov-fail-under=100

# The mutation-testing server mutation-tests itself. Survivors within the
# waivered budget (docs/mutation-waivers.md) pass; the gate fails on anything new.
mutate: install
	$(UV) run mutmut run || true
	$(UV) run python scripts/mutation_budget.py --max-undetected 6

clean:
	rm -rf $(VENV_DIR) mutants .coverage
	find . -type f -name '*.py[co]' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
