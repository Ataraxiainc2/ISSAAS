# Prompt 11 — Scout quality fixes + Milestone 3b: triage, critic and selection

Read `AGENTS.md`, audit entry 10 in `docs/AUDIT_LOG.md`, `docs/BACKLOG.md` (B4, B7) and `scout/scout-20261102-210449.json`
(the first live scout report). **No real model calls in this step** — tests use `FakeProvider`.

## A. Scout fixes found in the live report
1. `--languages` must restrict the GitHub queries themselves (`language:python`, `language:javascript`,
   `language:typescript`); 22 of 30 live candidates had `runner: none` and were useless.
2. Extend the bot/alert filter: titles like `[vuln-sentinel]`, "N known vulnerabilities", scanner rule IDs (`NEMO-009`, `[SQ:…]`).
3. Drop feature requests and docs tasks: titles starting with `[Feature]`, `feat:`, `Document`, `Add … support`, or labels
   `enhancement`/`feature`/`documentation` — unless the body has a stack trace or a reproduction.
4. Sort the final list by score (security first only as a tie-breaker inside the same score band).
5. **Rate limiting (AGENTS.md rule 8 — the repo is now public and must not get blocked):** a single GitHub client used
   by scout and workspace with a minimum delay between requests (search ≥ 2.5 s, REST ≥ 0.5 s), honoring
   `X-RateLimit-Remaining`, `X-RateLimit-Reset` and `Retry-After`; when the remaining budget is low it stops and returns
   what it has, with a `rate_limited` note in the report. Cache hits never wait. The Meta API client gets exponential
   backoff on 429/5xx with at most 3 retries and never runs requests in parallel.

## B. `.env` loading
Load `D:\SC\ISSAAS\.env` at CLI start with a tiny stdlib parser (no new dependency); never print values; real environment
variables win over `.env`.

## C. `triage.py` + `critic.py` + `select.py`
- `triage.score(candidates, provider)`: ONE batched model call for all candidates (cheap), strict JSON schema
  (`additionalProperties: false`, all fields required, nullable optionals) returning per issue: `solvability` 0–1,
  `difficulty` (low/medium/high), `is_real_bug`, `security_relevant`, `rationale` (≤ 2 sentences).
  Final score = documented weighted blend of the deterministic `signals` score and the model's solvability (B4), both shown.
- `critic.rescore(top_k, provider)`: second, independent call on the top 5 only; flags overconfidence; emits `critic_rescored`.
- `select.pick(...)`: deterministic; picks the issue to attempt and the effort (difficulty → low/medium/high), records
  `selected`/`skipped` with reasons; never picks `runner: none` or already-solved issues (a `proof/` bundle with
  `VERIFIED` for the same issue URL).
- Provider routing: triage and critic use Spark Open when `SPARK_OPEN_*` is set, else `muse-spark-1.3-contributor` via the
  Meta Model API (`MODEL_API_KEY`); every call emits `model_call` with tokens, USD and the provider actually used.
- CLI: `issa triage <scout_run_id> [--dry-run]` — `--dry-run` prints the exact request (model, token estimate, estimated
  USD) without calling the API.

## D. Tests (offline)
Scout fixes (language qualifiers, new bot/feature filters, sort), rate limiter (fake clock: delays, stop on low
remaining, Retry-After, no wait on cache hit), Meta API backoff, `.env` loader (no override of real env, values never
logged), triage blend math, strict schema rules, critic on top-5 only, selection rules, dry-run makes zero network calls.

`uv run pytest -m "not slow"` and `uv run ruff check` must pass (uv at `.tools/uv.exe`). Commit locally once, do not push.
Be concise in the final summary.
