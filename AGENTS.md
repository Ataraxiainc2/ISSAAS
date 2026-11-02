# AGENTS.md — rules for Muse Code in this repository

Project: **ISSA AS — Issue Scouting, Solving & Attestation, Agentic Solver**.
Built for the Meta Global AI Developer Hackathon 2026 (deadline: Wed Nov 4, 2026, 9:00 PM ET).
All code, comments, docs and commit messages are in **English**.

## Hard boundaries (non-negotiable)

1. **Stay inside `D:\SC\ISSAAS`.** Never read, write, delete or modify anything outside this folder.
   No global installs (`pip install --user`, `npm -g`, `uv tool install`, system PATH edits, registry, user profile files).
2. **All caches and environments stay in this folder.** Use `uv` only. `uv.toml` here pins the cache to `.cache/uv`.
   Virtual environments go in `.venv/` (ours) or `workspaces/<run>/.venv` (cloned target repos).
3. **Cloned third-party repositories go only under `workspaces/`** (git-ignored). Never run their code outside it.
4. **No GitHub writes.** Do not `git push`, do not run `gh` commands that create/modify anything, do not open issues, PRs or comments
   anywhere. Commit locally only; the administrator reviews and pushes at milestones.
5. **Never print, log, or commit secrets.** API keys come from environment variables only; `.env` is git-ignored; keep `.env.example` up to date.
6. **Budget awareness.** The project's total Meta API budget is $150. Every runtime model call must be logged with token counts and cost.

## Commit policy

- Commit locally when a module works and its tests pass — not after every small edit.
- Message format: `<area>: <what changed>` (e.g. `verifier: add fail-to-pass check`).

## Engineering conventions

- Python 3.13, `uv` for dependencies, `pytest` for tests, `ruff` for lint.
- Deterministic logic (verification, scoring math, budget accounting) is plain Python — never an LLM.
- Every pipeline stage appends events to a JSONL trace (`traces/<run_id>.jsonl`); the UI and the Keyless Trace Replay read only those traces.
- Prefer small modules with clear interfaces; keep the code readable for hackathon judges.
