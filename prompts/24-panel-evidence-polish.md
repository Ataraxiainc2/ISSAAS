# Prompt 24 — panel: show the best evidence, readable numbers

Read `AGENTS.md`. Scope: `web/app.js`, `web/styles.css`, `web/index.html`, `src/issa_as/serve.py`, `tests/test_serve.py`.
Do not touch README, docs, `video/`, `proof/` or `evals/results/`. No network in tests. Stay on the current branch.

The administrator reviewed Prompt 23's redesign at 1280×720. It looks good; these are the problems left.

1. **Decision & result shows the wrong evidence.** For `20261102-223630-ansible-ansible-runner-1548` the local
   (Windows) run ended in ERROR, but `proof/<run>/linux-verify.json` proves it on Linux (`f2p_pass: true`,
   `regressions: 0`). Today the fail-to-pass strip says "no failure on base → NOT demonstrated". Rule: the strip shows
   the best evidence. When `linux.f2p_pass` is true, show "FAIL on base → PASS with fix" with a green "Linux CI" chip,
   the suite scope and "0 regressions"; else the local result as today. The local result stays visible as one muted
   line, e.g. "Local run (Windows): ERROR — <first line of error.md or the local reason>".
2. **Empty rows.** Hide key/value rows whose value is missing instead of printing "—". When the trace has no repro or
   verify events, fill those fields from the proof bundle (`proof.verdict`: test path, fail-to-pass, regressions,
   patch hash). When there is no triage pick, say "Solved directly from the issue URL (no triage pick)".
3. **Linux in the hero:** a prominent green "Linux CI · VERIFIED" pill that links to `ci_run`, next to the verdict.
4. **Corrected cost.** Read the optional `evals/results/cost-reconciliation.json` (`{"runs": {run_id: {"cost_usd",
   "logged_usd", "basis"}}}`); when a run has an entry, the panel shows `cost_usd` with a small "incl. reasoning" chip
   and the logged value as muted secondary text (`basis` as the chip's `title`), in Runs and in Decision & result.
   Expose it through the existing JSON (add fields, rename none). A missing or malformed file is ignored.
5. **Numbers:** scores and signal values with 2 decimals; costs as `$1.23` (4 decimals only below $0.01, "$0" for
   zero); token counts with thousands separators or `4.65M` / `54.3k`. The overview KPI "Total spend" becomes
   "Runtime spend (ledger)" — it is the logged pipeline spend, not the whole project's.
6. **Links:** readable on the dark theme (`#60a5fa`, underline on hover); long URLs ellipsized on one line.
7. **Runs filter:** "Fixes" shows only `solve` runs, VERIFIED first, then newest; `video` and other kinds only under
   "All". The "local ERROR" chip on a run that Linux verified reads "Windows run: ERROR" in muted style.
8. Keep everything from Prompt 23 (textContent only, no `innerHTML`, no external resources, same endpoints and ids).
9. Tests: the best-evidence rule (Linux f2p overrides a local ERROR in the detail JSON or rendering inputs you add),
   reconciliation read and ignored when malformed, the "Fixes" filter excludes `video` runs (if the filter is
   server-side; if it is client-side, test the kind field it relies on).

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`, `--no-sync` if locked). Commit
locally once, do not push. Be concise in the final summary.
