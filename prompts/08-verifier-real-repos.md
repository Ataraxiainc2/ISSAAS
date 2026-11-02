# Prompt 08 — Verifier fixes from live run #3 + `issa reverify`

Read `AGENTS.md` and audit entry 07 in `docs/AUDIT_LOG.md`. Scope: `verifier.py`, `pipeline.py`, `cli.py`, tests.

Live run #3 (psobot/keynote-parser#91) worked up to verification: Muse committed a regression test (`3886fd5`) and a fix
(`b8f16ac`) for $0.013. The verifier returned `ERROR` because it could not start
`.venv/Scripts/python.exe -m pytest tests/test_zip_slip.py tests/test_file_utils.py ...`.
The administrator checked by hand: the new test fails on the test commit and passes on the fix commit;
`tests/test_file_utils.py` has 2 failures and the full suite has 3 collection errors **that already exist on the original
commit** (missing generated file `protobuf_schemas.tar.xz`). So the correct verdict is `VERIFIED`.

1. **Relative executables:** on Windows, `subprocess` resolves a relative `argv[0]` against the parent's cwd, not `cwd=`.
   Resolve `argv[0]` against the repo when it contains a path separator (and accept `python`/`pytest` by mapping them to
   the workspace venv interpreter). Keep `shell=False`.
2. **Fail-to-Pass uses only the new test file.** Run the F2P check on `test_file` alone, whatever extra files the model's
   `test_command` lists.
3. **Regressions are relative to the baseline.** Run the existing suite on the original commit (`test_parent`) and on the
   fix commit, collect per-test outcomes (pytest `--junitxml` into the run folder), and count as regressions only tests
   that passed on the baseline and fail after the fix. Record baseline failures and collection errors in `verdict.json`
   as `baseline_failures` (not regressions).
4. **`issa reverify <run_id>`:** re-run verification on an existing run's workspace and commits without calling Muse;
   rewrite `verdict.json`/`verify.log` and append a new `verify_verdict` event to the same hash-chained trace.
5. Tests (offline, `tmp_path` repos): relative interpreter path, F2P isolated from a failing sibling test file,
   pre-existing failure not counted as regression, a real new failure counted, `reverify` on a stored run.

`uv run pytest` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push. Be brief.
