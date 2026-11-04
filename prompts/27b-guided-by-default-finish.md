# Prompt 27b — finish Prompt 27 (guided by default in the panel)

Read `AGENTS.md` and `prompts/27-guided-by-default.md`. A previous session finished item 1 (CLI defaults) and the
estimate groundwork in `serve.py` (`estimate_run(guided=...)`, `estimate_scout()`); those changes are uncommitted in
the worktree. Keep them, do not rewrite them. Same scope and rules as Prompt 27.

Finish only what is still open:

1. `serve.py`: pass `guided` into `_run_scout` and `_run_full`; `estimate/run` reads `guided` (default true) and puts it
   in the confirm-token params; add `estimate/scout`. A guided scout action without a valid confirm token is refused;
   an unguided scout stays free. Update the existing busy-lock test to send `guided: false` if it needs to.
2. `web/app.js` (+ `index.html`/`styles.css` only if needed): the checkbox "Let Muse Spark Open choose the searches
   (guided)", checked by default, for Scout and Run full loop, going through estimate → Confirm when checked. In the
   Scout view, when a report has `guided: true`, show accepted `proposed_queries` with their `why`, rejected ones with
   the reason, and each candidate's `matched_query`. Old reports render as before. Use `textContent` only.
3. The tests from Prompt 27 item 5.
4. `.tools/uv.exe run --no-sync pytest -m "not slow"` and `.tools/uv.exe run --no-sync ruff check src tests` pass.
5. One local commit: `panel: guided discovery by default in run and scout`. Do not push.

Work efficiently: read only the parts of files you need (use search, not whole-file reads). Short final summary.
