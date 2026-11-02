# Prompt 15b — finish `issa serve`

Continue Prompt 15 (`prompts/15-serve.md`); its first session stopped at the step limit. Already done and uncommitted:
`recommended_max_usd` in `select.py`, its use in `run.py` and `issa solve --max-usd`, and `src/issa_as/serve.py`.
Do not rewrite those; review them only where the remaining work needs it.

Remaining:
1. Fix the `ruff` E501 long lines in `select.py` and `serve.py`.
2. Create `web/` (`index.html`, `app.js`, `styles.css`) with the views and the Actions panel described in Prompt 15
   (text from issues and models inserted with `textContent` only; works at phone width).
3. Wire `issa serve [--port 8780]` in `cli.py`.
4. `tests/test_serve.py` (offline): 127.0.0.1 bind, foreign Host/Origin rejected, paid actions refused without a valid
   token, busy lock (409), invalid run id (400), no secrets in JSON responses, `recommended_max_usd` rule.
5. Short README section "Local control panel" and the matching line in `docs/ARCHITECTURE.md`.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once (one commit
for all of Prompt 15), do not push. Be concise in the final summary.
