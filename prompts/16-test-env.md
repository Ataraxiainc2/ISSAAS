# Prompt 16 — target test environments and honest infrastructure errors

Read `AGENTS.md`. Scope: `workspace.py`, `verifier.py`, `pipeline.py`, `cli.py`, tests. No network in tests.

Facts from `issa reverify 20261102-223630-ansible-ansible-runner-1548` (run by the user on 2026-11-02):
- The workspace venv had only `pip install -e .`; every pytest call failed with `No module named pytest` (exit 1).
- The verifier still reported `ERROR` with reason **"test still fails after patch"**, which blames the fix for an
  environment problem. The baseline run failed the same way and was silently treated as having no failures.
- ansible-runner keeps its test dependencies in `test/requirements.txt` (pytest, pytest-mock, pytest-timeout,
  pytest-xdist, …), referenced from `tox.ini` (`deps = -r {toxinidir}/test/requirements.txt`). Its `pytest.ini` uses
  `-Werror` and `--strict-markers`.

1. **Test dependencies:** after the editable install, install the first test requirements found, in this order:
   `pyproject` optional dependency groups or `[dependency-groups]` named `test`/`tests`/`testing`/`dev`;
   `requirements-test*.txt`, `requirements-dev*.txt`, `test/requirements*.txt`, `tests/requirements*.txt`; `-r` lines in
   `tox.ini` `[testenv] deps`. Always make sure `pytest` itself is present. Never use tox `deps` lines that are URLs or VCS
   archives. Log every command and exit code in `install.log`, as today.
2. **Infrastructure detection:** before Fail-to-Pass, check that the target venv can import pytest and collect the test
   file (`pytest --collect-only -q <test_file>`). If not, verdict `ERROR` with a reason that names the problem
   (e.g. "test environment incomplete: No module named pytest"), never "test still fails". Also treat pytest exit codes
   2–5 (interrupted, internal, usage, no tests) and a baseline run that produced no JUnit file as infrastructure errors.
3. **`issa reverify <run_id> --setup`:** re-runs step 1 on the existing workspace before verifying, so old runs can be
   fixed without a new solve.
4. **Tests:** dependency discovery for each source kind (temp project fixtures; installation stubbed), URL/VCS deps
   skipped, missing pytest → ERROR with the right reason, exit codes 2–5 → ERROR, missing baseline JUnit → ERROR,
   `--setup` calls setup before verify.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
