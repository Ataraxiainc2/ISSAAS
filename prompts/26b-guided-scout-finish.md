# Prompt 26b — finish Prompt 26 (guided scout)

Read `AGENTS.md` and `prompts/26-guided-scout.md`. A previous session implemented items 1–4 of Prompt 26 in
`src/issa_as/traces.py`, `scout.py`, `cli.py` and `run.py`; those changes are uncommitted in the worktree. Keep them,
do not rewrite them. Same scope and rules as Prompt 26. No network in tests. Stay on the current branch.

Finish only what is still open, in this order:

1. `run.py`: pass `matched_query` into its `IssueDiscovered` events.
2. `solver.py`: add `solver_child_env(base=None) -> dict[str, str]` that copies `os.environ` (or `base`) minus
   `GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_PAT`, `GH_ENTERPRISE_TOKEN`. Pass it as `env=` to the real `muse exec` `Popen`
   and to the injected runner, so a test can assert the keys are absent and other variables pass through.
3. `.env.example`: document `GITHUB_TOKEN` (Prompt 26 item 6).
4. `pyproject.toml`: `addopts = "--basetemp=.cache/pytest-tmp"`. Do not touch `.cache/pytest`.
5. `tests/test_guided_scout.py` (offline, `FakeProvider`): the cases listed in Prompt 26 item 7.
6. `.tools/uv.exe run --no-sync pytest -m "not slow"` and `.tools/uv.exe run --no-sync ruff check src tests` pass.
   Fix only what you broke.
7. One local commit: `scout: guided queries proposed by Spark Open; strip GitHub token from Muse env`. Do not push.

Work efficiently: read only the files you need, avoid re-reading whole files, and keep the final summary short.
