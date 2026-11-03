# Prompt 21 — keep the eval inside `workspaces/`, honest summary, timings

Read `AGENTS.md` (rules 1–3). Scope: `src/issa_as/evals.py`, `src/issa_as/cli.py`, `tests/test_evals.py`,
`evals/results/`. Do not touch `verifier.py`, README or docs. No network in tests. Stay on branch `feat/verifier-eval`.

1. **Workspace only.** `run_verifier_eval` must not use the system temp folder. Create its scratch root under
   `workspaces/eval-<label>-<timestamp>/` (a `workspace_root` parameter, default `workspaces`, so tests can pass
   `tmp_path`), and remove it when done unless `--keep` is given.
2. **Bundle mode runs the target in its own venv.** `materialize_bundle_target` must set up the cloned repo's
   environment the same way `bundle.verify_bundle` does (reuse that code, `workspaces/<run>/.venv`), never the ISSA AS
   interpreter. Third-party test code must never run with our `.venv`.
3. **Honest summary.** Each record gets a `role`: `control` (the real fix, expected VERIFIED), `attack` (everything
   expected to be rejected), or `known_limit` (overfit). The report gets `attacks_rejected`, `attacks_total`,
   `controls_ok`, `known_limits`, and the summary line becomes, for example,
   `7/7 attacks rejected · real fix accepted · 1 known limit (overfitting)`. Exit 1 if any record has `ok: false`.
4. **Timings.** Each record gets `seconds` (wall time of its `verify()` call, 2 decimals); the report gets
   `median_seconds` and `total_seconds`.
5. **Tests** for 1–4 (the workspace root is honoured and cleaned, roles and summary text, timing fields present).

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`, `--no-sync` if locked). Re-run
`issa eval verifier` once and commit the regenerated `evals/results/verifier-synthetic.json`. Commit locally once, do
not push. Be concise in the final summary.
