# Prompt 33 — full regression suites get the suite timeout, not the 120 s command timeout

Read `AGENTS.md`. Scope: `src/issa_as/pipeline.py`, `src/issa_as/bundle.py`, `src/issa_as/cli.py`,
`src/issa_as/verifier.py` (only if needed), and their tests. No network in tests. Stay on the current branch.

Evidence: after Prompt 32 (allowlisted env), `issa reverify 20261104-030040-QWED-AI-qwed-verification-535` really runs
QWED's ~5,400-test suite for the first time and ends `ERROR: suite command timed out after 120 s`.
`verifier.DEFAULT_SUITE_TIMEOUT_S = 1200.0` exists, but `pipeline.reverify` passes only `timeout_s`
(`DEFAULT_TIMEOUT_S = 120`), so `verify()` falls back to it for the suite.

1. Every caller of `verifier.verify` that runs a regression suite (solve pipeline, `reverify`, `verify-bundle`,
   evals) passes `suite_timeout_s` explicitly, defaulting to `DEFAULT_SUITE_TIMEOUT_S`. The Fail-to-Pass test runs
   keep the 120 s timeout.
2. `issa reverify` and `issa verify-bundle` accept `--suite-timeout SECONDS` (positive float).
3. Tests: reverify and verify-bundle pass `DEFAULT_SUITE_TIMEOUT_S` by default and the CLI value when given; the
   solve pipeline passes it too.

`.tools/uv.exe run --no-sync pytest -m "not slow"` and `.tools/uv.exe run --no-sync ruff check src tests` must pass.
One local commit: `verifier: full suites use the suite timeout everywhere`. Do not push. Short final summary.
