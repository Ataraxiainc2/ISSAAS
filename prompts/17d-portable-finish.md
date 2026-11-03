# Prompt 17d — finish Prompt 17c

Continue `prompts/17c-portable-bundles.md`; its session stopped at the step limit. Already done and uncommitted (keep
it, do not rewrite): `verifier.py` (F2P-first order, F2P fields kept on suite errors, `suite_scope_for`,
`neutral_test_command`, `portable_test_command`, foreign-OS interpreter rebuild) and part of `pipeline.py`.

Remaining:
1. `pipeline.py`: `reverify_run`/`recover_run`/`solve_issue` write `fail_before`, `pass_after`, `f2p_pass`, `suite_scope`
   into `verdict.json`; `reverify` uses the portable rebuild.
2. `bundle.py`: `suite_path` option, portable rebuild of the stored command, `suite_scope` in `verdict.json` and in the
   summary line.
3. `solver.py`: store new test commands interpreter-neutral (`python -m pytest …`).
4. `cli.py`: `--suite-path` on `verify-bundle` and `reverify`.
5. Tests from 17c item 4 (both-direction interpreter rebuild, F2P kept when the suite times out, the `unit` rule,
   `--suite-path` override, `suite_scope` in the summary).

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once (one commit
for 17c + 17d), do not push. Be concise in the final summary.
