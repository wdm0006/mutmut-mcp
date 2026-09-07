# Mutation waivers

The CI survival budget (`scripts/mutation_budget.py`, invoked with
`--max-undetected 6`) allows exactly the six survivors below. Every other
survivor must be killed by a named test; raising the budget in CI requires a
waiver recorded here with the mutant, the mutated code, and why no test can
(or should) distinguish it.

Campaign at time of writing: 210 mutants, 204 detected (97.1%), 6 waived.

## `mutmut_mcp.x__run_command__mutmut_3` — `shell=False` → `shell=None`

```diff
-        result = subprocess.run(command, shell=False, capture_output=True, text=True, cwd=cwd)
+        result = subprocess.run(command, shell=None, capture_output=True, text=True, cwd=cwd)
```

Equivalent: `None` is subprocess's own default for `shell`, so both spellings
take the identical no-shell code path. `_run_command` only ever receives argv
lists, and `shell=True` with a list is a different mutant
(`mutmut_mcp.x__run_command__mutmut_12`), which is killed by
`tests/test_survivor_regressions.py::TestRunCommandSurvivors::
test_arguments_reach_the_binary_without_shell_interpretation`.

## `mutmut_mcp.x__run_command__mutmut_8` — `shell=False` removed

Equivalent: omitting the keyword also selects subprocess's default
(`shell=False`). The security property is the list-argv discipline itself,
pinned by the mutant-12 test above.

## `mutmut_mcp.x__unresolved_note__mutmut_3` / `mutmut_5` — `status_counts.get(STATUS_NOT_CHECKED, 0)` default → `None`

Equivalent: the default is only read when the key is absent, and `None` and
`0` are both falsy — the value is rendered only when truthy and compared only
against `1`. No behavior difference is observable through the function.

## `mutmut_mcp.x__unresolved_note__mutmut_9` / `mutmut_11` — `status_counts.get(STATUS_INTERRUPTED, 0)` default → `None`

Equivalent: same reasoning as the `STATUS_NOT_CHECKED` defaults above.
