# Prompt 32 — third-party code runs with a clean, allowlisted environment

Read `AGENTS.md`. Scope: `src/issa_as/verifier.py`, `src/issa_as/workspace.py` (and any other module that runs
target-repo commands: venv creation, dependency install, test runs, `verify-bundle`, `reverify`), and their tests.
Do not change the `muse exec` environment in `solver.py` (Muse needs its own configuration). No network in tests.
Stay on the current branch.

Evidence: the clean-room rebuild of QWED#535 on Windows reported `1 passed, 87 skipped, 5372 errors` for the full
suite, so "0 regressions" meant almost nothing. Cause: the operator's machine has `DATABASE_URL` (Postgres) set, the
verifier passes the whole `os.environ` to the target's pytest, and QWED's default (`sqlite:///./qwed.db`) was
overridden. Worse, `issa` loads `.env` into `os.environ`, so Meta/Baseten keys were visible to untrusted third-party
test and install code.

0. **First, two non-hermetic tests.** On this machine `tests/test_solve.py::test_pipeline_run_started_caps_and_workspace_ready`
   (`assert 60 == 30`: contributor/medium got 60 steps) and `tests/test_run.py::test_run_end_to_end_verified`
   (`'muse-spark-1.3' == 'muse-spark-1.3-contributor'`) fail at HEAD and at `cdf912d`, but the same suite passed at
   13:05 today with no source change in between. Find what outside the code they depend on (wall-clock date, real
   repository files such as `proof/`, `scout/`, `triage/` or `traces/`, `.cache`, test order) and make them
   hermetic. Fix the test or the code, whichever is wrong: `default_max_steps` gives the contributor model at least
   `CONTRIBUTOR_MIN_STEPS` by design (Prompt 25), so a test expecting 30 for contributor/medium may simply be stale.
   Explain the cause in one line in the final summary.
1. **One helper, `target_env(extra=None) -> dict[str, str]`,** used for every subprocess that runs target-repo code
   (venv creation, `pip`/`uv` install, test commands, import checks). Start from an allowlist of `os.environ` keys
   only: `PATH`, `SYSTEMROOT`, `SYSTEMDRIVE`, `WINDIR`, `COMSPEC`, `PATHEXT`, `TEMP`, `TMP`, `TMPDIR`, `HOME`,
   `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `PROGRAMDATA`, `LANG`, `LC_ALL`, `TZ`, `NUMBER_OF_PROCESSORS`,
   `PROCESSOR_ARCHITECTURE`, `OS`, `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `PIP_CACHE_DIR`, `VIRTUAL_ENV` (only the
   workspace one), `CI`. Then set `PYTHONDONTWRITEBYTECODE=1`, `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`. Everything
   else is dropped, including every `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `DATABASE_URL`, `GITHUB_*`,
   `BASETEN_*`, `META_*`, `MUSE_*`, `LLAMA_*`, `OPENAI_*`, `AWS_*`, `GOOGLE_*`, `AZURE_*`. Record in `verify.log` the
   list of variable **names** passed (never values).
2. Pass it explicitly as `env=` everywhere target code runs, including the injected runners used in tests, so a test
   can assert the keys are absent.
3. Tests (offline): with `DATABASE_URL`, `META_API_KEY`, `BASETEN_API_KEY` and `GITHUB_TOKEN` set in the parent env,
   the env passed to install and test commands contains none of them and keeps `PATH`/`SYSTEMROOT`; the names line in
   `verify.log` lists no secret names.

`.tools/uv.exe run --no-sync pytest -m "not slow"` and `.tools/uv.exe run --no-sync ruff check src tests` must pass.
One local commit: `verifier: run third-party code with an allowlisted environment (no keys, no host DB URL)`.
Do not push. Short final summary.
