# Audit Log

ISSA AS is built by **Meta Muse Code** (`muse exec`). A human-supervised administrator agent writes the prompts
(kept verbatim in [`prompts/`](../prompts)), reviews every result, runs the tests, and records here what was
accepted, what was corrected, and why.

| # | Date (ET) | Prompt | Model / effort | Cost (USD) | Verdict | Corrections requested |
|---|-----------|--------|----------------|------------|---------|-----------------------|
| 01 | 2026-11-02 | [01-architecture](../prompts/01-architecture.md) | muse-spark-1.3-contributor / high | ~$0.01 (552k in, 478k cached, 6.3k out, 15 calls) | Accepted with corrections | See entry 01 |

## Entries

### 01 — Architecture
**Accepted:** clear pipeline, deterministic verifier/budget/select, pluggable Spark Open provider with logged fallback,
trace schema, honest "one live fix + replay" demo promise. Stayed inside the workspace; committed locally, no push.

**Corrections requested (sent in prompt 02):**
1. *Untrusted code execution.* Running a third-party repo's test suite executes code we have not reviewed on the host PC.
   Add: reputation filter at scout time (stars, not archived, recent activity), sanitized environment (no secrets in env
   for test subprocesses), cwd and temp dirs pinned under `workspaces/<run>/`, hard timeouts, and run test commands through
   the Muse OS sandbox where possible.
2. *Runtime `muse exec` flags were unspecified.* The fixer must run with `--workspace workspaces/<run>/<repo>`,
   sandbox on, `--disable-web-tools`, `--max-model-steps`, worktree created inside `workspaces/`.
3. *Build order.* 31 h of breadth-first modules leaves no slack for a solo builder. Build a thin end-to-end vertical slice
   first (one known issue → failing test → fix → F2P verdict → trace → viewer), then widen.
4. *Ablation cost.* Restrict the 3-arm ablation to triage/critic (cheap); the fixer runs on one model plus one effort comparison.
5. Merge `reproduce` + `fixer` into one Muse session with two checkpoints (failing test committed first, then fix), so the
   agent keeps its repo context; the verifier still re-checks both independently.
