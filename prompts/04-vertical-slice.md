# Prompt 04 — Milestone 2: vertical slice (one issue, end to end)

Read `AGENTS.md`, `docs/ARCHITECTURE.md` (Milestone 2) and audit entry 03 in `docs/AUDIT_LOG.md`.
Goal: `issa solve <github_issue_url>` takes ONE issue end to end and leaves a hash-chained trace. Keep it lean.

1. **Follow-up from audit 03:** unknown Spark Open price → `cost_usd: null`, `price_known: false` (never 0.0).
2. **`workspace.py`:** read-only fetch of the issue (title, body, labels, repo) via the GitHub REST API (`GITHUB_TOKEN`
   optional, read-only use); shallow clone of the repo into `workspaces/<run_id>/<repo>`; create a project-local venv with
   `uv` inside that folder and install the repo's test dependencies. Paths must be validated to stay under `workspaces/`.
3. **`solver.py`:** one `muse exec` session (flags from ARCHITECTURE.md, `--workspace` = the cloned repo, `--output-schema`
   with fields `test_file`, `test_command`, `patch_summary`, `root_cause`, `confidence`, `abstain_reason`) that must:
   (a) write a failing regression test and commit it, then (b) write the fix and commit it separately. Parse the result;
   emit `fix_attempt` and `model_call` events (tokens and USD from the run's usage). Model and effort are parameters.
4. **Pipeline glue in `cli.py`:** `issa solve <url> [--model] [--effort] [--max-steps]` → workspace → solver →
   `verifier.verify()` using the two commits (base = parent of the test commit; patch = the fix commit diff) → write
   `proof/<run_id>/` (verdict, test, patch, logs, cost) or an abstention note. Nothing is ever sent to GitHub.
5. **Tests (offline):** fake the GitHub API and the `muse exec` call (a stub that writes a test commit + fix commit into a
   `tmp_path` repo) and assert the full pipeline produces a `VERIFIED` proof bundle and a valid hash chain; plus one
   abstention path. No real network or model calls in tests.

`uv run pytest` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally, do not push.
End with files changed, test results, and the exact command to try it on a real issue.
