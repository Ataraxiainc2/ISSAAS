# Prompt 27 — guided discovery on by default, in the CLI and the operator panel

Read `AGENTS.md`. Scope: `src/issa_as/cli.py`, `src/issa_as/serve.py`, the panel front-end file(s) that `serve.py`
serves for the Actions tab, and their tests. Do not touch README, docs, `video/`, `proof/`, `traces/`, `scout/`,
`triage/` or `evals/results/`. No network in tests. Stay on the current branch.

Goal: the autonomous loop lets Muse Spark Open choose where to search without anyone passing a flag. The human only
approves paid actions in the panel and, later, any upstream publication.

1. **CLI.** `issa run` and `issa scout` use guided discovery by default. Keep `--guided` as an accepted no-op for
   compatibility and add `--no-guided` to fall back to the fixed queries. `--dry-run` still makes no model call.
2. **Panel "Run full loop".** The run estimate and action accept `guided` (default `true`), include it in the
   confirm-token params, and pass it to `run.run(...)`. The estimate response shows `guided` and adds the guided
   query proposal as one extra Spark Open call in the estimated calls.
3. **Panel "Scout".** Accept `guided` (default `true`). Because a guided scout makes one paid model call, a guided
   scout must go through the same estimate → confirm-token flow as the other paid actions; an unguided scout stays
   free and needs no token, as today.
4. **Front-end.** In the Actions tab add a checkbox "Let Muse Spark Open choose the searches (guided)", checked by
   default, for both Scout and Run full loop. In the Scout view, when a report has `guided: true`, show its
   `proposed_queries` (accepted ones with their `why`, rejected ones with the reason) and each candidate's
   `matched_query`. Old reports without these fields must still render.
5. Tests (offline): CLI defaults (`guided` true, `--no-guided` false); the run token carries `guided`; a guided
   scout without a confirm token is refused and an unguided one is not; the scout detail API returns
   `proposed_queries` when present and works without them.

`.tools/uv.exe run --no-sync pytest -m "not slow"` and `.tools/uv.exe run --no-sync ruff check src tests` must pass.
One local commit: `panel: guided discovery by default in run and scout`. Do not push. Keep the final summary short.
