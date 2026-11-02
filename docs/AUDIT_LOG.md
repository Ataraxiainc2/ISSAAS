# Audit Log

ISSA AS is built by **Meta Muse Code** (`muse exec`). A human-supervised administrator agent writes the prompts
(kept verbatim in [`prompts/`](../prompts)), reviews every result, runs the tests, and records here what was
accepted, what was corrected, and why.

| # | Date (ET) | Prompt | Model / effort | Cost (USD) | Verdict | Corrections requested |
|---|-----------|--------|----------------|------------|---------|-----------------------|
| 01 | 2026-11-02 | [01-architecture](../prompts/01-architecture.md) | muse-spark-1.3-contributor / high | ~$0.01 (552k in, 478k cached, 6.3k out, 15 calls) | Accepted with corrections | See entry 01 |
| 02 | 2026-11-02 | [02-foundation](../prompts/02-foundation.md) | muse-spark-1.3-contributor / high | ~$0.03 (3.98M in, 3.80M cached, 34.7k out, 60 calls) | Accepted with corrections | See entry 02 |
| 03 | 2026-11-02 | [03-verifier](../prompts/03-verifier.md) | muse-spark-1.3-contributor / high | ~$0.03 (4.24M in, 4.11M cached, 41.3k out, 22.4k reasoning, 47 calls) | Accepted with minor follow-ups | See entry 03 |
| 04 | 2026-11-02 | [04-vertical-slice](../prompts/04-vertical-slice.md) | muse-spark-1.3-contributor / high | ~$0.03 (3.48M in, 3.36M cached, 44.4k out, 21.5k reasoning, 43 calls) | Accepted with corrections | See entry 04 |
| 05 | 2026-11-02 | [05-solver-fixes](../prompts/05-solver-fixes.md) | muse-spark-1.3-contributor / high | ~$0.02 (3.62M in, 3.50M cached, 31.3k out, 17.4k reasoning, 44 calls) | Accepted | See entry 05 |
| 06 | 2026-11-02 | Live run #1: `issa solve` psobot/keynote-parser#91 → [06-live-run-fixes](../prompts/06-live-run-fixes.md) | (no model call) | $0.00 | Failed (infrastructure) — fixes sent | See entry 06 |
| 06b | 2026-11-02 | [06-live-run-fixes](../prompts/06-live-run-fixes.md) | muse-spark-1.3-contributor / high | ~$0.03 (3.94M in, 3.82M cached, 32.7k out, 46 calls) | Accepted | See entry 07 |
| 07 | 2026-11-02 | [07-strict-schema](../prompts/07-strict-schema.md) + live runs #2–#3 | contributor / medium (prompt); contributor / medium (solver) | $0.009 (prompt) + $0.013 (solver run #3) | Accepted; fix verified by hand | See entry 07 |
| 08 | 2026-11-02 | [08-verifier-real-repos](../prompts/08-verifier-real-repos.md) | muse-spark-1.3-contributor / high | ~$0.03 (4.51M in, 4.34M cached, 35.1k out, 48 calls) | Accepted; reverify exposed a bug | See entry 08 |
| 09 | 2026-11-02 | [09-double-path](../prompts/09-double-path.md) | muse-spark-1.3-contributor / medium | ~$0.02 (2.21M in, 2.09M cached, 24.3k out, 30 calls) | Accepted; committed by administrator after verification | See entry 08 |
| 10 | 2026-11-02 | [10-scout](../prompts/10-scout.md) | muse-spark-1.3-contributor / high | ~$0.02 (2.26M in, 2.19M cached, 22.3k out, 37 calls) | Accepted | See entry 09 |

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

### 04 — Milestone 2 vertical slice (`issa solve`)
**Independently verified by the administrator:** `uv run pytest` → 53 passed; `uv run ruff check` → clean. One local commit
(`c0838fa`), no push. New: `workspace.py` (read-only issue fetch, shallow clone and venv under `workspaces/`),
`solver.py` (one `muse exec` session: failing-test commit, then fix commit), `pipeline.py` (`proof/<run_id>/` bundle or
`abstention.md`), offline end-to-end test with stubbed GitHub and Muse. Audit-03 follow-up done (unknown price → `null`).

**Muse corrected the administrator's prompt (accepted):** the prompt said to verify with base = parent of the test
commit, which can only ever yield `NO_TEST`. Muse verifies with base = the test commit plus the fix diff (the only real
Fail-to-Pass gate) and records the parent as `test_parent`. Good catch, disclosed clearly.

