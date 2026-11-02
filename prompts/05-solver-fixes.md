# Prompt 05 — Audit-04 corrections to the solver

Read `AGENTS.md` and audit entry 04 in `docs/AUDIT_LOG.md`. Scope: `solver.py`, `workspace.py`, their tests, and the
safety section of `docs/ARCHITECTURE.md`. Nothing else.

1. `build_command()` must pass `--model <model>` and `--reasoning-effort <effort>` (accept `minimal|low|medium|high|xhigh`)
   and `--approval-mode never`. Do **not** pass `--trust-workspace`; add a code comment and an ARCHITECTURE.md line
   explaining that this keeps a target repo's own agent rules from steering the solver.
2. Price each solver run from the model name reported in Muse's `run.usage` event (fall back to the requested model),
   including cached input tokens.
3. Add `--model` to `issa solve` (default `muse-spark-1.3-contributor`) so cheap and strong runs can be compared.
4. `workspace.py`: run dependency installs with the sanitized env and timeouts already used by the verifier, and copy
   the install log into the proof bundle (`install.log`). Add a "Known risk: dependency install runs third-party build code
   outside the OS sandbox" note with the mitigations to ARCHITECTURE.md.
5. Tests: assert the exact command line (model, effort, approval mode, no trust flag) and pricing from a usage event.

`uv run pytest` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
End with files changed and test results.
