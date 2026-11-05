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
| 10b | 2026-11-02 | Live scout run `scout-20261102-210449` (GitHub API only) | none | $0.00 | Works; quality fixes requested | See entry 10 |
| 11 | 2026-11-02 | [11-triage](../prompts/11-triage.md) | muse-spark-1.3-contributor / high | ~$0.055 | Accepted (161 fast tests); external review found gaps | See entry 11 |
| 11b | 2026-11-02 | Baseten connectivity check (administrator, 2 calls) | Muse Spark Open on Baseten | < $0.001 | Works with `meta-models/muse-spark-open` | See entry 11 |
| 12 | 2026-11-02 | [12-spark-open-live](../prompts/12-spark-open-live.md) | muse-spark-1.3-contributor / high | ~$0.037 (7.79M in, 7.65M cached, 36.6k out, 73 calls) | Accepted (190 fast tests) | See entry 12 |
| 12b | 2026-11-02 | Live Spark Open triage + critic on `scout-20261102-222258` (administrator) | Muse Spark Open (Baseten), effort low | price not configured (17.2k in / 3.9k out tokens) | Works; picked ansible/ansible-runner#1548 | See entry 13 |
| 13 | 2026-11-02 | [13-run-replay](../prompts/13-run-replay.md) | muse-spark-1.3-contributor / high | ~$0.031 (5.01M in, 4.86M cached, 32.1k out, 45 calls) | Accepted (196 fast tests) | See entry 13 |
| 13b | 2026-11-02 | Live `issa solve` of ansible/ansible-runner#1548 (run by the user) | muse-spark-1.3 / high | ~$1.06 (4.65M in, 4.53M cached, 54.3k out, 59 calls) | Muse finished; ISSA AS mis-parsed the answer (ERROR); recovered, verification pending | See entry 14 |
| 14 | 2026-11-02 | [14-solver-recover](../prompts/14-solver-recover.md) | muse-spark-1.3-contributor / high | ~$0.052 (6.20M in, 5.89M cached, 42.6k out, 55 calls) | Accepted (208 fast tests) | See entry 14 |
| 15 | 2026-11-02 | [15-serve](../prompts/15-serve.md) + [15b-serve-finish](../prompts/15b-serve-finish.md) | muse-spark-1.3-contributor / high | ~$0.075 (two sessions; the first hit the 60-step limit) | Accepted (223 fast tests, guards probed live) | See entry 15 |
| 16 | 2026-11-02 | [16-test-env](../prompts/16-test-env.md) + [16b-setup-noninteractive](../prompts/16b-setup-noninteractive.md) | muse-spark-1.3-contributor / high | ~$0.059 | Accepted (243 fast tests) | See entry 16 |
| 16c | 2026-11-02 | `issa reverify --setup` on ansible-runner#1548 (run by the user) | none | $0.00 | Honest ERROR: ansible-runner imports `fcntl`, cannot run on Windows | See entry 16 |
| 17 | 2026-11-02/03 | [17](../prompts/17-verify-bundle.md), [17b](../prompts/17b-bundle-timeouts.md), [17c](../prompts/17c-portable-bundles.md), [17d](../prompts/17d-portable-finish.md), [17e](../prompts/17e-plugins-and-hangs.md) | muse-spark-1.3-contributor / high | ~$0.16 (five sessions; 17c hit the step limit) | Accepted (276 fast tests); both bundles VERIFIED on Linux CI | See entry 17 |
| 18 | 2026-11-03 | [18-security-policy](../prompts/18-security-policy.md) (judging pass) | muse-spark-1.3-contributor / high | ~$0.030 | Accepted (292 fast tests) | See entry 18 |
| 19 | 2026-11-03 | Demo video (administrator tool, no Muse Code session) | muse-spark-1.3, muse-wonder-1.0 (+ Gemini TTS) | ~$0.72 Meta + $0.23 Google | Three videos built | See entry 19 |
| 19p | 2026-11-03 | [19-panel-linux-baseten](../prompts/19-panel-linux-baseten.md) | muse-spark-1.3-contributor / high | ~$0.025 (3.68M in, 3.56M cached, 20.9k out + 9.6k reasoning, 46 calls) | Accepted | — |
| 20 | 2026-11-03 | [20-verifier-attacks-eval](../prompts/20-verifier-attacks-eval.md) | muse-spark-1.3-contributor / high | ~$0.092 (14.30M in, 13.97M cached, 97.7k out + 55.8k reasoning, 93 calls) | Accepted after audit; follow-up Prompt 21 | See entry 20 |
| 21 | 2026-11-03 | [21-eval-workspace-honest-summary](../prompts/21-eval-workspace-honest-summary.md) | muse-spark-1.3-contributor / high | ~$0.036 (5.00M in, 4.87M cached, 42.2k out + 24.2k reasoning, 54 calls) | Accepted | See entry 20 |
| 22 | 2026-11-03 | [22-count-reasoning-tokens](../prompts/22-count-reasoning-tokens.md) | muse-spark-1.3-contributor / high | ~$0.069 (10.45M in, 10.22M cached, 75.6k out + 51.9k reasoning, 79 calls) | Accepted | See entry 20 |
| 23 | 2026-11-03 | [23-panel-redesign](../prompts/23-panel-redesign.md) | muse-spark-1.3-contributor / high | ~$0.023 (3.22M in, 3.11M cached, 25.8k out + 4.4k reasoning, 39 calls) | Accepted (341 fast tests); evidence bug sent back as Prompt 24 | See entry 21 |
| 24 | 2026-11-03 | [24-panel-evidence-polish](../prompts/24-panel-evidence-polish.md) | muse-spark-1.3-contributor / high | ~$0.043 (7.50M in, 7.35M cached, 44.7k out + 24.3k reasoning, 67 calls) | Accepted (347 fast tests) | See entry 21 |
| 19v2 | 2026-11-03 | Demo video v2 (administrator tool; live recordings) | muse-spark-1.3, muse-wonder-1.0 (+ Gemini TTS) | ~$0.83 Meta + $0.16 Google | Three videos rebuilt at 1440p | See entries 20–21 |

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