**Corrections requested (prompt 05):**
1. *Model and effort never reach Muse.* `build_command()` passes neither `--model` nor `--reasoning-effort`; effort only
   changes prompt wording and the step cap, and cost is priced as `muse-spark-1.3` while Muse runs its default model.
   This breaks the model-routing claim. Pass both flags and take the model name for pricing from Muse's `run.usage` event.
2. *Headless approvals.* Without `--approval-mode never`, tool calls that need approval can be auto-rejected in headless
   mode. Add it (the OS sandbox stays on). Keep **not** passing `--trust-workspace`, so a target repo's own agent rules
   cannot steer the solver (prompt-injection defense); document that choice.
3. *Dependency install runs third-party build code outside the Muse sandbox* (`uv pip install -e .`). Mitigations:
   install only from the reputation-filtered repos, sanitized env, timeouts, and record the install log in the proof bundle.
   Documented as a known risk in ARCHITECTURE.md.

**Environment note:** the Muse Windows sandbox creates empty marker folders (`.agents`, `.eden`, `.hg`, `.muse`, `.sl`)
in the workspace root at run start. They are empty, not tracked by git, and recreated on each run; left in place.

### 05 — Audit-04 corrections to the solver
**Independently verified by the administrator:** `uv run pytest` → 58 passed; `uv run ruff check` → clean. One local
commit (`94781a8`), no push. `build_command()` now passes `--model`, `--reasoning-effort` and `--approval-mode never`
and never `--trust-workspace` (rationale in code and ARCHITECTURE.md §6); pricing uses the model from Muse's `run.usage`
event; installs use the sanitized env and timeouts; `install.log` is in the proof bundle; exact command line is tested.

**Muse found and fixed a latent bug on its own:** the solver parsed `muse exec --json` stdout with a single `json.loads`,
which fails on real multi-line JSONL — the slice would have crashed on its first live run. Now parsed event by event.

**Open items:**
1. ARCHITECTURE.md §3 still shows the old `muse exec` command block (flagged by Muse). Fold into the next prompt.
2. The slice has only run against stubs. Next step: one live `issa solve` on a real, small issue (needs the user's OK:
   it clones a third-party repo into `workspaces/`, installs its dependencies and spends Meta credits).

### 06 — First live run: psobot/keynote-parser#91 (zip-slip)
Candidate chosen by the administrator (security-first, B7): public zip-slip / path-traversal report in a small, active
pure-Python repo (213 stars, pytest, no existing fix PR). Command:
`issa solve https://github.com/psobot/keynote-parser/issues/91 --model muse-spark-1.3-contributor --effort medium`.

**What worked:** read-only issue fetch; shallow clone into `workspaces/<run_id>/`; isolated venv; 15 dependencies plus the
package installed inside the workspace (sanitized env); hash-chained trace; proof folder with `issue.json`, `install.log`,
`cost.json`; nothing sent to GitHub.

**What failed:** `muse exec failed to start: [WinError 2]`. On Windows `muse` is a `muse.cmd` shim that `subprocess`
cannot start by bare name. The offline tests stubbed the binary, so they could not catch this.

**Findings (sent in prompt 06):**
1. Resolve the Muse binary (`MUSE_BIN` override, else `shutil.which`), pass the prompt with `--prompt-file`.
2. The failure was recorded as `ABSTAIN`. Infrastructure failures must be `ERROR`; `ABSTAIN` is only an honest model
   decline — otherwise the abstention-accuracy benchmark is polluted.
3. `run_started.caps` showed default caps (10 steps, $0.60), not the caps actually used. Clone/install were not traced.
4. Product runs were invisible to the administrator's console; the solver will stream its Muse log into `muse-runs/`.

### 07 — Prompts 06–07 and live runs #2–#3 on psobot/keynote-parser#91
**Prompt 06 (accepted, 67 tests):** Muse binary resolution (`MUSE_BIN`/`shutil.which`), `--prompt-file`, `ERROR` vs
`ABSTAIN`, real caps and `workspace_ready` in the trace, solver log streamed to `muse-runs/solver-<run_id>.*`.

**Live run #2 → ERROR (correctly classified):** Muse started; the Meta API rejected the request:
`'additionalProperties' is required to be supplied and to be false`. The `--output-schema` must follow strict
structured-output rules. **Prompt 07 (accepted, 69 tests):** strict schema, nullable optionals, schema-rules test.

