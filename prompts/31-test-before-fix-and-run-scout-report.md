# Prompt 31 — the regression test must run against the unfixed code; `issa run` saves its scout report

Read `AGENTS.md`. Scope: `src/issa_as/solver.py` (the solver prompt text only), `src/issa_as/run.py`, and their tests.
Do not touch `verifier.py`. No network in tests. Stay on the current branch.

1. **Solver prompt (step 2, `solver.py` ~line 229).** Live run `20261104-181531-Netw0rkNoob-VulnClaw-310` ended ERROR:
   the new test imported `build_stream_findings`, a helper the fix itself added, so on the base commit the test
   failed at import (collection error) instead of failing an assertion. The verifier correctly refuses that as
   Fail-to-Pass. Add to step 2, in plain words: the regression test must import only names that already exist
   before the fix and must exercise the existing public behavior the issue describes (for example call the existing
   function or CLI path that crashes); it must fail on the unfixed code with an assertion or the reported exception,
   never with an ImportError/collection error. Commit the test **before** writing any new helper. A test-only stub or
   fake is fine. Add a test that the prompt contains this rule.
2. **`issa run` saves its scout report.** Today `run.run()` keeps the candidates only in the trace, so
   `issa brief <run_id>` and the panel's "Explain candidates" cannot find `scout/<run_id>.json`. Write the same scout
   report `issa scout` writes (including `guided`, `proposed_queries`, `github_authenticated`, `matched_query`) to
   `scout/<run_id>.json` right after discovery, under the `scout_dir` the function already receives (tests use
   `tmp_path`). Test it.

`.tools/uv.exe run --no-sync pytest -m "not slow"` and `.tools/uv.exe run --no-sync ruff check src tests` must pass.
One local commit: `solver: regression test must run on unfixed code; run: save scout report`. Do not push.
Short final summary.
