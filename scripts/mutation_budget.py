#!/usr/bin/env python3
"""Mutation survival budget gate.

Reads `mutmut results`, counts undetected mutants — status 'survived' (a test
ran but did not detect the mutant) plus 'no tests' (no test exercises the
mutant at all) — and exits non-zero when that count exceeds the budget. This
is the CI survival-rate budget: it fails when the suite's mutation strength
regresses, independent of how many mutants exist.

Parsing reuses the server's own strict `mutmut results` parser
(mutmut_mcp._parse_results): the gate that scores the tool's output is the
same parser the tool exposes.

- timeout counts as detected: a hanging mutant is behavior the suite caught.
- suspicious / not checked / interrupted statuses make the run incomplete;
  the gate fails and asks for a rerun rather than scoring a partial picture.
- zero reported mutants fails too: a broken config must not read as a pass.

Exit codes: 0 within budget, 2 budget exceeded, 3 broken or incomplete run.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mutmut_mcp import (  # noqa: E402
    STATUS_INTERRUPTED,
    STATUS_NO_TESTS,
    STATUS_NOT_CHECKED,
    STATUS_SURVIVED,
    _parse_results,
)

STATUS_TIMEOUT = "timeout"
STATUS_SUSPICIOUS = "suspicious"

# Statuses meaning a mutant went undetected: either a test ran and missed it,
# or no test covers it at all.
UNDETECTED_STATUSES = (STATUS_SURVIVED, STATUS_NO_TESTS)
# Statuses meaning the campaign did not finish, so the numbers are not scoreable.
INCOMPLETE_STATUSES = (STATUS_NOT_CHECKED, STATUS_INTERRUPTED, STATUS_SUSPICIOUS)

EXIT_OK = 0
EXIT_BUDGET = 2
EXIT_BROKEN = 3


def evaluate(results: list[tuple[str, str]], max_undetected: int) -> tuple[int, str]:
    """Score parsed (mutant_name, status) pairs against the budget.

    Returns (exit_code, report_message).
    """
    status_counts: dict[str, int] = {}
    for _, status in results:
        status_counts[status] = status_counts.get(status, 0) + 1

    total = len(results)
    if total == 0:
        return (
            EXIT_BROKEN,
            "Budget check failed: mutmut reported 0 mutants — is [tool.mutmut] configured and was `mutmut run` executed?",
        )

    incomplete = [s for s in INCOMPLETE_STATUSES if status_counts.get(s, 0)]
    if incomplete:
        detail = ", ".join(f"{s}={status_counts[s]}" for s in incomplete)
        return (
            EXIT_BROKEN,
            f"Budget check failed: incomplete campaign run ({detail}) — rerun `mutmut run` before scoring.",
        )

    undetected = sum(status_counts.get(s, 0) for s in UNDETECTED_STATUSES)
    killed = total - undetected
    kill_rate = 100.0 * killed / total
    summary = (
        f"Mutation summary: {killed}/{total} mutants detected ({kill_rate:.1f}%), "
        f"undetected={undetected} (survived={status_counts.get(STATUS_SURVIVED, 0)}, "
        f"no tests={status_counts.get(STATUS_NO_TESTS, 0)}), "
        f"budget={max_undetected}."
    )
    if undetected > max_undetected:
        return EXIT_BUDGET, (
            f"{summary} Budget EXCEEDED: kill the new survivors or, if they are "
            "equivalent/inert mutants, raise the budget in CI with justification."
        )
    return EXIT_OK, f"{summary} Within budget."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--max-undetected", type=int, required=True, help="Maximum allowed undetected mutants (survived + no tests)."
    )
    parser.add_argument(
        "--project", default=".", help="Project directory holding mutmut state (default: current directory)."
    )
    parser.add_argument("--mutmut-bin", default="mutmut", help="mutmut executable to invoke (default: mutmut on PATH).")
    args = parser.parse_args()
    if args.max_undetected < 0:
        parser.error("--max-undetected must be >= 0")

    try:
        proc = subprocess.run(
            [args.mutmut_bin, "results", "--all", "true"],
            shell=False,
            capture_output=True,
            text=True,
            cwd=args.project,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
        print(f"Budget check failed: could not run {args.mutmut_bin!r} in {args.project!r}: {exc}", file=sys.stderr)
        return EXIT_BROKEN
    if not proc.stdout.strip() and proc.returncode != 0:
        print(f"Budget check failed: `mutmut results` produced no output (exit {proc.returncode}).", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        return EXIT_BROKEN

    code, report = evaluate(_parse_results(proc.stdout), args.max_undetected)
    print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
