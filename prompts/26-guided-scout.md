# Prompt 26 — the agent chooses where to look (guided scout), token never reaches Muse

Read `AGENTS.md`. Scope: `src/issa_as/scout.py`, `src/issa_as/providers.py` (only if a helper is needed),
`src/issa_as/solver.py`, `src/issa_as/cli.py`, `.env.example`, and their tests. Do not touch README, docs, `video/`,
`proof/`, `traces/`, `scout/`, `triage/` or `evals/results/`. No network in tests. Stay on the current branch.

Today the scout runs a fixed list of GitHub searches, so every run finds the same ~30 issues. We want the agent to
choose where to look, based on what ISSA AS has already shown it can solve, while deterministic code keeps control of
every GitHub request.

1. **`issa scout --guided`** (also `issa run --guided`). Before discovery, make one model call through the same
   provider path as triage (Muse Spark Open on Baseten, fallback `muse-spark-1.3-contributor`, logged with tokens and
   cost like every call). Input: a short summary of past outcomes read from `proof/*/` (issue title, security class,
   verdict, cost; at most 20 entries) and the list of allowed security classes. Output JSON:
   `{"queries": [{"keywords": "...", "class": "<security class or null>", "why": "..."}], "rationale": "..."}`,
   at most 5 queries.
2. **The model proposes keywords only; code builds and runs the query.** Validate each proposal deterministically:
   `keywords` is 1–60 chars of letters, digits, spaces, `-`, `_`, `.` and double quotes only; no GitHub qualifiers
   (reject anything containing `:`), so the model cannot target a repo, user or org. Code builds
   `type:issue state:open <keywords> <language qualifier>` and runs it through the existing cached, rate-limited client,
   then evaluates and scores candidates exactly as today. The fixed queries still run after the guided ones as a
   fill, within the same `--limit` and the same rate-limit early stop. Rejected proposals are recorded with the reason.
3. **Trace it.** Append a `scout_queries_proposed` event to the scout trace: model, accepted queries, rejected ones
   with reasons, the rationale, tokens and cost. The scout report JSON gains optional `guided` (bool) and
   `proposed_queries` fields; old reports still load. Each candidate keeps the query that found it
   (`matched_query`).
4. **Authenticated or not, never the value.** The scout report records `github_authenticated: true/false` from whether
   `GITHUB_TOKEN` is set. Never log, trace or print the token.
5. **The solver never passes a GitHub token to Muse Code.** In `solver.py`, build the `muse exec` child environment
   from `os.environ` minus `GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_PAT` and `GH_ENTERPRISE_TOKEN`. Pass it explicitly as
   `env=` (the injected test runner must receive it too, so a test can check that the keys are absent).
6. `.env.example`: document `GITHUB_TOKEN` as optional, read-only (a fine-grained token with public repositories
   read-only and no permissions is enough), used only by the deterministic scout, and never passed to any model.
7. Tests (offline, `FakeProvider`): valid proposals become queries with the language qualifier; proposals with `:`,
   too long, or bad characters are rejected with reasons; more than 5 are cut to 5; the guided run falls back to the
   fixed queries when the model call fails; the trace event exists and contains no token; the solver child
   environment has no GitHub token keys while other variables pass through.

8. Housekeeping: `.cache/pytest` is locked by an earlier sandbox (Access denied), so a plain `pytest` errors in
   setup. In `pyproject.toml` change `addopts` to `--basetemp=.cache/pytest-tmp`. Do not try to delete `.cache/pytest`.

`uv run pytest -m "not slow"` and `uv run ruff check src tests` must pass (uv at `.tools/uv.exe`, `--no-sync` if
locked). Commit locally once, do not push. Be concise in the final summary.
