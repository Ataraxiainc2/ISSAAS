# Prompt 15 — `issa serve`: the local web control panel

Read `AGENTS.md`, `README.md` and `docs/ARCHITECTURE.md`. Build `web/` (static HTML/CSS/JS, no build step, no framework,
no CDN) and **`issa serve [--port 8780]`**, which serves it on `127.0.0.1` only. English UI. Tests stay offline.
The public site is a separate presentation page; this panel is for the operator's own machine.

## Views (read from local `scout/`, `triage/`, `traces/`, `proof/`)
- **Runs:** every run with its verdict, cost and hash-chain status, plus total spend against the $150 budget.
- **Scout:** candidates, repo, stars, runner, security class, deterministic signals.
- **Triage + critic:** deterministic score, Muse Spark Open solvability, critic adjustment, final score, `is_real_bug`,
  rationale, model/provider of each number and its reasoning excerpt.
- **Decision:** the pick and why, solver model/effort and `model_rationale`, skipped issues with reasons.
- **Work and verification:** trace timeline (repro_result → fix_attempt → verify_verdict), Fail-to-Pass as FAIL on base →
  PASS with fix, regressions, `baseline_failures`, tokens and USD per model call.
- **Result:** verdict badge, regression test, `fix.patch` as a diff, root cause; `ERROR`/`ABSTAIN`/`PENDING` shown as such.

## Actions panel
*Scout*, *Triage* (dry-run estimate first), *Solve selected* (model, effort, step cap, estimated USD), *Recover*,
*Reverify*, *Run full loop* (`max-usd` field). Each paid action needs a confirm token returned by its estimate request and
checked on the server, not only in the UI. Actions call the existing CLI functions in one background worker, one at a
time, and stream progress to the page (SSE or polling).

## Security
Bind `127.0.0.1` only. Reject requests whose `Host` or `Origin` is not localhost (DNS-rebinding/CSRF guard). Actions are
POST only. Validate run ids. Insert issue and model text with `textContent` only (it is untrusted). Never return `.env`
values or keys.

## Selection
Escalated recommendations (`muse-spark-1.3`, effort `high`) carry `recommended_max_usd = 1.20` (the ansible-runner#1548
fix took 59 steps and $1.06; the $0.60 default now allows ≈ 33 steps). `issa run` and the panel use it; document the rule.

## Tests
Binds 127.0.0.1, rejects a foreign Host/Origin, paid actions refuse without a valid confirm token, one-at-a-time lock,
run-id validation, `recommended_max_usd` rule, JSON endpoints contain no secrets.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
