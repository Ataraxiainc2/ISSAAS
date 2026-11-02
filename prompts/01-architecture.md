# Prompt 01 — Architecture (no code)

You are the lead engineer of **ISSA AS — Issue Scouting, Solving & Attestation, Agentic Solver**, a solo entry for the
Meta Global AI Developer Hackathon 2026. Deadline: Wed Nov 4, 2026, 9:00 PM ET (~50 hours from now). Read `AGENTS.md` first and obey it.

## Task
Write `docs/ARCHITECTURE.md` (max ~2 pages). **Do not write any code in this step.** Commit it locally when done.

## Product
An agent that autonomously (1) discovers open bug issues on public GitHub repos, (2) triages 20–30 candidates with a
solvability score and rationale, (3) selects which to attempt and explains why, (4) reproduces the bug as a failing test,
fixes it in an isolated git worktree, (5) proves the fix deterministically (Fail-to-Pass), and (6) produces a
"proof bundle" (failing test, patch, logs, cost) plus a draft PR text for a human to approve. If it cannot reproduce or
verify, it abstains honestly and writes an evidence-based triage note instead.

## Fixed decisions
- Scope: pure-Python repos with pytest, small test suites, issues labelled bug with a code snippet. Windows host, `uv` only, no Docker.
- Deterministic verifier (plain Python): new test FAILS on base commit, PASSES after patch, and the pre-existing suite does not regress.
- Models (Meta Model API, OpenAI-compatible, base URL `https://api.meta.ai/v1`, key in env `MODEL_API_KEY`):
  - `muse-spark-1.3` ($1.25 in / $4.25 out per 1M tokens) and `muse-spark-1.3-contributor` ($0.10 / $0.20).
  - Fixer stage runs through `muse exec --worktree create --output-schema ...` with reasoning effort chosen from the triage difficulty.
  - **Muse Spark Open** (`meta-models/muse-spark-open-1026`) is NOT served by Meta Model API; it is served by hosted
    providers (e.g. Baseten, OpenAI-compatible). Make the triage/critic model a pluggable provider so it can run on Spark Open
    when credentials exist and fall back to Spark 1.3 otherwise. Spark Open must be a load-bearing stage (triage + patch critic) for the open-weights bonus.
- Every stage appends events to `traces/<run_id>.jsonl`. One static HTML viewer renders both live runs and the
  **Keyless Trace Replay** (judges replay committed real traces with no API keys).
- Budget: total $150 for the whole project. Per-issue caps (steps, wall time, USD) enforced in code and logged.
- Benchmark to report: verified-fix rate, abstention accuracy, triage calibration (predicted solvability vs outcome),
  cost per verified fix, and an ablation comparing triage models (Spark Open vs Spark 1.3 vs contributor) on the same issues.
- GitHub writes at runtime only through an explicit, human-approved `publish` command (fork + draft PR). Default is dry-run.

## ARCHITECTURE.md must contain
1. One-paragraph pitch (problem, who benefits, why it matters post-hackathon).
2. Pipeline diagram (ASCII or Mermaid) and the responsibility of each stage.
3. Module/file layout under `src/issa_as/` with the public interface of each module (function signatures only).
4. JSONL trace event schema (event types and fields).
5. Model routing table: stage → model → reasoning effort → why → estimated cost per issue.
6. Safety model: filesystem confinement to `workspaces/`, network, GitHub write gate, secrets.
7. Build plan: ordered modules, each small enough for one prompt, with time estimate and a test that proves it works.
8. Top risks and mitigations (be honest; cut anything that is demo smoke).

Keep it concise. Finish with a 5-line summary of what you decided.
