# Prompt 02 — Architecture corrections + foundation milestone

Read `AGENTS.md`, `docs/ARCHITECTURE.md` and entry 01 in `docs/AUDIT_LOG.md` (the administrator's review).

## Part A — update `docs/ARCHITECTURE.md` (keep it concise)
Apply the five corrections from audit entry 01:
1. Untrusted-code safety section: reputation filter at scout time, sanitized env for test subprocesses (no secrets),
   cwd/TMP/TEMP pinned under `workspaces/<run>/`, hard timeouts, OS sandbox where possible.
2. Exact runtime `muse exec` flags for the fixer (workspace = the cloned repo under `workspaces/`, sandbox on,
   `--disable-web-tools`, `--max-model-steps`, `--output-schema`, `--json`, worktree inside `workspaces/`).
3. Replace the build plan with: **Milestone 1 foundation → Milestone 2 vertical slice on ONE known issue end-to-end →
   Milestone 3 scout/triage/critic breadth → Milestone 4 benchmark + ablation → Milestone 5 viewer polish + README**.
4. Ablation covers triage/critic arms only; fixer = one model + one effort comparison.
5. `reproduce` + `fixer` become one Muse session with two checkpoints (failing test first, then fix); verifier re-checks both.

## Part B — implement Milestone 1 (foundation)
- `pyproject.toml` (package `issa_as` under `src/`, Python >=3.13, deps: `httpx`, `pydantic`; dev: `pytest`, `ruff`), set up with `uv`.
- `traces.py`: typed events (pydantic) for the schema in ARCHITECTURE.md, `append`/`load`, monotonic `seq`, UTC `ts`.
- `budget.py`: price table for `muse-spark-1.3` and `muse-spark-1.3-contributor` (incl. cached input), `Ledger`,
  per-issue `IssueCaps` (steps, seconds, USD), `check_and_charge` that raises before an over-cap call and emits `cap_blocked`.
- `providers.py`: OpenAI-compatible chat client for Meta Model API (`https://api.meta.ai/v1`, env `MODEL_API_KEY`) and an
  optional hosted Spark Open provider (env `SPARK_OPEN_BASE_URL`, `SPARK_OPEN_API_KEY`, model `meta-models/muse-spark-open-1026`);
  automatic logged fallback; every call emits `model_call` with tokens and USD. Include a `FakeProvider` for tests.
- `cli.py`: `issa --help` with stub subcommands `run`, `replay`, `publish` (publish = dry-run only).
- `.env.example` listing every env var (no values).
- Tests for traces, budget and providers (using `FakeProvider`; **no real API calls in tests**). `uv run pytest` and `uv run ruff check` must pass.

Commit locally when Part A and Part B are done and tests pass. Do not push.
End with: files changed, test results, and anything you could not do.
