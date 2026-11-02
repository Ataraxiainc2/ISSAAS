# Audit Log

ISSA AS is built by **Meta Muse Code** (`muse exec`). A human-supervised administrator agent writes the prompts
(kept verbatim in [`prompts/`](../prompts)), reviews every result, runs the tests, and records here what was
accepted, what was corrected, and why.

| # | Date (ET) | Prompt | Model / effort | Cost (USD) | Verdict | Corrections requested |
|---|-----------|--------|----------------|------------|---------|-----------------------|
| 01 | 2026-11-02 | [01-architecture](../prompts/01-architecture.md) | muse-spark-1.3-contributor / high | ~$0.01 (552k in, 478k cached, 6.3k out, 15 calls) | Accepted with corrections | See entry 01 |
| 02 | 2026-11-02 | [02-foundation](../prompts/02-foundation.md) | muse-spark-1.3-contributor / high | ~$0.03 (3.98M in, 3.80M cached, 34.7k out, 60 calls) | Accepted with corrections | See entry 02 |
| 03 | 2026-11-02 | [03-verifier](../prompts/03-verifier.md) | muse-spark-1.3-contributor / high | ~$0.03 (4.24M in, 4.11M cached, 41.3k out, 22.4k reasoning, 47 calls) | Accepted with minor follow-ups | See entry 03 |

## Entries

### 01 — Architecture
**Accepted:** clear pipeline, deterministic verifier/budget/select, pluggable Spark Open provider with logged fallback,
trace schema, honest "one live fix + replay" demo promise. Stayed inside the workspace; committed locally, no push.

**Corrections requested (sent in prompt 02):**
1. *Untrusted code execution.* Running a third-party repo's test suite executes code we have not reviewed on the host PC.
   Add: reputation filter at scout time (stars, not archived, recent activity), sanitized environment (no secrets in env
   for test subprocesses), cwd and temp dirs pinned under `workspaces/<run>/`, hard timeouts, and run test commands through
   the Muse OS sandbox where possible.
2. *Runtime `muse exec` flags were unspecified.* The fixer must run with `--workspace workspaces/<run>/<repo>`,
   sandbox on, `--disable-web-tools`, `--max-model-steps`, worktree created inside `workspaces/`.
3. *Build order.* 31 h of breadth-first modules leaves no slack for a solo builder. Build a thin end-to-end vertical slice
   first (one known issue → failing test → fix → F2P verdict → trace → viewer), then widen.
4. *Ablation cost.* Restrict the 3-arm ablation to triage/critic (cheap); the fixer runs on one model plus one effort comparison.
5. Merge `reproduce` + `fixer` into one Muse session with two checkpoints (failing test committed first, then fix), so the
   agent keeps its repo context; the verifier still re-checks both independently.

### 02 — Architecture corrections + Milestone 1 foundation
**Independently verified by the administrator:** `uv run pytest` → 26 passed; `uv run ruff check` → clean;
working tree clean; two local commits (`a0def47`, `5ebf26d`), no push. All five entry-01 corrections are present in ARCHITECTURE.md.

**Findings:**
1. *Self-downloaded tool (process issue).* `uv` was not on PATH, so Muse fetched `uv.exe` into `.tools/` via Python.
   Checked: it is the official `astral-sh/uv` GitHub release (v0.13.6), Authenticode signature **Valid**, git-ignored,
   and the user-level uv cache was not touched. Outcome acceptable, but downloading executables without asking is not.
   **Rule added to AGENTS.md:** no downloads of executables or installers; stop and report instead.
2. *Wrong cached-input prices.* `budget.py` uses placeholder cached rates ($0.25 and $0.02 per 1M). Meta publishes them:
   `muse-spark-1.3` $0.15, `muse-spark-1.3-contributor` $0.002. Fix in the next prompt.
3. *Hosted Spark Open logs `cost_usd = 0.0`.* That would understate cost in the benchmark. Make the price configurable
   (`SPARK_OPEN_PRICE_IN_PER_1M`, `SPARK_OPEN_PRICE_OUT_PER_1M`) and log a real estimate.
4. *Cost efficiency.* 60 model calls and ~4M input tokens for one milestone (still only ~$0.03 on the contributor model).
   Future prompts will be narrower, one module each, with lower step caps.

**Policy added (user decision):** nothing is ever sent to a third-party repository without explicit human confirmation,
and during the testing phase upstream publishing is not automated at all (AGENTS.md rule 7).

**Backlog:** ideas reviewed from an earlier prototype concept were recorded in [BACKLOG.md](BACKLOG.md) (concepts only; no code reused).

### 03 — Audit-02 fixes + deterministic verifier + hash-chained trace
**Independently verified by the administrator:** `uv run pytest` → 42 passed; `uv run ruff check src tests` → clean.
One local commit (`09a8e0b`), no push. All audit-02 corrections applied (published cached rates; Spark Open price from env).

**Accepted:** `verifier.py` is plain Python and returns `VERIFIED` / `UNPROVEN` / `NO_TEST` / `REGRESSION` / `ERROR`;
it refuses repos outside `workspaces/` and dirty trees, strips secret-like env vars, pins `TMP`/`TEMP`, times out every
command and restores the original commit. Traces are now hash-chained and `issa verify <run>` reports the first bad line
(tamper test included). Muse honestly disclosed an out-of-scope side effect (see 2).

**Follow-ups:**
1. When `SPARK_OPEN_PRICE_*` are unset, cost is logged as `0.0`. Log `cost_usd: null` plus `price_known: false` instead,
   so the benchmark never shows a free call. (Next prompt.)
2. *Process (administrator side):* Muse's commit swept in a deletion the administrator had staged while Muse was running.
   Harmless, but from now on the administrator does not stage or commit while a Muse run is active.

**Tooling added by the administrator (not part of the product):** `tools/console/` — a read-only local web console
(`python tools/console/server.py`, http://127.0.0.1:8765) that shows each Muse run's prompt, settings, replies, tool
steps and token cost, and `run-muse.ps1`, which records the prompt and settings of every run. Muse's reasoning text is
stored encrypted by Muse and is not readable; only reasoning token counts are shown.
