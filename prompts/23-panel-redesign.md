# Prompt 23 — panel redesign for live demos

Read `AGENTS.md`. Scope: `web/index.html`, `web/styles.css`, `web/app.js`, new `web/logo.svg`, `src/issa_as/serve.py`
(only `_STATIC_FILES`), `tests/test_serve.py`. Do not touch README, docs, `video/`, `proof/` or any JSON API shape.
No network, no external fonts, scripts, CDNs or images: everything is served from `web/`. Stay on the current branch.

Goal: the local panel (`issa serve`) is recorded for the demo video at a 1280×720 CSS viewport. Today it is a narrow
760 px column of default form controls, raw `JSON.stringify` output and long run ids. Make it look like a calm,
modern product that reads well on video, without changing what it does.

## Look

1. Dark theme, defined as CSS custom properties on `:root`: background `#0b1020`, surfaces `#121a2e` / `#18223a`,
   lines `#26314d`, text `#e8ecf6`, muted `#9aa6c2`, accent teal `#2dd4bf`, blue `#60a5fa`, ok `#34d399`, warn
   `#fbbf24`, bad `#f87171`. System font stack only (`"Segoe UI", system-ui, sans-serif`; mono `"Cascadia Mono",
   Consolas, monospace`), base 16px, `font-variant-numeric: tabular-nums` for numbers. Visible focus rings; honour
   `prefers-reduced-motion`.
2. Layout up to 1200 px wide, cards with 12 px radius and generous padding; single column below 760 px.
3. Header: the logo (copy `video/assets/issa-as-logo.svg` to `web/logo.svg` and serve it as `image/svg+xml`), "ISSA AS",
   one short subtitle ("Find · fix · prove — local operator panel"), and the budget as a meter: "$X spent of $150"
   with a thin progress bar and "remaining $Y".
4. Navigation: keep the five `data-tab` buttons and their values, but show them as a numbered stepper: "Runs",
   "1 Scout", "2 Triage", "3 Decision & result", "Actions". Hash routing: `#runs`, `#scout/<id>`, `#triage/<id>`,
   `#detail/<run_id>`, `#actions` open that tab and report; the select boxes and the hash stay in sync.

## Views (same data, readable)

5. **Runs:** a row of KPI cards above the table: verified fixes, of which Linux-verified, triage runs, total spend.
   Filter chips: All / Fixes / Triage / Scout (default Fixes). Each row shows a readable name ("ansible/ansible-runner
   #1548", parsed from the run id when it matches `<date>-<time>-<owner>-<repo>-<n>`; fall back to the raw id), the
   date, verdict pill, Linux and CI chips, cost, a chain check mark. The raw run id stays visible in small mono text.
6. **Scout:** candidate cards: repo and issue title as the heading, chips for stars, runner, security class, score;
   the signals as small key/value chips instead of a JSON dump.
7. **Triage:** one card per scored candidate, the picked one first with a "PICKED" badge and an accent border. Show
   four labelled horizontal bars (0–1 or 0–10 as the data gives): signals score, Spark solvability, critic-adjusted,
   final score. "Real bug" yes/no pill, model and provider under the bars. Rationales visible; reasoning excerpts in a
   closed `<details>`.
8. **Decision & result:** a verdict hero at the top (large VERIFIED / ABSTAIN / ERROR pill, Linux CI chip, cost, chain
   ok). A fail-to-pass strip: "FAIL on base" (red) → "PASS with fix" (green), with the test path. Model calls as a table
   with a totals row (tokens in / out / reasoning, cost). `fix.patch` as a code block with `+` lines green and `-` lines
   red (one span per line, built with `textContent`). `verdict.json` and `cost.json` in closed `<details>`.
9. **Actions:** forms in a two-column grid of cards with proper styled inputs and buttons (primary for Estimate/Start,
   accent for Confirm). Render the estimate as a short summary (model, provider, candidates, estimated tokens,
   estimated cost, or "unpriced (Baseten credits)" when `price_known` is false). Never print the `confirm_token`.
   Job progress: a terminal-styled log (mono, dark, auto-scroll to the end), a status pill (running with a subtle
   pulse / done / error) and elapsed seconds; the final result as a key/value summary, not raw JSON. Jobs that finish
   refresh Runs (as today).

## Must stay true

10. All server text is still inserted with `textContent` / `createTextNode` (issue and model text is untrusted); only
    `http(s)` URLs become links; no `innerHTML` with data. Same endpoints, same request bodies, same confirm-token
    flow, same element ids that exist today (add new ones freely).
11. Tests: `web/logo.svg` is served with `image/svg+xml`; an unknown path is still 404; existing serve tests keep
    passing. Add a light static check that `web/app.js` contains no `innerHTML` assignment.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`, `--no-sync` if locked). Commit
locally once, do not push. Be concise in the final summary.
