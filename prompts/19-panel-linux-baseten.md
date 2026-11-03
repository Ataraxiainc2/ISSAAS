# Prompt 19 — panel shows the Linux verdict; Baseten as the named Spark Open provider

Read `AGENTS.md`. Scope: `serve.py`, `web/`, `providers.py`, `budget.py` if needed, `.env.example`, tests. Do not edit
README or docs (the administrator does). No network in tests. You are on branch `feat/panel-linux-verdict`.

Facts: `proof/<run_id>/linux-verify.json` (fields `verdict`, `f2p_pass`, `regressions`, `suite_scope`, `ci_run`,
`platform`, `date`) records the keyless Linux CI rebuild. The panel ignores it, so ansible-runner#1548 shows the local
Windows `ERROR` although it is VERIFIED on Linux. Scout and triage runs show `PENDING`, which reads like an unfinished
fix.

1. **Panel verdicts:** `summarize_runs` / `run_detail` return `verdict` (best known), `local_verdict` (verdict.json or
   trace, as today) and `linux` (the linux-verify.json fields above, or null). Best known = the Linux verdict when
   present, else the local one. The runs table shows the best verdict with a small "Linux" tag and the CI link
   (`textContent` / `href` only, never innerHTML); the detail view shows both verdicts and the reason. Runs whose id
   starts with `scout-` or `triage-` get kind `scout` / `triage` and the label `SCOUT` / `TRIAGE`, not `PENDING`.
2. **Baseten:** when `SPARK_OPEN_API_KEY` is set and `SPARK_OPEN_BASE_URL` is empty, default the base URL to
   `https://inference.baseten.co/v1` (constant `BASETEN_BASE_URL`), and record `provider: "baseten"` (else `"custom"`)
   on Spark Open trace events. Update `.env.example` accordingly. No other behaviour change.
3. **Tests:** Linux verdict wins and both are returned; no linux file keeps today's behaviour; scout/triage labels;
   Baseten default and explicit override; provider name on the trace event.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`; use `--no-sync` if the venv is
locked). Commit locally once on this branch, do not push. Be concise in the final summary.
