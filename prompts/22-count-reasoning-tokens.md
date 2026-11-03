# Prompt 22 — count Muse reasoning tokens in the budget

Read `AGENTS.md` (rule 9). Scope: `src/issa_as/solver.py`, `src/issa_as/budget.py`, `tools/console/server.py`,
`src/issa_as/serve.py`, tests.
Do not touch README, docs or committed `proof/` files. No network in tests. Stay on the current branch.

Problem: Muse Code's `run.usage` and `model_completed` payloads report `output_tokens` and `reasoning_tokens` as
disjoint counts, and the Meta Model API bills both at the output rate. The ledger reads only `output_tokens`, so every
logged Muse cost is too low (the administrator reconciled this against the dev.meta.ai Usage page on 2026-11-03).

1. Wherever Muse usage is parsed (`solver.py` around lines 327 and 463, `tools/console/server.py`), read
   `reasoning_tokens` too (default 0), keep it as its own field, and bill `output_tokens + reasoning_tokens` at the
   output price in `budget.py`. Keep old records without the field working (they count as 0).
2. Trace and cost records gain `reasoning_tokens`; the human summary shows it next to output tokens.
3. Muse may start sub-agent sessions whose usage is not in the parent's `run.usage` (`children_included: false`).
   When that flag is false, add `"children_included": false` to the cost record so the ledger says it is a lower
   bound. Do not read anything outside this repository to find those sessions.
4. Small panel fix (`src/issa_as/serve.py`, `web/app.js` only if needed): runs whose id starts with `video-` get kind
   `video` and the label `VIDEO` from `_run_kind`, instead of being shown as a solve run.
5. Tests: reasoning tokens billed at the output rate; old records without the field; the flag carried through; the
   `video-` run kind.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`, `--no-sync` if locked). Commit
locally once, do not push. Be concise in the final summary.
