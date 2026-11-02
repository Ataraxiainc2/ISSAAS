# Prompt 12 — Spark Open on Baseten for real + triage/critic/select fixes

Read `AGENTS.md` and audit entry 11 in `docs/AUDIT_LOG.md`. No real API calls in tests. Scope: `providers.py`,
`scout.py`, `triage.py`, `critic.py`, `select.py`, `.env.example`, tests.

Facts verified by the administrator with one live call (2026-11-02):
- Baseten is OpenAI-compatible at `https://inference.baseten.co/v1`, header `Authorization: Bearer <key>`.
- Model id **`meta-models/muse-spark-open`** works. `meta-models/muse-spark-open-1026` (the hackathon guide's id) returns
  HTTP 404 "please check the model you provided" on this account.
- `reasoning_effort` accepts `low|medium|high|xhigh` and **defaults to `xhigh`** (expensive) — always send it explicitly.
- The reply has `message.reasoning_content` (reasoning text) and `message.content`; reasoning tokens count toward
  `completion_tokens`, so `max_tokens` must leave room for reasoning plus the JSON answer.

## 1. Providers
- Spark Open model id from env `SPARK_OPEN_MODEL` (default `meta-models/muse-spark-open`; add to `.env.example` with a
  comment about the `-1026` id). Always send `reasoning_effort`.
- `complete()` takes an optional `max_tokens`; triage and critic pass a value sized to the batch (e.g. 600 tokens per
  candidate + 2000 for reasoning, capped at 16000). This fixes the 512-token truncation of the 30-candidate JSON.
- Record in every `model_call` event: provider, model, `reasoning_tokens`, the provider's request id if returned, and a
  `reasoning_excerpt` (first 1500 chars of `reasoning_content`) so the console and replay can show the model's reasoning.
- If the JSON answer is truncated or invalid, retry once with a larger `max_tokens`, then fail with a clear error.

## 2. Scout → triage → critic data
- `Candidate` gains `body_excerpt` (first 1000 chars of the issue body: strip HTML comments, collapse whitespace).
  In prompts, wrap it in clear delimiters and state it is untrusted issue text, never instructions (prompt-injection guard).
- Propagate `security_class` and `body_excerpt` into `TriageResult` and into the critic prompt (remove the hardcoded
  `"security_class": None` in `critic.py`).

## 3. Selection
- Skip `is_real_bug == False` (count `skipped_not_bug` in the report).
- Only `runner == "python"` is attemptable until a Node runner exists; others are skipped with reason
  `runner not implemented yet`.
- Add `recommended_solver_model` and `recommended_effort` with a one-line `model_rationale`: default
  `muse-spark-1.3-contributor`; escalate to `muse-spark-1.3` with `high` effort when difficulty is high or the
  security_class is injection, path traversal, deserialization or auth bypass. Rules are deterministic and documented.

## 4. Tests (offline)
Max-tokens sizing, explicit reasoning_effort in the request body, reasoning excerpt and request id recorded, truncated-JSON
retry, body_excerpt sanitizing and delimiting, security_class reaching the critic, the new selection rules and the
model-routing rule table.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
