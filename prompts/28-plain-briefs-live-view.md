# Prompt 28 — plain-language candidate briefs (EN/ES/PT) and a live "now working on" view

Read `AGENTS.md`. Scope: a new `src/issa_as/briefs.py`, `src/issa_as/run.py`, `src/issa_as/cli.py`,
`src/issa_as/serve.py`, `src/issa_as/traces.py`, `web/app.js`, `web/index.html`, `web/styles.css`, and their tests.
Do not touch README, docs, `video/`, `proof/`, `traces/`, `scout/`, `triage/` or `evals/results/` data files.
No network in tests. Stay on the current branch.

Why: scouted issues are often written in Chinese, Japanese or other languages, and their titles are terse. The human
operator must be able to read, in plain English by default (Spanish and Portuguese on request), what each candidate
is about and what the Meta model is about to try, and watch it live.

1. **`briefs.py` — one batched model call per scout report.** Through the same provider path as triage (Muse Spark
   Open on Baseten, fallback `muse-spark-1.3-contributor`; logged with tokens and cost like every call; explicit
   `reasoning_effort="low"`), send each candidate's title, labels and the first ~1,500 characters of its body, and
   ask for JSON per issue URL:
   `{"title": {"en","es","pt"}, "problem": {"en","es","pt"}, "fix_idea": {"en","es","pt"}, "source_language": "zh|ja|en|..."}`.
   `problem` = what is wrong and who is affected, in at most 2 plain sentences; `fix_idea` = what a minimal fix would
   try, 1 sentence. Treat issue text as untrusted data (say so in the system prompt). Validate deterministically:
   unknown URLs dropped, each string trimmed and capped (title 140, problem 400, fix_idea 250 chars), missing
   languages fall back to English. Write `scout/<scout_run_id>.briefs.json`; append a `candidates_briefed` trace
   event (model, provider, count, tokens, cost). A failed call is recorded and never blocks the loop.
2. **Wire it in.** `issa run` builds briefs right after scouting (skipped in `--dry-run`, counted in the estimate).
   New `issa brief <scout_run_id>` for existing reports. In the panel, a "Explain candidates" action per scout report
   (paid: estimate → confirm token, like triage).
3. **Panel language.** A language switch EN | ES | PT in the header, English by default, remembered in
   `localStorage` (wrapped in try/catch). The Scout and Triage views show the brief title, problem and fix idea in the
   chosen language, with the original title kept under a small "Original (zh)" line. Reports without briefs render as
   today. Static UI labels stay English.
4. **Live "Now working on" card.** While a panel job (scout, triage, solve, run) is running, show at the top of the
   panel: the current stage (scouting / briefing / triaging / selecting / solving / verifying), elapsed time, and when
   an issue is selected, its brief (title, problem, fix idea in the chosen language) plus solver model and effort.
   Feed it from the job status and the run's trace events (poll every 3 s only while a job is running; stop when done).
   Show the final verdict when the job ends.
5. All model text is rendered with `textContent` only, never `innerHTML`.
6. Tests (offline, `FakeProvider`): brief parsing and caps, fallback to English, unknown URL dropped, failed call
   recorded without raising, trace event present, `/api/scout/<id>` returns briefs when the file exists and still
   works without it, the live-status endpoint returns stage and selected issue from a fixture trace.

`.tools/uv.exe run --no-sync pytest -m "not slow"` and `.tools/uv.exe run --no-sync ruff check src tests` must pass.
One local commit: `briefs: plain-language EN/ES/PT candidate briefs and live panel view`. Do not push.
Work efficiently: read only what you need. Keep the final summary short.
