# Prompt 17e — pytest plugins the target needs, per-test timeouts, one-line reasons

Read `AGENTS.md`. Scope: `workspace.py`, `verifier.py`, `bundle.py`, tests. No network in tests.

Facts from Linux run 41561890786 (ubuntu, Python 3.13.17):
- keynote-parser#91: `ERROR` "pytest cannot collect tests/test_zip_slip.py". Its `pyproject.toml` has
  `[tool.pytest.ini_options] addopts = "--cov=keynote_parser"`, which needs pytest-cov. Dependency discovery installed
  only the first source found (the `test` extra, `["coverage"]`) and skipped `requirements.txt`, which lists `pytest-cov`.
- ansible-runner#1548: **`f2p=true`** (the test fails on base and passes with the fix), but the `test/unit` regression
  suite ran past 1200 s. ansible-runner's `test/requirements.txt` installs pytest-timeout and pytest-xdist.
- The summary `reason` ends with the command line; the actual pytest error is cut off.

1. **Install every discovered source,** not only the first: pyproject test/tests/testing/dev extras or groups, then
   requirement files (`requirements-test*.txt`, `requirements-dev*.txt`, `test(s)/requirements*.txt`, and a plain
   `requirements.txt` when it mentions pytest), then `tox.ini` `-r` lines. URL/VCS lines stay skipped.
2. **Plugins required by the pytest config:** read `addopts` from `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`;
   `--cov*` → `pytest-cov`, `--timeout*` → `pytest-timeout`, `-n`/`--numprocesses` → `pytest-xdist`; install the missing ones.
3. **Suite runs only (not Fail-to-Pass):** if `pytest_timeout` is importable in the target venv, add `--timeout=120`
   (per test; a hanging test fails on its own and is compared against the baseline like any other); if `xdist` is
   importable, add `-n auto`. Record the added flags as `suite_flags` in `verdict.json`.
4. **One-line reasons:** `<problem>: <last non-empty pytest output line>` (e.g. "pytest cannot collect …: error:
   unrecognized arguments: --cov=keynote_parser"); the full output stays in `verify.log`.
5. **Tests:** all sources installed, addopts → plugin mapping for each config file, suite flags added only when the
   plugins import, reason contains the last pytest line.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
