# Prompt 17 — `issa verify-bundle` and Linux verification in GitHub Actions

Read `AGENTS.md` and audit entry 16 in `docs/AUDIT_LOG.md`. Scope: `pipeline.py` (or a new `bundle.py`), `cli.py`,
`.github/workflows/verify-bundles.yml`, README, tests. No network in tests.

Why: ansible-runner imports `fcntl`, so its tests cannot run on Windows (entry 16). A proof bundle must be checkable
on a clean Linux machine by anyone, from the committed repo alone, without Muse and without keys.

1. **`issa verify-bundle <run_id> [--out <dir>]`** — rebuild the run from `proof/<run_id>/` only:
   - repo from `issue_id`; `git clone https://github.com/<owner>/<repo>.git` into `workspaces/bundle-<run_id>/` and
     check out `test_parent` (the public upstream commit). Clone source overridable for tests (a local path).
   - copy the committed test file (`proof/<run_id>/test/<test_file>`) in place and commit it locally (a fixed local
     author via `git -c user.name=… -c user.email=…`; never touch global git config). That commit is the test commit.
   - set up the venv with the Prompt 16 dependency logic, then run the existing verifier with `fix.patch`
     (Fail-to-Pass on the test file, suite regressions against the baseline on `test_parent`).
   - write `verdict.json`, `verify.log`, `install.log` and a `platform` field (OS, Python version) to `--out`
     (default `workspaces/bundle-<run_id>/result/`). Never overwrite the original `proof/<run_id>/` files.
   - exit code 0 only for `VERIFIED`; print one summary line: run id, verdict, f2p, regressions, baseline failures.
2. **`.github/workflows/verify-bundles.yml`** — `workflow_dispatch` only, input `run_ids` (space-separated, default the
   keynote-parser and ansible-runner run ids). `ubuntu-latest`, `permissions: contents: read`, `actions/checkout` with
   `persist-credentials: false` (third-party test code must not see a token), `setup-python` 3.13 then `setup-uv`,
   `uv sync --frozen`, then `issa verify-bundle` per run id with `continue-on-error` per id, `timeout-minutes: 45`.
   Append each summary line and the `verdict.json` to `$GITHUB_STEP_SUMMARY`; upload each result dir as an artifact.
   No secrets are used.
3. **README:** a short "Verify a proof bundle on Linux" section (local command and the workflow).
4. **Tests (offline):** rebuild from a temp upstream git repo plus a fixture bundle (test file and patch), test commit
   created on `test_parent`, original proof files untouched, missing bundle fields refused, exit code non-zero for
   non-VERIFIED, workflow YAML has `contents: read`, `persist-credentials: false` and no `secrets.` references.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
