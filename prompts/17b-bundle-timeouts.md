# Prompt 17b — verify-bundle: timeouts that do not crash, reasons that are visible

Read `AGENTS.md`. Scope: `verifier.py`, `bundle.py`, `cli.py`, tests. No network in tests.

Facts from the first Linux run (GitHub Actions run 41555739576, ubuntu-26.04, Python 3.13.17):
- ansible-runner#1548: the rebuild reached the baseline suite run, which hit the 120 s default timeout; then
  `verifier._run` crashed with `TypeError: can't concat str to bytes` at `out = (exc.stdout or "") + (exc.stderr or "")`
  — `TimeoutExpired.stdout/stderr` are bytes even with `text=True`.
- keynote-parser#91: `verdict=ERROR` after 4 s, but the summary line printed no reason, so the cause is invisible in the
  job log.

1. Every `TimeoutExpired` handler decodes `stdout`/`stderr` when they are bytes (utf-8, `errors="replace"`). A timeout is
   an `ERROR` verdict with reason "<step> timed out after <N> s", never an exception.
2. `issa verify-bundle --timeout <s>` (default 1200) for the suite runs; the per-test Fail-to-Pass runs keep a shorter
   limit.
3. The summary line adds `reason=<first line of the reason>` and `platform=<os>/<python>`.
4. `verify-bundle` catches any unexpected exception, writes an `ERROR` `verdict.json` with the exception type and
   message, prints the summary line and exits non-zero (no traceback-only exit).
5. Tests: bytes from `TimeoutExpired` (stub runner raising it), timeout → ERROR with that reason, summary line includes
   the reason, unexpected exception → ERROR verdict written.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