### 10 — First live scout run (`issa scout --limit 30 --languages python,node`, no model calls)
Report: `scout/scout-20261102-210449.json`. 30 candidates, **27 security-tagged** (path traversal, SQL/command
injection, open redirect, ReDoS, SSRF, auth bypass, deserialization). Our solved issue psobot/keynote-parser#91 scored
highest (0.85). Promising next targets: thombashi/DateTimeRange#63 (ReDoS, Python), BearStudio/start-ui-web#774
(open redirect, Node).

**Findings (prompt 11):**
1. `--languages` did not restrict the queries: 22/30 candidates were `runner: none` (Java, C, Lua…), wasted slots.
2. Bot/scanner alerts still leak through (`[vuln-sentinel] … npm minimatch: 9 known vulnerabilities`, `NEMO-009 …`).
3. Feature requests and docs tasks are tagged as security bugs (`[Feature] Add ZIP Slip Protection`, `feat: support
   file-based secrets`, `Document a secrets … policy`).
4. Some security-class tags are keyword false positives (a pickle issue tagged `command-injection`).
5. The list is not sorted by score.
Items 1–3 and 5 are deterministic fixes; item 4 is left to the model triage (`is_real_bug`, `security_relevant`), which is
exactly the hybrid-score design (B4).

### 11 — Prompt 11 audit, external code review, CI, Spark Open connectivity
**Prompt 11 (accepted):** scout quality fixes and rate limiting, `.env` loading, `triage.py`, `critic.py`, `select.py`,
`issa triage --dry-run`. Independently verified: 161 fast tests passed, ruff clean (commit `762949b`).

**External review (a developer friend of the user, "ARG") — checked against the code by the administrator:**
1. Triage/critic call the model with `effort="low"` → `max_tokens=512`; a 30-candidate JSON answer would be cut off. **Confirmed.**
2. `select.pick()` never checks `is_real_bug`. **Confirmed.**
3. The issue body never reaches triage/critic, and `critic.py` hardcodes `"security_class": None`. **Confirmed.**
4. `select` can pick `runner: node`, but workspace/verifier only support Python. **Confirmed.**
5. `issa run` / `issa replay` are still stubs; `repro_result` is never emitted. **Confirmed.**
6. CI failure attributed to CRLF line endings. **Not the cause** — see CI below.
Items 1–4 plus model routing (`recommended_solver_model`) are in prompt 12; item 5 goes to prompt 13.

**CI (GitHub Actions, Linux):** run 1 failed at `uv python install 3.13` because `uv.toml` forbids uv-managed Python
downloads → workflow now uses `actions/setup-python`. Run 2 reached the tests: 98 passed, 63 errors, all
`FileNotFoundError: .cache/pytest` — the pinned `--basetemp` parent folder does not exist in a fresh clone (it is
git-ignored). Fix: track an empty `.cache/.gitkeep`. This also affects judges cloning the repo, so it matters.

**Muse Spark Open connectivity (Baseten):** base URL `https://inference.baseten.co/v1`, Bearer auth. The hackathon id
`meta-models/muse-spark-open-1026` → HTTP 404 "please check the model you provided"; `meta-models/muse-spark-open` →
200, answer `OK`, `reasoning_content` present, 152 prompt / 113 completion tokens (102 reasoning). `reasoning_effort`
defaults to `xhigh`, so ISSA AS must always send it explicitly. Key stored only in the git-ignored `.env`.

### 12 — Spark Open on Baseten + triage/critic/select fixes
**Independently verified:** 190 fast tests passed, ruff clean (commit `6905b4f`). `SPARK_OPEN_MODEL` (default
`meta-models/muse-spark-open`), explicit `reasoning_effort`, per-batch `max_tokens` (600/candidate + 2000, cap 16000),
`reasoning_tokens` / request id / 1500-char `reasoning_excerpt` recorded in every `model_call`, one retry on truncated JSON.
`body_excerpt` reaches triage and critic inside `<<<UNTRUSTED_ISSUE_TEXT>>>` delimiters (prompt-injection guard);
`security_class` no longer hardcoded in the critic. Selection skips `is_real_bug == False` and non-Python runners and
returns `recommended_solver_model` / `recommended_effort` / `model_rationale` (contributor by default; `muse-spark-1.3`
+ `high` for high difficulty or injection / path traversal / deserialization / auth bypass). ARG items 1–4 closed.
Administrator changes committed alongside: README honesty pass (model status table, build provenance, regression wording),
CI fix for fresh clones (`.cache/.gitkeep`).