**Live run #3 → ERROR, but the fix is real.** Muse (contributor model, medium, 24 calls, **$0.013**) found the root cause
(CWE-22: `dir_file_sink.accept()` joined member names verbatim), committed a regression test (`3886fd5`) and then a fix
(`b8f16ac`, new `safe_target_path()`: normalizes separators, strips drive letters and `..`, confines with `realpath`).
The verifier could not start `.venv/Scripts/python.exe` (relative executable on Windows).

**Manual verification by the administrator:** new test **fails** on the test commit and **passes** on the fix commit;
`test_file_utils.py` (2 failures) and 3 collection errors (missing generated `protobuf_schemas.tar.xz`) fail identically on
the original commit, so they are pre-existing, not regressions. Equivalent verdict: **VERIFIED**.

**Findings (prompt 08):** resolve relative executables against the repo; run Fail-to-Pass on the new test file only;
count regressions relative to a baseline run of the original commit; add `issa reverify <run_id>` so a run can be
re-verified without paying for another Muse session.

### 08 — Verifier on real repos, `issa reverify`, first automatic VERIFIED
**Prompt 08 (accepted, 77 tests):** relative executables resolved, Fail-to-Pass on the new test file only, regressions
counted against a baseline run of the original commit (`--junitxml`), `baseline_failures` in `verdict.json`,
`issa reverify <run_id>` (no Muse call). First `reverify` of live run #3 returned `ERROR`: the suite interpreter path was
anchored twice (`…\keynote-parser\workspaces\…\keynote-parser\.venv\…`).

**Prompt 09:** absolute interpreter paths; `_resolve_cmd` no longer re-anchors paths that already resolve inside the repo;
nested-workspace test; slow end-to-end tests marked (`-m "not slow"` runs in ~30 s). Muse hit its 30-step cap before
running tests or committing and **said so explicitly**, listing the remaining commands. The administrator ran them:
fast suite 74 passed, full suite 78 passed, ruff clean; committed the change unmodified (author of the code: Muse).
Lesson: give fix prompts ≥40 steps when they must also run a 45 s suite.

**Result — first automatic VERIFIED (no extra model cost):**
`issa reverify 20261102-201027-psobot-keynote-parser-91` → `VERIFIED`, `f2p_pass: true`, `regressions: 0`,
`baseline_failures: 3` (pre-existing). Matches the administrator's manual check in entry 07. `issa verify` → chain ok.
Total model cost of the fix: **$0.013** (contributor model). Proof bundle and trace are committed under `proof/` and
`traces/` (also the two failed runs, kept as honest evidence).

**Housekeeping:** clones of failed runs #1–#2 removed from `workspaces/` (~150 MB). A `pytest-of-24/` temp folder created
inside the Muse sandbox is not deletable by the user account (sandbox ACL); git-ignored. Next prompt: pin pytest's
`--basetemp` under `.cache/` so it does not recur.

### 09 — Milestone 3a: security-first scout
**Independently verified by the administrator:** fast suite 99 passed (`-m "not slow"`), ruff clean. Commit `e52af3b`.
`scout.py`: read-only GitHub search with 11 vulnerability classes first (B7), reputation filter, bot-alert filter,
AI-refusal and linked-PR filters (B5), ecosystem detection `python` / `node` / `none` (B8), cache under `.cache/scout/`.
`signals.py`: 9 named, weighted deterministic signals with `explain()` (B4). `issa scout` writes a report and trace.

**Environment finding (not a code defect):** files Muse creates inside its OS sandbox are owned by the sandbox account.
`.cache/pytest` (Muse's pinned `--basetemp`) and `.pytest_cache` cannot be cleared by the user account, so the
administrator's first test run showed 47 setup errors. Re-run with `--basetemp .cache/pytest-admin` → 99 passed.
Administrator test runs now always pass their own `--basetemp`; both folders are git-ignored.

**Housekeeping (user-authorized cleanup):** removed `.ruff_cache`, the administrator's temp folder and the empty sandbox
marker folders (`.agents`, `.eden`, `.hg`, `.muse`, `.sl`; recreated by Muse on its next run). Kept: `.venv`, `.tools`
(uv), `workspaces/` run #3 (needed by `issa reverify`), `muse-runs/` (console history), `proof/`, `traces/`.

**Secrets:** the user provided a Meta Model API key; the administrator copied it from the user's key file into the
git-ignored `.env` without displaying it. The code does not load `.env` yet (next prompt). Per the user, no Meta API
calls are made until a step really needs them.
