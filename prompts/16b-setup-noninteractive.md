# Prompt 16b — `reverify --setup` must never wait on a prompt

Read `AGENTS.md`. Scope: `workspace.py`, `verifier.py`, `pipeline.py`, tests. No network in tests.

Fact (user run of `issa reverify 20261102-223630-ansible-ansible-runner-1548 --setup`, 2026-11-02): the process sat
silent for minutes and wrote nothing. `setup_venv` runs `uv venv .venv` on a workspace whose `.venv` already exists;
uv 0.13 then asks interactively whether to replace it. Output is captured and stdin is inherited from the user's
terminal, so the question is invisible and the process waits.

1. `setup_venv`: use `uv venv .venv --allow-existing` so an existing venv is reused, never replaced or prompted for.
2. Every subprocess ISSA AS starts for a target repo (venv, installs, git, pytest, verifier) gets `stdin=DEVNULL`, so
   nothing can wait for input; keep the existing timeouts.
3. `reverify --setup`: write the setup log to `install.log` even when setup fails, print one progress line per step
   to stderr (creating venv, installing <source>, verifying), and report a failed setup as a clear CLI error.
4. Tests: `--allow-existing` in the venv command, `stdin=subprocess.DEVNULL` on every runner call (stub runner records
   kwargs), install.log written when setup raises.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
