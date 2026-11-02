# Prompt 03 — Audit-02 fixes + deterministic verifier + hash-chained trace

Read `AGENTS.md` (note the new rules 6 and 7), audit entry 02 in `docs/AUDIT_LOG.md`, and `docs/BACKLOG.md` items B1 and B3.
Scope is limited to the items below. Do not touch other modules.

1. **budget.py:** set the published cached-input rates (`muse-spark-1.3` $0.15 per 1M, `muse-spark-1.3-contributor` $0.002 per 1M)
   and remove the "placeholder" comment. Hosted Spark Open cost comes from env `SPARK_OPEN_PRICE_IN_PER_1M` and
   `SPARK_OPEN_PRICE_OUT_PER_1M` (add to `.env.example`); log a real estimate instead of 0.0.
2. **traces.py — B1 hash chain:** every appended event gets `prev_hash` and `hash` (SHA-256 over canonical JSON of the event
   without `hash`, plus `prev_hash`; genesis `prev_hash` = 64 zeros). Add `verify_chain(run_id) -> ChainReport`
   (ok flag, first bad line number, reason). Add `issa verify <run_id>` to the CLI.
3. **verifier.py — B3 verdicts (pure Python, no LLM):** given a git repo path under `workspaces/`, a base commit, a patch,
   a test file path and a test command, run in a sanitized subprocess (no inherited secrets, cwd/TMP/TEMP inside the
   workspace, timeout) and return one of `VERIFIED`, `UNPROVEN` (test passes on base), `NO_TEST`, `REGRESSION`
   (pre-existing tests break), `ERROR`, with logs. Emit a `verify_verdict` trace event.
4. **Tests (offline):** build a tiny throwaway git repo inside pytest's `tmp_path` with a known bug, and cover
   VERIFIED, UNPROVEN, NO_TEST and REGRESSION; tamper with one trace line and assert `verify_chain` reports it.

Keep it lean. `uv run pytest` and `uv run ruff check` must pass (uv is at `.tools/uv.exe`). Commit locally once, do not push.
End with files changed and test results.
