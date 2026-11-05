# Prompt 35 — Pass-to-Pass preservation tests in the verifier

Read `AGENTS.md`. Work only in this repository (`src/issa_as/`, `tests/`). No network, no push, no GitHub writes,
no new dependencies. Deterministic logic only — no LLM calls in the verifier.

## Why

Upstream PR QWED#586 was `VERIFIED` by ISSA AS (Fail-to-Pass plus a green full suite), yet outside reviewers found
three regressions: behaviour the patch changed that no existing test covered. A green suite only protects behaviour
that some test already pins down. The fix: the solver must also commit **preservation tests** (Pass-to-Pass). These
pin down existing behaviour next to the change, and the verifier proves they pass both before and after the patch.

## Changes

1. `src/issa_as/verifier.py`, `verify()`: new keyword-only argument `preserve_file: Path | str | None = None`
   (repo-relative path, like `test_file`). When it is `None` or blank, behaviour is exactly what it is today.
   When it is given:
   - **Integrity:** if the patch touches `preserve_file`, the verdict is `UNPROVEN` with reason
     `patch modifies the preservation tests: <path>`, and no test runs. Reuse `_test_integrity_violations`, or
     follow its pattern.
   - **Path:** a path that escapes the repo is `ERROR`, like `test_file`.
   - **Base:** on the base commit, right after the base Fail-to-Pass run and before the patch is applied, run
     `preserve_file` alone. Build the command the way `_f2p_cmd` / `_f2p_cmd_with_junit` / `_iso` build the F2P
     run, and parse it with `_parse_f2p_junit`.
     - It must be clean: at least one passed, and zero failed / errored / skipped (`_patched_clean`).
     - A missing file is `ERROR` (`preservation file not found: <path>`).
     - A run that is not clean is `UNPROVEN` with reason `preservation tests do not pass on base: <counts>`. Tests
       that fail before the fix do not describe existing behaviour.
   - **Patched:** after the patched Fail-to-Pass run passes, and before the suite runs, run `preserve_file` the
     same way with the patch applied.
     - Not clean → `REGRESSION` with reason `preservation tests pass on base but fail after patch: <counts>`,
       and `regressions` = failed + errored count (at least 1).
     - Timeouts and infra exit codes follow the existing F2P handling.
   - **`VerifyResult` fields:** add `p2p_checked: bool = False` (True only when both preservation runs happened and
     were clean) and `p2p_tests_passed: int = 0` (passed count of the patched preservation run). Thread them
     through `decided()` the same way `f2p_tests_passed` is threaded.
   - **Docstrings:** update the module docstring and the `verify()` docstring.

2. `src/issa_as/solver.py`:
   - **`OUTPUT_SCHEMA`:** add `"preserve_file": {"type": ["string", "null"]}`, also listed in `required`; the schema
     is strict.
   - **`SolverResult`:** add `preserve_file: str = ""`.
   - **Parsing:** parse the new field with `_opt_str`, like `test_file`. A missing or null value means no
     preservation file. Older answers without the key must still parse, because `_matches_schema` is lenient.
   - **Solver prompt, step 2:** after the regression test, also write a separate preservation test file. It holds
     a few tests (aim for 3–8) that pin down existing behaviour of the code the fix will touch: neighbouring inputs,
     other branches of the same function, and behaviour that must not change. They must PASS on the unfixed code.
     Commit the file together with the regression test in the same 'test: add regression test' commit, and report
     its path in `preserve_file`. Say why: the verifier runs it before and after the fix, and any change is a
     regression.

3. `src/issa_as/pipeline.py`:
   - Pass `preserve_file=solver.preserve_file or None` to `verify_mod.verify` where the solver result is available.
   - Pass the stored value at the bundle re-verification call: read `preserve_file` from the stored verdict
     document, defaulting to `None`.
   - Write `preserve_file`, `p2p_checked` and `p2p_tests_passed` into the verdict JSON in both places that write
     `f2p_pass` / `suite_scope`.
   - Do the same in `src/issa_as/bundle.py` where `f2p_tests_passed` is written.
   - Existing bundles without these keys must still load and re-verify unchanged.

4. **Tests.** Add them to `tests/test_verifier.py`, using the same real temporary git repo fixtures the existing
   F2P tests use. Each test needs a base commit containing the regression test and a preservation file, and a
   patch.
   - Preservation file passes on base and after the patch → `VERIFIED`, `p2p_checked` True,
     `p2p_tests_passed` ≥ 1.
   - Patch breaks a preservation test (one that passes on base) → `REGRESSION` with the reason above,
     `p2p_checked` False.
   - Preservation test already fails on base → `UNPROVEN`.
   - Patch edits the preservation file → `UNPROVEN`, and no test runs.
   - `preserve_file=None` → identical result to today (`p2p_checked` False).
   - `tests/test_solve.py` (or the existing solver tests): the schema has `preserve_file`, a payload with it parses
     into `SolverResult.preserve_file`, and a payload without it still parses.

## Run

From the repo root:
- `uv run pytest -q`: everything must pass.
- `uv run ruff check src tests`.

Do not touch `proof/`, `evals/` or `docs/`.

One local commit: `verifier: pass-to-pass preservation tests (solver writes them, verifier proves them)`. Do not push.
Short final summary listing the new tests.
