# Prompt 25 — pick fresh issues, honest security class, panel deep link

Read `AGENTS.md`. Scope: `src/issa_as/select.py`, `src/issa_as/scout.py`, `src/issa_as/run.py`, `src/issa_as/cli.py`,
`web/app.js`, and their tests. Do not touch README, docs, `video/`, `proof/`, `traces/`, `scout/`, `triage/` or
`evals/results/`. No network in tests. Stay on the current branch.

The administrator ran three live triage runs; all three picked ansible/ansible-runner#1548 again, an issue already
fixed and verified on Linux. We want `issa run` to move on to new issues. Fix these:

1. **Linux-verified counts as solved.** `select.solved_issue_urls()` only reads `proof/*/verdict.json` with
   `verdict == "VERIFIED"`. The ansible-runner bundle says `ERROR` locally (Windows cannot import `fcntl`) but its
   `proof/<run>/linux-verify.json` has `f2p_pass: true` and `regressions: 0`. Treat that bundle as solved too. Read
   the issue URL from `verdict.json` (or `issue.json` if missing there). Corrupt files are ignored, as today.
2. **Do not re-attempt by default.** An issue that already has any proof bundle (any verdict) is skipped with the
   reason `already attempted (<VERDICT>, run <run_id>)`; solved issues keep the reason `already solved (...)` and say
   whether the evidence is local or Linux CI. Add `issa run --retry-attempted` to allow re-attempting issues whose
   bundles are not solved (never solved ones). Thread the flag through `run.py` and `select.pick()` as a keyword
   argument with default `False`; the panel keeps today's call shape.
3. **Honest security class.** In `scout.evaluate_issue`, when `classify_security(title, body)` finds nothing, the
   candidate currently inherits the search query's class. Example: VulnClaw#310 is a plain crash (`get_summary()`
   returns an int and the code slices it) but was labelled `sql-injection`, which escalated it to the expensive model.
   Keep `security_class = None` in that case and record the query class in a new optional field `matched_query_class`
   on the candidate (add the field, rename none, old reports without it still load).
4. **Panel deep link.** Opening `#detail/<run_id>` directly in a new tab shows the first run instead of `<run_id>`.
   Make the initial route win over the default selection. Also pluralise "1 calls" → "1 call" wherever the panel
   prints a call count.
5. **Solver step floor.** Live evidence: VulnClaw#310 on `muse-spark-1.3-contributor` at `medium` (30 steps) ran out
   of steps right after committing its test (ABSTAIN, $0.017); at `high` (60 steps) it finished in 55 steps for
   $0.025. Exploring, building the venv and running tests use about half the steps in a real repo. Give the
   contributor model at least 60 steps whatever the effort (`--max-steps` still overrides; the USD-derived cap still
   applies). The strong model keeps today's per-effort steps.
6. **Solver instruction: keep the repo's own checks green.** The same #310 fix passed fail-to-pass but ISSA AS found 3
   regressions: the repo has a guard test that allowlists every `subprocess` call by `file:line`, and the fix moved one
   call to a new line. In the solver prompt (`solver.py`), tell Muse: before answering, run the repository's full
   test suite (or, if it takes more than about 5 minutes, every test module that imports or scans a changed file);
   fix any failure the patch caused in source or config files; never edit, skip or delete existing tests; if a guard
   or allowlist keyed on line numbers breaks, update that allowlist entry and say so in `patch_summary`.
7. Tests (offline): Linux-verified bundle counted as solved; attempted bundle skipped with its verdict in the reason;
   `--retry-attempted` re-allows a non-solved attempt but never a solved one; a query-matched issue with no security
   keywords gets `security_class None` and `matched_query_class` set; old scout JSON without the field loads. For the
   deep link, test the pure route-parsing helper if one exists or add one.

`uv run pytest -m "not slow"` and `uv run ruff check src tests` must pass (uv at `.tools/uv.exe`, `--no-sync` if
locked). Commit locally once, do not push. Be concise in the final summary.
