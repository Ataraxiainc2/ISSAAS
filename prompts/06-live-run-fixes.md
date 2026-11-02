# Prompt 06 — Fixes from the first live run

Read `AGENTS.md` and audit entry 06 in `docs/AUDIT_LOG.md`. Scope: `solver.py`, `pipeline.py`, their tests. Keep it lean.

The first live `issa solve` (psobot/keynote-parser#91) cloned the repo and installed dependencies correctly, then failed:
`muse exec failed to start: [WinError 2] The system cannot find the file specified`. On this Windows host `muse` is a
`muse.cmd` shim (`C:\Users\24\AppData\Local\Programs\muse\muse.cmd`), which `subprocess` cannot start by bare name.

1. **Resolve the Muse binary robustly:** env `MUSE_BIN` override (add to `.env.example`), else `shutil.which("muse")`.
   Fail with a clear error if neither is found.
2. **Pass the prompt via `--prompt-file`** (a file inside the run's `workspaces/<run_id>/` folder), not as a positional
   argument — long multi-line prompts are fragile through a `.cmd` shim.
3. **Infrastructure failures are not abstentions.** If Muse cannot start, times out, or crashes, the verdict is `ERROR`
   (with the reason), never `ABSTAIN`. `ABSTAIN` is reserved for the model honestly declining. This protects the
   abstention-accuracy metric.
4. **Trace consistency:** `run_started.caps` must reflect the caps actually used for the run (steps from `--max-steps` or
   the effort default). Add trace events for clone and install (`workspace_ready` with repo SHA and install exit codes).
5. **Live visibility:** stream the solver's `muse exec --json` stdout line by line into
   `muse-runs/solver-<run_id>.jsonl` while it runs, and write `muse-runs/solver-<run_id>.meta.json` with the fields
   `run, prompt_file, model, reasoning_effort, max_model_steps, disable_web_tools, command, started, finished, exit_code`
   (same format as `tools/console/run-muse.ps1`), so the administrator's console shows product runs live.
6. Tests: binary resolution (env override, which, missing), ERROR vs ABSTAIN classification, caps in `run_started`,
   streamed log + meta file written.

`uv run pytest` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
End with files changed and test results.
