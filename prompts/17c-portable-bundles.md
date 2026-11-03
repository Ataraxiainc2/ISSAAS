# Prompt 17c — portable bundles, Fail-to-Pass first, scoped regression suite

Read `AGENTS.md`. Scope: `verifier.py`, `bundle.py`, `pipeline.py`, `solver.py` (only where the test command is stored),
tests. No network in tests.

Facts from Linux run 41557110526 (ubuntu, Python 3.13.17):
- keynote-parser#91: `ERROR` "test command did not complete on base: … keynote-parser/.venv/Scripts/python.exe -m
  pytest …" — the bundle stores a Windows interpreter path, which does not exist on Linux (`.venv/bin/python`).
- ansible-runner#1548: `ERROR` "suite command timed out after 1200 s". The baseline suite runs before Fail-to-Pass, so
  the fix was never tested; ansible-runner's full suite includes slow integration tests.

1. **Portable test command:** `verify-bundle` (and `reverify`) rebuild the interpreter for the current OS from the
   workspace venv (`bin/python` or `Scripts/python.exe`); never reuse an interpreter path from the bundle. New bundles
   store the test command as `python -m pytest …` (interpreter-neutral).
2. **Fail-to-Pass first:** run it before any suite run, and keep `fail_before`/`pass_after`/`f2p_pass` in `verdict.json`
   even if a later suite run errors or times out (verdict stays `ERROR`, reason names the suite step).
3. **Scoped regression suite:** `--suite-path <dir>`. Default rule, deterministic and documented: if the test file is
   under a directory named `unit` (e.g. `test/unit/...`), the regression suite is that `unit` directory; otherwise
   the full suite. Record `suite_scope` in `verdict.json` and in the summary line.
4. **Tests:** Windows-style stored command rebuilt to the POSIX venv path and vice versa, Fail-to-Pass recorded when
   the suite times out, the `unit` scoping rule, `--suite-path` override, `suite_scope` in the summary.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
