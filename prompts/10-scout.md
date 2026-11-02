# Prompt 10 — Milestone 3a: security-first scout with deterministic signals

Read `AGENTS.md`, `docs/ARCHITECTURE.md` (Milestone 3) and `docs/BACKLOG.md` items B4, B5, B7, B8.
Scope: new `scout.py` and `signals.py`, a `scout` CLI command, tests, plus one housekeeping item. No model calls in this step.

1. **Housekeeping:** pin pytest's temp dir under the repo (`addopts = "--basetemp=.cache/pytest"` or equivalent) so no
   `pytest-of-*` folders appear in the project root.
2. **`scout.py` — discovery (GitHub REST/search API, read-only, `GITHUB_TOKEN` optional, results cached under `.cache/scout/`):**
   - Security-first (B7): query open issues for vulnerability classes first — path traversal / zip slip, command or SQL
     injection, XSS, open redirect, XXE, ReDoS, unsafe deserialization / pickle / yaml.load, SSRF, auth bypass, secret
     leakage — then ordinary `bug`-labelled issues to fill the pool. Tag each candidate with `security_class` or `null`.
   - Reputation filter: repo not archived, not a fork, pushed within 180 days, stars ≥ 50, size ≤ 50 MB.
     Drop bot-generated dependency-alert issues (Dependabot/Mend/Renovate/"N vulnerabilities (highest severity…)").
   - Maintainer respect (B5): skip repos whose README/CONTRIBUTING refuse AI-generated contributions, and issues that
     already have a linked open PR.
   - Language runners (B8): detect the ecosystem (`python` = pyproject/setup.py + tests; `node` = package.json with a
     test script); others are kept in the report as `runner: none` and never attempted.
   - Output 20–30 candidates.
3. **`signals.py` — deterministic part of the hybrid score (B4):** has reproduction steps, stack trace, code snippet,
   test suite present, CI present, repo size, issue age, comment count, security_class. Each signal is a named number
   with its weight, so the viewer can explain the score.
4. **CLI:** `issa scout [--limit 30] [--languages python,node]` writes `scout/<run_id>.json` and emits
   `issue_discovered` events (with signals) into a hash-chained trace.
5. **Tests (offline):** fake GitHub responses covering ordering (security first), each filter (archived, fork, stale,
   low stars, bot alert, AI-refusal, linked PR), ecosystem detection, and the signal table.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
