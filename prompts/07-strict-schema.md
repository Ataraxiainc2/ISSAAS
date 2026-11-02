# Prompt 07 — Strict output schema for the solver

Live run #2 on psobot/keynote-parser#91 started Muse correctly, then the Meta API rejected the run:
`API error 400: 'additionalProperties' is required to be supplied and to be false. (invalid_request_error)`.

Scope: `OUTPUT_SCHEMA` in `src/issa_as/solver.py`, the code that reads the solver answer, and tests. Nothing else.

1. Make `OUTPUT_SCHEMA` valid for strict structured output: `"additionalProperties": false`, and **every** property listed
   in `required`. Optional values become nullable (`"type": ["string", "null"]`, `["number", "null"]`); an abstaining answer
   sets the test/patch fields to null and fills `abstain_reason`.
2. Update the answer parsing so null fields behave exactly like missing fields did before.
3. Add a test that walks the schema and asserts strict-mode rules (additionalProperties false at every object level, all
   properties required).

`uv run pytest` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push. Be brief.