### 13 — First live Muse Spark Open triage, `issa run`, `issa replay`
**Live triage (Baseten, `meta-models/muse-spark-open`):** one batched triage call over 30 scouted issues (14.5k in /
2.8k out, 427 reasoning tokens, request `chatcmpl-9b3e412c…`) and one critic call over the top 5 (2.6k in / 1.1k out,
request `chatcmpl-24fce9df…`). The open-weight model was load-bearing: it marked **20 of 30** candidates
`is_real_bug=false` (CVE-aggregator feeds, news digests, bounty tasks, feature requests) that the keyword filters had
let through, and the critic lowered overconfident scores (keynote-parser −0.14, decompress-zip −0.17). Deterministic
selection then skipped the already-solved keynote-parser#91 and picked **ansible/ansible-runner#1548** (path traversal
& symlink escape in `unstream_dir()`, hybrid 0.834) with `recommended_solver_model = muse-spark-1.3`, effort `high`
("path-traversal security class"). Evidence committed: `scout/`, `triage/`, `traces/triage-scout-20261102-222258.jsonl`.
Open item: Baseten price for the model is not configured, so cost is logged as unknown (tokens are recorded).

**Solve of ansible-runner#1548 not run yet:** Claude Code's auto-mode safety classifier denied the administrator's
`issa solve` because it clones and executes third-party code on the user's machine. Left to the user's decision.

**Prompt 13 (accepted, 196 fast tests, commit `88bf64c`):** `issa run` (scout → triage → critic → select → solve →
verify under one run id and one trace, run-level `--max-usd` cap checked before every paid stage, `--dry-run`),
`issa replay` (verifies the chain, refuses a broken one, prints a readable timeline with reasoning excerpts; verified by
the administrator on the live triage trace), `repro_result` emitted. ARG item 5 closed.

### 14 — ansible-runner#1548 live solve, solver fixes, `issa recover`, public page
**Live solve (run by the user, `20261102-223630-ansible-ansible-runner-1548`, muse-spark-1.3 / high):** Muse Code
finished the job: a regression test commit (`313a181`) and a fix commit (`eb78464`) confining `unstream_dir()` to the
target directory (CWE-22/CWE-59), confidence 0.85. ISSA AS still recorded `ERROR` "muse exec returned invalid JSON":
the final answer held two concatenated JSON objects (an all-null one, then the real one). Two more defects found by the
administrator in the same run: the error path logged 0 tokens although `run.usage` reported 4.65M input (4.53M cached),
54.3k output, 59 calls (≈ $1.06), and that cost exceeded the run's $0.60 cap, which was never checked during or after
the session.

**Prompt 14 (accepted, 208 fast tests re-run by the administrator, ruff clean, commit `e8fdca0`):** answers parsed with
`raw_decode` (last schema-matching, non-null object), cost recorded on every path, `--max-model-steps` derived from the
remaining USD (≈ $0.018/step for muse-spark-1.3, $0.002 for the contributor model) with a live kill and a
`cap_exceeded` event, and `issa recover`. The administrator ran `issa recover` on the ansible run: it rebuilt
`verdict.json` (`PENDING`), `fix.patch`, the test file and `cost.json` ($1.059) from the saved log and git, without calling
Muse or running target-repo code; `issa verify` reports the chain intact. `issa reverify` (which runs ansible-runner's
tests) is left to the user. Follow-up: at the default $0.60 cap, muse-spark-1.3 now gets ≈ 33 steps while this fix took
59, so escalated issues need a higher per-issue cap.

**Public page:** <https://issasodaweb.com/issaas/> (Spanish, English, Brazilian Portuguese), a presentation of the
project on the ISSASODA site, linked from its project cluster (`#p-issaas`). It presents the project; it does not run it.

### 15 — `issa serve`, the local control panel
**Prompt 15** stopped at the 60-step limit with `serve.py` and the `recommended_max_usd` rule done and honestly reported
what was missing; **15b** finished `web/`, the CLI wiring, 15 offline tests and the docs (commit `3029b5a`). Escalated
picks (`muse-spark-1.3`/high) now carry a $1.20 per-issue cap. Administrator checks: 223 fast tests, ruff clean, panel
opened in the browser (runs, scout, triage, decision, actions; no console errors) and the guards probed with curl
against the live server: foreign `Host` → 403, foreign `Origin` POST → 403, paid action without a confirm token → 403,
path-traversal run id → 400, no secrets in any JSON response. No paid action was clicked. Minor findings for later:
scout/triage runs are labeled `PENDING` in the runs table, and the budget line counts only solver `cost.json` files.

### 16 — Target test environments, and why ansible-runner#1548 cannot be verified on Windows
The user's first `issa reverify` of the recovered ansible run returned `ERROR` "test still fails after patch", but
`verify.log` showed `No module named pytest` on every call: the workspace only had `pip install -e .`, and the verifier
blamed the fix for an environment problem. **Prompt 16** (commit `656ea3d`) installs the target's test dependencies
(pyproject groups, requirement files, `tox.ini` `-r` lines; URL/VCS deps skipped), checks that pytest can collect the
test before Fail-to-Pass, and reports infrastructure failures as "test environment incomplete: <cause>"; it adds
`issa reverify --setup`. The user's `--setup` run then failed with "uv venv failed (see log)" without writing the log:
uv 0.13 refuses to recreate an existing `.venv`. **Prompt 16b** (commit `0d745b8`) uses `uv venv --allow-existing`,
gives every target-repo subprocess `stdin=DEVNULL` and always writes `install.log`.

