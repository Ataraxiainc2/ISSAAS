# Prompt 13 — `issa run` (the autonomous loop), `issa replay`, `repro_result`

Read `AGENTS.md` and audit entry 12 in `docs/AUDIT_LOG.md`. Scope: `cli.py`, `pipeline.py`, a new `run.py` if useful,
`traces.py` (only if an event needs a field), tests. No real network or model calls in tests.

1. **`issa run [--languages python] [--limit 30] [--max-usd 0.50] [--dry-run]`** — one run id, one hash-chained trace:
   scout → triage → critic → select → solve (using the selection's `recommended_solver_model` / `recommended_effort`)
   → verify → proof bundle or abstention. A run-level USD cap (`--max-usd`) is checked before every paid stage and emits
   `cap_blocked` when it would be exceeded. `--dry-run` stops before the first paid call and prints the planned stages
   with estimated tokens/USD. The selection event must carry the human-readable reasons (why this issue, why this model).
2. **`issa replay <run_id>`** — Keyless Trace Replay: verify the hash chain first (refuse to replay a broken chain), then
   print a readable timeline: time, stage, event type, key fields (issue, score, model, effort, verdict, cost) and the
   first 300 chars of any `reasoning_excerpt`. No network, no API keys.
3. **`repro_result` event** — emitted after the solver's test checkpoint: test file, test commit, whether the test failed on
   the base commit and the failure reason (the event type already exists in `traces.py`).
4. Tests: `issa run` end to end with fake GitHub, fake providers and a stubbed `muse exec`; run-level cap blocking;
   dry-run makes zero paid calls; replay of a valid trace; replay refusing a tampered trace; `repro_result` emitted.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
