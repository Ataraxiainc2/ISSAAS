# Prompt 29 — two bugs found in the live panel run

Read `AGENTS.md`. Scope: `src/issa_as/serve.py`, `src/issa_as/verifier.py`, `tests/test_briefs.py`,
`tests/test_serve.py`, `tests/test_verifier.py` (and `briefs.py` only if needed). Do item 4 first. No network in tests. Stay on the current branch.

1. **Confirm token rejected for whole-number budgets.** In the panel, "Run full loop" with Max USD `3` returns
   `403 paid action needs a valid confirm token`. Cause: the estimate binds the token to normalized params
   (`max_usd=float(3)` → `3.0`), but `_handle_action` binds the raw JSON body (`3`), so `_params_key` differs. Fix it
   at the source: one normalizer per paid action (run, solve, triage, brief, guided scout) used by **both** the
   estimate and the action, so the action checks the token against the same normalized params it then runs with.
   Tests: integer `max_usd` (3), float (2.5), and `limit` sent as a string all confirm; a changed value is still
   refused.
2. **Tests wrote into the real `scout/` folder.** Running the suite created `scout/run-capped.briefs.json`,
   `run-retry`, `run-skip`, `run-solved`, `run-tamper`. Every test must use `tmp_path` roots. Add a test that runs the
   brief-building path and asserts nothing new appears under the repository's `scout/`.
3. **Briefs files listed as scout reports.** `GET /api/scout` lists `*.briefs.json` as if they were scout reports
   (the panel then requests `/api/scout/run-capped.briefs`). Exclude `*.briefs.json` from the scout report list and
   test it.

4. **Verifier picks up ISSA AS's own pytest config (most important).** Live run `run-20261104-173437`
   (aerkalov/ebooklib#359) ended `ERROR: test still fails after patch`, but both runs failed in setup with
   `PermissionError ... workspaces\run-...\ebooklib\.cache\pytest-tmp`. ebooklib has no pytest config, and
   `workspaces/` sits inside this repo, so pytest walked up and used **our** `pyproject.toml`
   (`addopts = --basetemp=.cache/pytest-tmp`, rootdir `D:\SC\ISSAAS`). Fix in `verifier.py` (also used by
   `reverify` and `verify-bundle`): at execution time only (never stored in the bundle's `test_command`), every pytest
   run in a target repo gets `--rootdir=<repo>`, `-p no:cacheprovider`, a fresh unique `--basetemp` under
   `workspaces/<run_id>/.issa-tmp/`, and, when the repo has no pytest config of its own (`pytest.ini`, `pytest.toml`,
   `.pytest.ini`, `pyproject.toml` with `[tool.pytest.ini_options]`, `tox.ini` `[pytest]`, `setup.cfg`
   `[tool:pytest]`), `-c <workspaces/<run_id>/.issa-pytest.ini>` containing only `[pytest]`. Also: a fail-before that
   is only a setup/collection error (no test actually ran to an assertion) must not count as Fail-to-Pass evidence;
   keep the verdict honest (ERROR with a clear environment reason). Tests: a fake repo without config under a parent
   that has `addopts` is not affected by the parent's config; the flags never appear in the stored `test_command`.

`.tools/uv.exe run --no-sync pytest -m "not slow"` and `.tools/uv.exe run --no-sync ruff check src tests` must pass.
One local commit: `verifier: isolate target pytest config; panel: normalize confirm-token params; isolate brief tests`.
Do not push. Short final summary.