**Result (user run, 19:33):** dependencies installed, then `ERROR` "test environment incomplete: pytest exited with code
4 on base … `import fcntl` … No module named 'fcntl'". ansible-runner is POSIX-only, so its tests cannot even be
collected on Windows; the verdict is about the environment, not the fix. It also means Muse Code could not have run its
test during the solve, so the 0.85 confidence was not test-backed. Next: verify proof bundles on Linux.

### 17 — `issa verify-bundle`: both fixes VERIFIED on a clean Linux runner
**Prompt 17** (commit `bcf9401`) added `issa verify-bundle` (rebuild a run from `proof/<run_id>/` alone: fresh clone at
`test_parent`, committed test file, `fix.patch`, venv, verifier) and the manual `verify-bundles.yml` workflow
(`contents: read`, `persist-credentials: false`, no secrets). The administrator hardened the workflow input (passed via
env and validated, Actions-injection guard, commit `3bf7a5a`). Four Linux runs, each failure a defect in ISSA AS, not in
the fixes, each fixed by Muse Code:

| CI run | keynote-parser#91 | ansible-runner#1548 | Fix |
|---|---|---|---|
| 41555739576 | ERROR, reason not printed | crash: `TimeoutExpired` bytes + str | 17b: timeouts become ERROR, reasons and platform printed |
| 41557110526 | Windows interpreter path stored in the bundle | full suite > 1200 s, before Fail-to-Pass | 17c/17d: portable interpreter, Fail-to-Pass first, `unit`-dir suite scope |
| 41561890786 | `--cov` addopts but pytest-cov not installed | **f2p=true**; `test/unit` > 1200 s | 17e: install every test source and addopts plugins; suite-only `--timeout=120`, `-n auto` |
| [41613094986](https://github.com/Ataraxiainc2/ISSAAS/actions/runs/41613094986) | **VERIFIED**, f2p, 0 regressions, 3 baseline failures, full suite | **VERIFIED**, f2p, 0 regressions, `test/unit` | — |

The last job took 77 s. Results are recorded in each bundle's `linux-verify.json` (written by the administrator from
the job log summary line). ansible-runner#1548 is therefore the second verified security fix and the first one chosen
autonomously: scouted, triaged by Muse Spark Open, escalated to `muse-spark-1.3`/high by the deterministic router, fixed
by Muse Code ($1.06) and verified without any model. Regression scope for it is `test/unit`, stated in the verdict.

### 18 — Judging pass: a false claim, `SECURITY.md`, docs
The administrator checked every README and project-page claim against the code. **Finding:** the project page said ISSA
AS leaves alone projects that ask for private security reports, but the scout never read `SECURITY.md` (backlog B5,
never built). ansible-runner's `SECURITY.md` says reports "MUST NOT be reported through public channels such as GitHub
issues, pull requests" and must go to the project's security email. The issue was already public (filed by its
reporter) and ISSA AS published nothing, but the claim was false: the page was corrected the same hour (ISSASODAWEB
`bfa411b`). **Prompt 18** (commit `55ca754`): the scout parses `SECURITY.md` into `security_policy`
(`private_reporting`, contact, source); `publish` never drafts a public PR for such repos and writes `disclosure.md`
instead (DRAFT header, the policy's fields, AI-assistance line); `issa disclosure <run_id>` builds it for old runs.
Generated for ansible-runner#1548 and committed; it is not sent. Gap noted: "Affected versions" is left unknown even
when the issue body names versions. Docs: README gained a judges' path, the spend from the session logs (build ≈ $0.70
over 23 sessions, solves $1.07), a Known Limits section, and the stale `-1026` setup text was removed; ARCHITECTURE.md
gained an implementation-status box (plan vs what was built; patch critic, ablation and benchmark not built).

### 19 — Demo video in three languages
The user asked for a narrated video in English, Spanish and Portuguese using Meta voices and the hackathon tokens.
**Finding:** the Meta Model API has no text-to-speech. `POST /v1/audio/speech` returns 404, Muse Wonder outputs text
only, and `muse-voice-transcribe-1.0` is listed by `/v1/models` but returns 404 / `model_not_found` on every route
tried. The user then chose Google, or the best-ranked voice available: Gemini 3.8 Flash TTS (5th on the Artificial
Analysis speech leaderboard; above it only ElevenLabs, with no key, and Alibaba Qwen; the user had named Google). The Meta models still do the
rest: Muse Spark 1.3 translated the administrator's English script ($0.0995, three calls, one malformed JSON reply)
and Muse Wonder listened to every clip and compared it with the script ($0.59, mostly reasoning tokens). **The check
caught a real defect:** with the style prompt inline, Gemini read the prompt aloud and dropped a sentence; the model
also rejects a system instruction, so the final round sends the narration text only. The first round also ran past
3 minutes; the script was cut by about 15 %. Final: 2:32 / 2:36 / 2:42, −14.5 LUFS, 1080p30, with SRT subtitles.
The slides use only committed data (triage report, `fix.patch`, trace hashes, `SECURITY.md` quote, `disclosure.md`) and
a screenshot of the local panel, cropped to the keynote-parser runs because the panel does not yet show the Linux
verdict (ansible-runner appears there as the local Windows ERROR; backlog). The request to Meta for a voice model is in
`docs/VIDEO.md` and the README. Tool: `video/make_video.py` (ruff: `video/*` exempt from E501 for inline HTML/CSS).


