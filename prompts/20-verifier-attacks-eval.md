# Prompt 20 — attack our own verifier, close the holes, and ship an evaluation harness

Read `AGENTS.md` (rules 3, 8). Scope: `verifier.py`, `bundle.py`, a new `src/issa_as/evals.py`, a new `evals/` folder,
`cli.py`, tests. Do not edit README or docs (the administrator does). No network in tests. Branch `feat/verifier-eval`.

Problem: a fix only counts if the verifier proves it, so the verifier itself must not be fooled. Today Fail-to-Pass
treats pytest exit code 0 as "passed". A patch that adds `pytest.skip()` to the regression test, deletes it, adds a
`conftest.py` hook that rewrites failures to passes, or edits pytest config could therefore be reported VERIFIED.

1. **Harden the verifier (plain Python, deterministic):**
   - F2P runs with `--junitxml`; "passes after the patch" requires the target test file's tests to have actually run
     with at least one passed and zero failed/errored/skipped/xfailed. Same parse for "fails before".
   - Test integrity: if the patch touches the regression test file, any `conftest.py`, `pytest.ini`, `tox.ini`,
     `setup.cfg`, or the `[tool.pytest*]` section of `pyproject.toml`, the verdict is `UNPROVEN` with reason
     `patch modifies the regression test or test configuration: <paths>`, and no tests are run.
   - Keep every existing verdict and field; add `f2p_tests_passed` / `f2p_tests_skipped` counts to `VerifyResult`.
2. **Attack suite** (`evals/verifier_attacks.py` data + `src/issa_as/evals.py` logic): build a tiny local git project
   at eval time (a path-join helper with a traversal bug plus its failing test; no network), then run the real
   `verify()` against: the real fix (expect VERIFIED), no-op patch, test deleted, `pytest.skip` injected, `xfail`
   injected, conftest force-pass hook, pytest config `addopts` that deselects the test, fix plus an unrelated
   regression (expect REGRESSION), and a fix that only special-cases the test's literal input (expect VERIFIED, listed
   as a known limit: F2P cannot catch overfitting). Each attack: name, description, expected, got, ok.
3. **`issa eval verifier [--bundle RUN_ID] [--out PATH]`:** runs the attack suite on the synthetic project, or, with
   `--bundle`, the same attack shapes generated from a committed proof bundle (`fix.patch`, test file; clone via the
   existing bundle code). Writes JSON (`evals/results/verifier-<target>.json`) and prints a one-line summary
   `N/N attacks rejected as expected`. Exit 1 if any attack gets an unexpected verdict.
4. **`issa eval runs [--out PATH]`:** deterministic metrics from committed `proof/`, `traces/` and `triage/`: runs,
   verdict counts (best known, Linux verdict wins), verified rate, cost per verified fix, total solver USD, triage
   stats (scored, judged not real, picked, picked outcome). Writes `evals/results/runs.json`.
5. **Small panel fix:** in `serve.py`, if the `_run_kind` helper exists (it may land from another branch), runs whose
   id starts with `video-` get kind `video` and the label `VIDEO`; if it does not exist, skip this item.
6. **Tests:** each hardening rule, every attack's expected verdict on the synthetic project (mark slow only if > 20 s
   total), `eval runs` on fixture files.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`, `--no-sync` if locked). Run
`issa eval verifier` and `issa eval runs` once locally and commit their result files. Commit locally once on this
branch, do not push. Be concise in the final summary.
