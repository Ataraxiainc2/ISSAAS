# Prompt 14 — Solver: multi-object answers, cost on every path, a USD cap that bites, `issa recover`

Read `AGENTS.md`. Scope: `solver.py`, `pipeline.py`, `cli.py`, `budget.py`, tests. No network or model calls in tests.

Facts from live run `20261102-223630-ansible-ansible-runner-1548` (muse-spark-1.3, effort high), checked by the administrator:
- Muse finished the job: the workspace repo has a test commit (`313a181`) and a fix commit (`eb78464`). But the
  `run.terminal.completed` text held **two concatenated JSON objects** (`{...all null...}{...real answer...}`), so
  `json.loads` failed, the run became `ERROR` "muse exec returned invalid JSON", and no `verdict.json`/`fix.patch` exist.
- The error path wrote `cost.json` with 0 tokens, although `run.usage` reported 4,648,905 input tokens (4,529,818 cached),
  54,279 output, 59 calls ≈ **$1.06**, above the run's `max_usd` 0.60. The cap was never checked during or after the session.

1. **Answer parsing:** scan the terminal text with `json.JSONDecoder.raw_decode` and use the last object that matches the
   output schema and is not all-null. Still fail clearly when there is none; record how many objects were found.
2. **Cost on every path:** parse `run.usage` and write `cost.json` plus a `model_call` event with tokens and USD for
   success, abstain and error alike.
3. **A USD cap that bites:** derive `--max-model-steps` from the remaining USD and a documented, conservative per-step cost
   per model (this run: ≈ $0.018/step for muse-spark-1.3 at high effort with caching), never above the configured step
   cap. If the streamed log exposes usage during the session, terminate `muse exec` when the cap is crossed. Emit
   `cap_exceeded` whenever the final cost is above `max_usd`.
4. **`issa recover <run_id>`:** rebuild a failed run's result without calling Muse and without running any target-repo
   code: re-parse `muse-runs/solver-<run_id>.jsonl` with the new parser, read the test and fix commits from the workspace
   repo, write what `issa reverify` needs (`verdict.json` with a pending verdict, `fix.patch`, the test file, `cost.json`,
   root cause) and append a `solver_recovered` event to the same hash-chained trace. Refuse when the commits are missing.
5. **Tests:** null-then-real and single-object answers, no valid object, cost recorded on the error path, step derivation,
   `cap_exceeded`, recover on a fixture log plus a temp git repo with two commits, recover refusing without commits.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