### 20 — Attacking the verifier, the cost gap, video v2
**Prompts 20–22** (PR #2, merged). The administrator asked Muse Code to attack its own verifier. Before the fix,
Fail-to-Pass trusted pytest's exit code, so a patch that skipped, xfailed or deleted the regression test, or added a
`conftest.py` hook, could have been reported VERIFIED. F2P now reads `--junitxml` (≥ 1 passed, 0 failed / errored /
skipped / xfailed), and a patch that touches the regression test or any pytest configuration is UNPROVEN before any
test runs. `issa eval verifier` runs the real `verify()` on 9 patches against a synthetic path-traversal project:
**7/7 attacks rejected, real fix accepted, 1 known limit** (a fix that special-cases the test's literal input still
passes; Fail-to-Pass cannot catch overfitting). The real fix takes about 4–7 s per check; tampering is rejected in
about 0.2 s. Audit findings sent back as Prompt 21: the first version used the system temp folder and, in `--bundle`
mode, would have run third-party tests with ISSA AS's own interpreter (AGENTS.md rule 3), and its summary counted
the control and the known limit as "attacks rejected". Prompt 21 moved scratch state under `workspaces/`, gave
bundle mode the target's own venv, and made the summary honest. CI found one test that needed a cached pytest
wheel; it now skips rather than going online.

**The $1.12 gap.** On 2026-11-03 Meta's Usage page showed $3.63 spent, against $2.51 in our ledger. Prices matched.
Argon (the user's other assistant) and the administrator traced it: Muse Code reports `reasoning_tokens` separately
from `output_tokens`, Meta bills both as output, and the ledger counted only output (Meta's 1.5M contributor output
tokens = our 0.94M output + 0.46M reasoning + sub-agent sessions). Muse also starts sub-agent sessions whose usage
is not in the parent's `run.usage` (`children_included: false`): 62 such sessions on disk. Together with Prompts 19–20
running at the time, this explains the gap to within rounding, except about 0.76M `muse-spark-1.3` input tokens (≈ $0.7)
that appear in no log of ours; they are left **unattributed** (probably early direct API probes). Prompt 22 bills
reasoning at the output rate and marks cost records `children_included: false`, so a logged cost is labelled a lower
bound. Committed proof bundles keep their original `cost.json`; the ansible-runner fix cost $1.23 with reasoning, not
$1.06.

**Video v2.** The watt-mind tracker scored the first video 1.05 / 2.50: no hackathon provenance in the first 20
seconds, about 5 % live product footage, no visible model execution, captions not burned in. v2 is recorded from
the running product by `video/record.py` (headless Edge, DevTools screencast, visible cursor; live scout and Muse
Spark Open triage; the real Muse Code session in the Muse console; real commands streamed into a terminal page), opens
with "Built for the Meta Global AI Developer Hackathon 2026", and burns captions and step labels into the picture.
Limits stated in `docs/VIDEO.md`: GitHub job logs need a sign-in, so the CI scene shows run and step status only; long
waits are cut. Narration and the listen-back check as in entry 19 (three Muse Wonder misfires re-checked at 92–95 %).
Meta spend for v2 ≈ $0.70 (with the intro voicing and its listen-back check).

### 21 — Panel redesign for the video, video v2 rebuilt
The user compared v2 with the tracker's leading entry and found three problems: the live clips looked pixelated, the
music could not be heard, and Argon's 20-second intro card had too much text.

**Prompts 23–24 (panel).** Prompt 23 asked Muse Code for a dark, video-ready redesign of `issa serve`: KPI cards,
score bars on triage, a verdict hero, a fail-to-pass strip and a coloured `fix.patch`, using `textContent` only (a test
checks that `app.js` has no `innerHTML`). Accepted after review (341 fast tests), but the audit at 1280×720 found
that the ansible-runner fix showed "no failure on base → NOT demonstrated". The panel read the local Windows ERROR and
ignored `linux-verify.json`. Scores and costs were also printed as raw floats, and links were hard to read on the dark
theme. Prompt 24 fixed these:
- the strip shows the best evidence (Linux fail-to-pass beats a local ERROR, with the local result kept as one muted line);
- empty rows are hidden;
- the hero has a "Linux CI · VERIFIED" pill;
- numbers are formatted;
- the KPI is labelled "Runtime spend (ledger)";
- the Fixes filter shows `solve` runs only.

Prompt 24 also added optional support for `evals/results/cost-reconciliation.json`, so the panel shows the
corrected $1.23 (including reasoning) beside the logged $1.06. 347 fast tests passed. One bug is still open: a
`#detail/<run>` link selects the first run when the page first loads. The recorder works around it with the run
selector.

**Video rebuild (administrator tools, `video/`).** The pixelation came from capturing JPEG frames and encoding them
three times at 1080p. Frames are now lossless PNG at 2240×1260, the exact window each clip fills, and the build
compresses once, to 2560×1440 output. The music was inaudible because its energy sat at 50–110 Hz, where laptop and
phone speakers barely play, about 20 dB under the voice, and it was ducked 10:1 on top of that. The bed now has a
pluck arpeggio and a hat, and sits about 12 dB under the voice. Argon's intro was checked and corrected first:
- 7/7 attacks, not 9/9;
- spend ≈ $4.4, not $3.56;
- triage paid by Baseten credits, not "$0.00";
- solvability, not severity.

It was then replaced by a minimal title card that hands over to the live panel, so the 8 s and 15 s keyframes are
live UI. About 88 % of each video is live footage. The "Built by Muse Code" scene now shows the Prompt 23 session.
A third live triage (`triage-scout-20261103-234936`) picked ansible-runner #1548 again. Every narrated scene was
listened back with Muse Wonder (word match 0.81–1.00). One Portuguese check failed with "could not transcribe" and was
re-run. Meta spend for v2 ≈ $0.83 in all (see `docs/VIDEO.md`); the panel redesign is build cost (≈ $0.07).

**Escalated solver cap raised to $2.00 (administrator fix, no model calls).** No failure so far came from a budget
cap: the first psobot run failed on an invalid output schema (API 400), the ansible-runner run on an answer-parsing bug
and then on `fcntl` under Windows, and two Muse Code development sessions on the console's 60-step limit. But the
escalated cap of $1.20 was set when the ansible-runner fix appeared to cost $1.06; with reasoning tokens counted it
cost $1.23, so a rerun would be stopped just before finishing. `ESCALATED_MAX_USD` is now 2.00 and the per-step
estimate for `muse-spark-1.3` 0.021 (from 0.018), so the default $0.60 cap allows 28 strong steps and the escalated cap
the full 60-step high-effort session. The $0.60 default and the $150 project budget are unchanged. 347 fast tests pass.

### 22 — The agent chooses where to look; the verifier stops trusting the host
**Prompts 25–33, 2026-11-04** (branch `feat/more-cases`). Every prompt is in `prompts/`; Muse Code wrote all product
code; the administrator audited each commit and wrote the follow-up prompts.

**Autonomy.** Prompt 25 stopped selection from repeating issues already attempted and made the solver run the repo's
whole suite before handing in a fix. Prompts 26–27 added guided discovery, on by default in the CLI and the panel:
before searching GitHub, Muse Spark Open (open weights, Baseten) reads ISSA AS's own outcomes in `proof/*/` and
proposes up to 5 keyword searches with a reason each. Code validates every proposal (letters, digits, spaces and
`-_."` only, ≤ 60 chars, no `:` so no `repo:`/`user:`/`org:` qualifiers), builds the query, and runs it through the
cached, rate-limited client; fixed queries fill the rest. The GitHub token (read-only, fine-grained) is used only by
that deterministic client and is stripped from the `muse exec` environment. Prompts 28 and 30 added plain-language
briefs of every candidate in English, Spanish and Portuguese (a second, translation-only pass after Spark Open first
answered in English only and the validator silently copied English into the other two), a language switch, and a
live "Now working on" card in the panel.

**First live guided run** (`run-20261104-173437`, started from the panel with one confirm click, $2.50 cap). Spark
Open proposed 5 searches aimed at what had already been verified (zip-slip, path traversal, taint/sanitiser bugs),
saying it avoided the report types that had caused regressions; it triaged 25 candidates (5 in Chinese), picked
aerkalov/ebooklib#359 and routed it to Muse Spark 1.3/high. Muse Code found the main traversal already guarded in
that snapshot and closed a remaining gap: `os.path.commonpath` raises `ValueError` on paths it cannot compare (another
drive or UNC), so the guard now fails closed with the documented `OSError`. This is hardening of an existing guard,
not a new exploit. Cost $0.76. A later guided scout already cited ebooklib#359 as a pattern to look for.

**Four honesty bugs found by the administrator, fixed by Muse Code.**
1. *Host pytest config leak (Prompt 29).* ebooklib has no pytest config and `workspaces/` sits inside this repo, so
   pytest used ISSA AS's own `pyproject.toml` (`--basetemp` in a folder Muse's sandbox had locked). Both runs failed
   in setup and the verdict read "test still fails after patch". Every target pytest run now gets `--rootdir`,
   `-p no:cacheprovider`, a fresh `--basetemp` and, when the repo has no config, a neutral `-c`; a fail-before with no
   test reaching an assertion is ERROR. `issa reverify` then gave ebooklib#359 **VERIFIED** with the same patch.
2. *Host environment leak (Prompt 32).* The clean-room rebuild of QWED#535 reported `1 passed, 87 skipped, 5372
   errors` for the full suite: the operator's machine sets `DATABASE_URL` (Postgres) and the verifier passed the whole
   environment, so "0 regressions" rested on almost nothing. Worse, `issa` loads `.env`, so Meta and Baseten keys were
   visible to third-party install and test code. All target code now runs with an allowlisted environment; `verify.log`
   lists the variable names passed, never values.
3. *Suite timeout (Prompt 33).* With a clean environment QWED's suite really ran and hit the 120 s command timeout;
   every caller now uses the 1,200 s suite timeout. QWED#535 re-verified **VERIFIED: 5,466 passed, 0 regressions**
   (3 pre-existing failures, identical before and after).
4. *A test that cannot fail for the right reason (Prompt 31).* A VulnClaw#310 retry wrote a test importing a helper
   the fix itself added, so it failed at import on the base commit. The verifier refused it as Fail-to-Pass; the
   solver prompt now requires a test that runs against the unfixed code.

Also fixed: confirm tokens rejected whole-number budgets (`3` vs `3.0`), tests wrote stray files into `scout/`,
and two `slow` tests stale since Prompt 25. 446 fast tests and 447 including slow pass; `ruff` clean.

**VulnClaw#310, four attempts, never claimed.** With the Prompt 31 rule the regression test finally failed for the
right reason on the base commit and passed after the fix, with both the contributor model ($0.03) and Spark 1.3/high
($0.86). Both runs still ended REGRESSION: VulnClaw keeps a security allowlist of process-spawn sites keyed by file
and line (`scripts/verify_execution_boundary.py`), so any fix that adds lines above a reviewed site in
`vulnclaw/cli/main.py` breaks 3 guard tests. Making them pass means editing a security allowlist, which the solver
is told not to do and which deserves a human review. ISSA AS reported the regression instead of silencing the guard.
Stopped there; recorded as a known limit. Muse Code development today (Prompts 25–33): ≈ $0.55; solves: ≈ $2.97.

### 23 — First upstream PR, and outside reviewers find what our verifier could not
**Prompt 34, 2026-11-04** (branch `feat/upstream-qwed`). The administrator re-applied the verified QWED#535 fix on
current upstream (unchanged since our run) and the human owner opened it as
[QWED-AI/qwed-verification#586](https://github.com/QWED-AI/qwed-verification/pull/586) from their fork, with an AI-use
disclosure and a link to the proof bundle. Nothing was sent by ISSA AS itself.

Within minutes QWED's automated reviewers (Gitar, Greptile, Sentry, CodeRabbit, CodeAnt) left 17 inline findings.
The administrator did not take them on trust: each scenario was written as a probe (`bot_cases.py`) and run against
upstream `main` and our branch. Result: **one real regression introduced by our patch** — `y = x; y += "s";
os.system(y)` was reported on `main` and missed on our branch — plus gaps the patch had widened or left open
(`os.environ[...]`/`sys.argv[...]` inside expressions propagated but never reported, `render_template_string`
cleared by `html.escape`, substring source matching, SQL bound parameters flagged, tuple unpacking tainting every
target). One bot claim (branch reassignment) did not reproduce. The PR was converted to draft with a public comment
stating the regression.

This is the known limit in practice: Fail-to-Pass plus the full suite only protect what the tests cover, and QWED had
no test for augmented assignment. Our verdict "VERIFIED, 0 regressions" was true for the suite and still missed a
behavioural regression.

Prompt 34 (Muse Code, `muse-spark-1.3-contributor`, 28 calls, ≈ $0.02) fixed all eight items with one regression
test each. Audit: the 8 new tests fail on the previous commit and pass after; the administrator's own 8 probes
(written separately) all behave as expected, where `main` misses 4; the bot scenarios now give the expected result;
QWED's full suite on Windows, clean environment: **5,474 passed, 0 regressions** (failures identical to `main`:
the ACL-locked `pytest-of-24` folder and `test_fallback_to_cwd`). No `eval`/`exec`/`subprocess`/`os.system` calls
were added. Lesson for the solver: probe the analyzer with the reviewers' scenarios before opening a PR, not after.

The reviewers caught the next round too: within minutes Gitar flagged that item 6 (exact source names) had dropped
method-call sources. The administrator confirmed it with a separate probe (`method_sources.py`):
`sys.stdin.readline()`, `sys.stdin.read()` and `conn.recv()` were VULNERABLE on `main` and on the first commit, and
SAFE after Prompt 34 — a second regression, introduced by a fix. The "ready for review" comment was held back.
Prompt 34b (≈ $0.01) matches method calls by their last dotted segment, excluding the generic `get`
(`os.getcwd()` and `dict.get()` stay non-sources), with a test that fails on the previous commit. Audit: 49 taint
tests pass, every probe set (bot scenarios, administrator probes, method sources) gives the expected result, and the
full suite gives **5,475 passed, 0 regressions**. `tests/test_cli_cache.py` errors intermittently under `-n auto` on
`main` too (module-level asserts on a shared on-disk cache, unrelated to the analyzer).

Before marking the PR ready, the administrator followed a reviewer's hint (`request.cookies.get`) and found a third
regression of the same family: `request.cookies/headers/values/query_params.get(...)` were sources on `main` (by
substring) and not after excluding the generic `get`. Prompt 34c (≈ $0.01) treats `.get` on `request.*` (and
`self.request.*`) as a source. Then the check that should have come first: a **systematic sweep** (`sweep.py`) over
every source form × every sink the analyzer defines, in three shapes (assigned, inline, concatenated), on `main` and on
the branch. 3,450 combinations: the branch detects 1,900 that `main` misses, never crashes, and loses only arbitrary
`obj.get()` — the intended removal of a false positive (any `dict.get()` was a source on `main`), now stated in the PR.
Full suite: **5,476 passed, 0 regressions**. Lesson, now a rule for upstream PRs: before claiming "no regressions"
for an analyzer, sweep its own source/sink tables against the base branch; a passing suite is not enough.

Marking the PR ready triggered the reviewers that skip drafts. Within minutes Greptile, Sentry and CodeRabbit posted
seven findings on head `00bb7bf`. The administrator reproduced every one against `main` and the branch side by side
(`review2_cases.py`). Results:
- **Three real regressions:**
  - `request.get_data()` and `flask.request.args.get()` were no longer sources.
  - `shlex.quote(x).strip("'")` cleared shell taint, although the `strip` removes the protective quotes.
  - Non-injectable sink keywords (`env=os.environ.copy()`, `cwd=`, `input=`, `json=`) were reported as injections.
    These were false positives.
- **Two old gaps:** `request.get_json()[...]` / `getlist` sources, and starred unpacking targets.
- **One wrong bot claim:** `cursor.execute(args=(), query=q)` was not detected on `main` either.
- **One deliberate trade-off:** `x = input(); x = shlex.quote(x)` stays reported. The analyzer keeps the union of
  assignments so that branches cannot hide taint.

Prompt 34d (≈ $0.02, 39 calls) fixed the first five groups:
- Request-object `get*` methods are sources.
- A sanitizer whose result is transformed by a method call no longer cleans; the assigned and inline forms behave the
  same.
- A per-class allowlist of injectable keywords. The SQL query is selected by position or by keyword name.
- Starred targets are tracked.

Each of the four new tests fails on `00bb7bf` and passes after. Audit results:
- 54 taint tests pass.
- Every earlier probe set is unchanged.
- The sweep (3,450 combinations against `main`) still gives +1,900 detections, no crashes, and only the intentional
  `obj.get()` loss. Nothing is lost against `00bb7bf`.
- Full suite: **5,480 passed**, with failures identical to `main`. The one exception is `tests/test_cli_cache.py`,
  which errored at collection under `-n auto` and collects cleanly alone. It is known to be flaky under xdist and is
  not touched by the diff.

### 24 — Pass-to-Pass preservation gate

**Why.** Entry 23 showed the gap: `VERIFIED` (Fail-to-Pass plus a green suite) only protects behaviour some test
already pins down. QWED#586 passed both checks and still lost detections that no QWED test covered.

**What Prompt 35 built.** [Prompt 35](../prompts/35-preservation-tests.md) (≈ $0.07, three sessions; the first hit the
80-step cap):
- The solver's output schema gains `preserve_file`. The solver prompt asks for 3–8 tests of existing behaviour next to
  the change, committed with the regression test.
- `verify()` runs that file alone on base and with the patch:
  - not clean on base → `UNPROVEN`;
  - not clean after the patch → `REGRESSION`;
  - a patch that edits the file → `UNPROVEN` with no test run.
- The result is recorded as `p2p_checked` / `p2p_tests_passed` in `verdict.json`. Without `preserve_file`, behaviour
  is unchanged and old bundles still re-verify.

**Audit.**
- The key test is `test_p2p_broken_by_patch_is_regression`. Its patch fixes `add` (Fail-to-Pass passes) but breaks
  `mul`, which only the preservation file covers: the QWED#586 situation in miniature.
- Full suite: 461 passed. Ruff: clean.
- The administrator reflowed two docstring paragraphs Muse left mis-indented.

**Not done (time).** An adversarial reviewer that proposes inputs, plus a plain-Python base/patched diff on them
(listed as the next step in the README). The four existing bundles predate the gate.

**Round on `1dbb66a`.** Greptile, CodeRabbit and Sentry posted six more findings. The administrator reproduced them
across `main`, `00bb7bf` and `1dbb66a` (`review3_cases.py`):
- **Two regressions from Prompt 34d's keyword allowlist:** `os.popen(cmd=x)`, and a SQL query keyword next to a `**`
  expansion.
- **One regression against `main`:** `shlex.quote(x)[1:-1]` assigned to a variable.
- **Two old gaps:** `request.form[...]`-style reads, and a sliced sanitizer used inline.
- **Left unchanged and explained:** slicing a variable that already holds a sanitized value needs per-variable
  provenance, and long `+=` chains are quadratic (0.8 s for 300).

Prompt 34e (≈ $0.02, 18 calls) fixed the first four. Audit:
- Each of the four new tests fails on `1dbb66a`, and 58 taint tests pass.
- Every earlier probe set is unchanged.
- The sweep against `main` gives +1,975 detections and no crashes. The only losses are the intentional `obj.get()`
  ones, and nothing is lost against `1dbb66a`.
- Full suite: 5,484 passed, with failures identical to `main` except the known xdist-flaky `test_cli_cache.py`.

Before the ebooklib#359 PR, the same lesson was applied: a preservation probe (`eb_preserve.py`) runs
`Directory.read` over 13 path shapes on base and patched. Only the two other-drive paths change (`ValueError` →
`OSError`), which is the intended fix.
