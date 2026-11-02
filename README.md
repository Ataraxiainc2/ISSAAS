# ISSA AS — Issue Scouting, Solving & Attestation, Agentic Solver

> **Autonomous Security-First GitHub Issue Scout, Multi-Model Triage Router, and Deterministic Fail-to-Pass Repair Agent powered by Meta Muse Spark.**

[![CI](https://github.com/Ataraxiainc2/ISSAAS/actions/workflows/ci.yml/badge.svg)](https://github.com/Ataraxiainc2/ISSAAS/actions/workflows/ci.yml)

## Meta Global AI Hackathon 2026 — Submission Overview

- **Track**: Indie Developer (solo) · **Problem statement**: **Push the Frontier** — autonomous software engineering and security repair.
- **Built during the hackathon (Oct 26 – Nov 4, 2026), from an empty folder.** The whole history is in this repo's commits.
- **How it was built — Meta Muse Code wrote the product code.** Every line under `src/` and `tests/` was written by
  **Meta Muse Code (`muse exec`, model `muse-spark-1.3-contributor`)** from prompts kept verbatim in [`prompts/`](prompts/).
  A human-supervised administrator agent (Claude Code) wrote those prompts, audited every result, re-ran the tests,
  wrote the local audit console in `tools/console/` and the docs, and recorded each correction with its token count and
  USD cost in [`docs/AUDIT_LOG.md`](docs/AUDIT_LOG.md). Total Muse Code build cost so far: **≈ $0.30**.
- **Bonus prizes targeted**: **Best Agentic Use-Case** and **Best Build on Meta Open Weight Models**.
- **Web portal (planned)**: [https://issasodaweb.com](https://issasodaweb.com)

### How Meta models run inside ISSA AS

| Stage | Meta model | Where | Status |
|---|---|---|---|
| Triage of 20–30 issues (one batched call) + independent critic of the top 5 | **Muse Spark Open** (open weights, `meta-models/muse-spark-open` on Baseten) — fallback `muse-spark-1.3-contributor` via Meta Model API | `triage.py`, `critic.py`, `providers.py` | Provider verified with a live call; first full live triage run pending |
| Reproduce + fix (failing-test commit, then fix commit) | **Muse Spark 1.3** family through **Muse Code** (`muse exec --output-schema`), model and reasoning effort chosen per issue | `solver.py`, `select.py` | Live: keynote-parser#91 fixed and **VERIFIED** for **$0.013** |
| Verification, scoring math, budget | none — plain deterministic Python | `verifier.py`, `signals.py`, `budget.py` | Live |

The hackathon guide names the Spark Open id `meta-models/muse-spark-open-1026`; on our Baseten account that id returns
HTTP 404 and `meta-models/muse-spark-open` works, so the id is configurable (`SPARK_OPEN_MODEL`).

---

## 1. Why ISSA AS?

Open-source maintainers drown in unverified bug reports—especially public security disclosures—while existing AI coding tools require a human to hand-pick an issue and still produce unverified patches that break existing tests.

**ISSA AS** closes the full autonomous software engineering loop with **mechanical honesty**:
1. **Security-First Autonomous Discovery (`scout.py`)**: Scans public GitHub repositories for 11 classes of publicly disclosed vulnerabilities first (*path traversal / zip-slip, command/SQL injection, SSRF, ReDoS, open redirect, XXE, unsafe deserialization, XSS, auth bypass, secret leakage*), enforcing reputation floors, maintainer AI-policy respect filters (`CONTRIBUTING.md` / `README.md`), and test-runner detection.
2. **Explainable Hybrid Scoring + Multi-Model Triage (`signals.py`, `triage.py`, `critic.py`, `select.py`)**: Combines 9 deterministic repository signals with batched **Muse Spark Open** solvability scoring and an independent **Critic** pass to penalize overconfidence and select the highest-value opportunity under strict per-issue USD/step caps.
3. **Two-Checkpoint Reproduction & Repair (`solver.py`)**: Launches an isolated `muse exec` session (`--approval-mode never`, `--disable-web-tools`, prompt-injection hardened by omitting `--trust-workspace`) that must commit **(1) a minimal failing regression test** before committing **(2) the minimal source fix**.
4. **Deterministic Plain-Python Fail-to-Pass Gate (`verifier.py`)**: Zero LLM involvement in verification. Checks out the base state, verifies the new regression test **FAILS on base** and **PASSES after the patch**, and compares JUnit XML results against the original commit, so only tests that passed before and fail after count as **regressions** (pre-existing failures are recorded separately).
5. **Cryptographic Hash-Chained Traces & Proof Bundles (`traces.py`, `proof/`)**: Every pipeline event is chained with SHA-256 (`prev_hash` $\rightarrow$ `hash`) in `traces/<run_id>.jsonl`. Unproven fixes are never published; when reproduction fails, ISSA AS emits an **honest abstention** with diagnostic evidence.

---

## 2. Architecture

```mermaid
flowchart LR
    S[scout: security-first discovery] --> T[triage: Spark Open hybrid score]
    T --> C[critic: Spark Open rescore]
    C --> SL[select: pick issue + effort]
    SL --> R[solver checkpoint 1: failing test commit]
    R --> F[solver checkpoint 2: minimal fix commit]
    F --> V[verifier: plain-Python Fail-to-Pass + JUnit baseline]
    V -- VERIFIED --> A[attester: proof/<run_id>/ bundle]
    V -- cannot verify --> AB[abstain: honest triage note]
    A --> P[publish: human-gated dry-run / draft PR]
    S -.-> TR[(SHA-256 hash-chained traces/run_id.jsonl)]
    T -.-> TR
    C -.-> TR
    SL -.-> TR
    R -.-> TR
    F -.-> TR
    V -.-> TR
    A -.-> TR
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the complete technical specification, safety model, and trace schema.

---

## 3. Real Verified Proof Bundle Included in This Repo

Judges can inspect a real, committed end-to-end security fix generated and deterministically verified by ISSA AS with **zero API keys required**:

- **Target Issue**: [`psobot/keynote-parser#91`](https://github.com/psobot/keynote-parser/issues/91) — *Zip-Slip / Arbitrary File Overwrite via Path Traversal (CWE-22)*.
- **Run ID**: `20261102-201027-psobot-keynote-parser-91`
- **Artifacts in [`proof/20261102-201027-psobot-keynote-parser-91/`](proof/20261102-201027-psobot-keynote-parser-91/)**:
  - [`verdict.json`](proof/20261102-201027-psobot-keynote-parser-91/verdict.json): `"verdict": "VERIFIED"`, `"f2p_pass": true`, `"regressions": 0` (3 pre-existing failures in the upstream suite recorded as `baseline_failures`), `"cost_usd": 0.0131`
  - [`test/tests/test_zip_slip.py`](proof/20261102-201027-psobot-keynote-parser-91/test/tests/test_zip_slip.py): Minimal regression test proving CWE-22 traversal fails before the patch and passes after.
  - [`fix.patch`](proof/20261102-201027-psobot-keynote-parser-91/fix.patch): Confines extracted archive paths via `safe_target_path()` (`os.path.realpath` containment).
  - [`verify.log`](proof/20261102-201027-psobot-keynote-parser-91/verify.log) & [`cost.json`](proof/20261102-201027-psobot-keynote-parser-91/cost.json): Full execution transcript and token ledger.
- **Honest Failure / Abstention Evidence Also Committed**:
  - [`proof/20261102-195816-psobot-keynote-parser-91/`](proof/20261102-195816-psobot-keynote-parser-91/) and [`proof/20261102-200703-psobot-keynote-parser-91/`](proof/20261102-200703-psobot-keynote-parser-91/) preserve earlier unedited runs, demonstrating strict separation between `ERROR`, `ABSTAIN`, and `VERIFIED`.

---

## 4. Quickstart & Keyless Trace Verification (Zero API Keys Required)

### Prerequisites
- Python 3.13 installed locally and [`uv`](https://github.com/astral-sh/uv) (`uv.toml` disables uv-managed Python downloads, so uv uses your installed 3.13)

### 1. Install & Run Offline Test Suite (No API Keys Needed)
```bash
uv sync
uv run pytest -m "not slow"
uv run ruff check src tests
```

### 2. Verify Committed Cryptographic Trace Chains (Keyless)
Every committed run in [`traces/`](traces/) is SHA-256 hash-chained. Verify tamper-evidence immediately without any API keys:
```bash
uv run issa verify 20261102-201027-psobot-keynote-parser-91
uv run issa verify scout-20261102-210449
```

### 3. Launch the Read-Only Local Audit Console (Keyless)
Inspect every Muse Code build & solver session, tool call, and token cost:
```bash
uv run python tools/console/server.py
# Open http://127.0.0.1:8765 in your browser
```

### 4. Live Execution (With Meta Model API / Muse Code)
Copy `.env.example` to `.env` and supply `MODEL_API_KEY` (and optional `SPARK_OPEN_BASE_URL` / `SPARK_OPEN_API_KEY` for hosted `meta-models/muse-spark-open-1026`):
```bash
cp .env.example .env

# 1. Discover security-first candidate issues on GitHub
uv run issa scout --limit 30 --languages python,node

# 2. Triage & rank candidates (use --dry-run to preview token/USD estimate without API calls)
uv run issa triage scout-20261102-210449 --dry-run

# 3. Solve a specific issue end-to-end in an isolated workspace
uv run issa solve https://github.com/psobot/keynote-parser/issues/91 --model muse-spark-1.3-contributor --effort medium
```

---

## 5. Repository Structure

| Path | Description |
|---|---|
| [`src/issa_as/scout.py`](src/issa_as/scout.py) | Security-first GitHub issue discovery, reputation & AI-policy filters, runner detection |
| [`src/issa_as/signals.py`](src/issa_as/signals.py) | 9 weighted deterministic solvability signals with human-readable explanations |
| [`src/issa_as/triage.py`](src/issa_as/triage.py) / [`critic.py`](src/issa_as/critic.py) / [`select.py`](src/issa_as/select.py) | Batched Spark Open triage, top-k overconfidence critic, and cost-capped issue selector |
| [`src/issa_as/solver.py`](src/issa_as/solver.py) | Two-checkpoint `muse exec` wrapper (failing test commit $\rightarrow$ minimal fix commit) |
| [`src/issa_as/verifier.py`](src/issa_as/verifier.py) | Deterministic plain-Python Fail-to-Pass gate and JUnit XML baseline regression checker |
| [`src/issa_as/traces.py`](src/issa_as/traces.py) | Typed JSONL event logger with SHA-256 hash chaining (`issa verify`) |
| [`src/issa_as/budget.py`](src/issa_as/budget.py) & [`providers.py`](src/issa_as/providers.py) | Per-issue USD/step/time caps, token cost ledger, and Meta Model API / Spark Open routing |
| [`proof/`](proof/) & [`traces/`](traces/) | Committed proof bundles, patches, regression tests, and hash-chained execution traces |
| [`docs/AUDIT_LOG.md`](docs/AUDIT_LOG.md) & [`prompts/`](prompts/) | Full transparent log of every Muse Code session used to build and test ISSA AS |

---

## License

Released under the [MIT License](LICENSE).
