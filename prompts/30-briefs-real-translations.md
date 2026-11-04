# Prompt 30 — briefs must really come in English, Spanish and Portuguese

Read `AGENTS.md`. Scope: `src/issa_as/briefs.py` and `tests/test_briefs.py` (plus `web/app.js` only if item 3 needs
it). No network in tests. Stay on the current branch.

Live evidence: `scout/run-20261104-173437.briefs.json` has 25 briefs (5 from Chinese issues) and **all 25** have
`es` and `pt` identical to `en`. Spark Open answered in English only (3,293 output tokens for 25 issues is about one
language's worth) and the validator silently filled `es`/`pt` with English.

1. **Two-pass design.** Pass 1 (as today, Muse Spark Open, `effort="low"`): English only — `title`, `problem`,
   `fix_idea`, `source_language`. Pass 2: one batched call that translates the validated English fields into Spanish
   and Portuguese (same provider path; give it enough `max_tokens` for 2 languages × 3 fields × N issues; retry once
   with a larger budget on `length`, like triage). Both calls are logged with tokens and cost and counted in the
   `candidates_briefed` event (add `translated: true/false` and per-language counts).
2. **Honest fallback.** If pass 2 fails or a language is missing for an issue, store `es`/`pt` as missing (not a copy of
   English) and record `missing_languages`. The API and panel then show the English text with a small
   "(English — translation unavailable)" note instead of pretending it is translated.
3. Show the prompt an example of the exact JSON shape with nested language objects.
4. Tests (offline, `FakeProvider`): pass 2 output fills `es`/`pt`; a pass-2 failure leaves them missing with
   `missing_languages`; an English-only pass-2 answer (es == en for every field) is treated as missing; the panel data
   still renders.
5. Do not run any live model call; the administrator runs `issa brief` after auditing.

`.tools/uv.exe run --no-sync pytest -m "not slow"` and `.tools/uv.exe run --no-sync ruff check src tests` must pass.
One local commit: `briefs: real ES/PT translations in a second pass; honest fallback`. Do not push.
Short final summary.
