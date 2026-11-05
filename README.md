# ISSA AS — Issue Scouting, Solving & Attestation, Agentic Solver

> **Autonomous Security-First GitHub Issue Scout, Multi-Model Triage Router, and Deterministic Fail-to-Pass Repair Agent powered by Meta Muse Spark.**

[![CI](https://github.com/Ataraxiainc2/ISSAAS/actions/workflows/ci.yml/badge.svg)](https://github.com/Ataraxiainc2/ISSAAS/actions/workflows/ci.yml)

## Meta Global AI Hackathon 2026 — Submission Overview

- **Track**: Indie Developer (solo) · **Problem statement**: **Push the Frontier** — autonomous software engineering and security repair.
- **Built during the hackathon (Oct 26 – Nov 4, 2026), from an empty folder.** The whole history is in this repo's commits.
- **Inference: Meta Model API + Baseten (official hackathon inference partner).** Triage and the critic run
  **Muse Spark Open** open weights (`meta-models/muse-spark-open`) **on Baseten** (`https://inference.baseten.co/v1`,
  hackathon credits); repair runs **Muse Spark 1.3** through Meta Model API and Muse Code.
- **How it was built — Meta Muse Code wrote the product code.** Every line under `src/` and `tests/` was written by
  **Meta Muse Code (`muse exec`, model `muse-spark-1.3-contributor`)** from prompts kept verbatim in [`prompts/`](prompts/).
  A human-supervised administrator agent (Claude Code) wrote those prompts, audited every result, re-ran the tests,
  wrote the local audit console in `tools/console/` and the docs, and recorded each correction with its token count and
  USD cost in [`docs/AUDIT_LOG.md`](docs/AUDIT_LOG.md).
- **Spend:** Meta's Usage page showed **$3.63 of the $150 budget** on 2026-11-03, before Prompts 21–24 (≈ $0.17) and
  demo video v2 (≈ $0.83). On 2026-11-04 Prompts 25–33 cost ≈ $0.55 and live solves ≈ $2.97: **≈ $8 in total**
  (Meta's Usage page is the authoritative figure). Our own ledger had once counted $2.51 against $3.63; the difference
  was Muse reasoning tokens and sub-agent sessions it did not count (now fixed, see [audit entry 20](docs/AUDIT_LOG.md)).
  Muse Spark Open runs on Baseten hackathon credits; its tokens are logged in the traces, but no Baseten price is
  configured.
- **Bonus prizes targeted**: **Best Agentic Use-Case** and **Best Build on Meta Open Weight Models**.
- **Open data on Hugging Face**: [Ataraxiainc/issaas-triage-and-verdicts](https://huggingface.co/datasets/Ataraxiainc/issaas-triage-and-verdicts).
  It holds the 54 real Muse Spark Open triage and critic judgments and the 11 deterministic run verdicts from this repo.
  Issue bodies are left out. It is a dataset, not model weights.
- **Project page**: [https://issasodaweb.com/issaas/?lang=en](https://issasodaweb.com/issaas/?lang=en) (also in Spanish and Brazilian Portuguese)
- **Demo video**: [English (2:42)](https://www.youtube.com/watch?v=dii033mtx-0) · [Español (2:53)](https://www.youtube.com/watch?v=I7sf75a_2ek) · [Português (2:58)](https://www.youtube.com/watch?v=-Vfh8c5PNL8) — Muse Spark 1.3 translated
  the script and Muse Wonder listened back to every clip. The voice is Google Gemini TTS: our hackathon Meta API key
  offers no text-to-speech model (checked 2026-11-03); we ask for one in [`docs/VIDEO.md`](docs/VIDEO.md).

### For judges: the 5-minute path

1. **Four real fixes, VERIFIED:** the table in [section 3](#3-real-verified-proof-bundles-included-in-this-repo),
   each rebuilt from its committed bundle alone (no Muse, no keys) with `issa verify-bundle`; all four also on
   [Linux CI](https://github.com/Ataraxiainc2/ISSAAS/actions/runs/41920511861).
2. **The agent chooses where to look (new on Nov 4):** in `run-20261104-173437`, started with one confirm click in the
   local panel, Muse Spark Open read ISSA AS's own past outcomes, proposed 5 GitHub searches with a reason each,
   triaged 25 candidates (5 written in Chinese, explained in plain English/Spanish/Portuguese), picked
   aerkalov/ebooklib#359, and Muse Code fixed it: **VERIFIED, 0 regressions**. See
   [`traces/run-20261104-173437.jsonl`](traces/run-20261104-173437.jsonl) and [audit entry 22](docs/AUDIT_LOG.md).
3. **Muse Spark Open deciding:** [`triage/triage-scout-20261102-222258.json`](triage/triage-scout-20261102-222258.json)
   (30 scouted issues, 20 judged not real bugs, critic adjustments, the pick and why); readable with
   `uv run issa replay triage-scout-20261102-222258`, or browsable on the [project page](https://issasodaweb.com/issaas/?lang=en#datos).
4. **How it was built:** [`docs/AUDIT_LOG.md`](docs/AUDIT_LOG.md) (every Muse Code session, what failed, what it cost)
   and the verbatim prompts in [`prompts/`](prompts/).
5. **Limits we know about:** [section 6](#6-known-limits), including what outside reviewers found in our first
   upstream PR ([QWED#586](https://github.com/QWED-AI/qwed-verification/pull/586)) and the Pass-to-Pass gate we added
   in response.

### How Meta models run inside ISSA AS

| Stage | Meta model | Where | Status |
|---|---|---|---|
| Guided discovery: propose up to 5 GitHub searches from ISSA AS's own past outcomes (code validates them, no `:` qualifiers, and runs them) | **Muse Spark Open** on Baseten | `scout.py` | Live, on by default: `run-20261104-173437`, `scout-20261104-181347` |
| Plain-language briefs of every candidate (title, problem, fix idea) in English, Spanish, Portuguese | **Muse Spark Open** on Baseten (two passes: English, then translation) | `briefs.py` | Live: 28/28 translated (`scout/scout-20261104-181347.briefs.json`) |
| Triage of 20–30 issues (one batched call) + independent critic of the top 5 | **Muse Spark Open** (open weights, `meta-models/muse-spark-open` on Baseten) — fallback `muse-spark-1.3-contributor` via Meta Model API | `triage.py`, `critic.py`, `providers.py` | Live: 30 scouted issues triaged in one call, top 5 critiqued (`traces/triage-scout-20261102-222258.jsonl`) |
| Reproduce + fix (failing-test commit, then fix commit) | **Muse Spark 1.3** family through **Muse Code** (`muse exec --output-schema`), model and reasoning effort chosen per issue | `solver.py`, `select.py` | Live: keynote-parser#91 ($0.013), ansible-runner#1548 ($1.23), QWED#535 ($1.25) and ebooklib#359 ($0.76) **VERIFIED**; VulnClaw#310 honestly **REGRESSION** (see section 6) |
| Verification, scoring math, budget | none — plain deterministic Python | `verifier.py`, `signals.py`, `budget.py` | Live |

The hackathon guide names the Spark Open id `meta-models/muse-spark-open-1026`; on our Baseten account that id returns
HTTP 404 and `meta-models/muse-spark-open` works, so the id is configurable (`SPARK_OPEN_MODEL`).

---

## 1. Why ISSA AS?

Open-source maintainers drown in unverified bug reports—especially public security disclosures—while existing AI coding tools require a human to hand-pick an issue and still produce unverified patches that break existing tests.

**ISSA AS** closes the full autonomous software engineering loop with **mechanical honesty**:
1. **Security-First Autonomous Discovery (`scout.py`)**: Scans public GitHub repositories for 11 classes of publicly disclosed vulnerabilities first (*path traversal / zip-slip, command/SQL injection, SSRF, ReDoS, open redirect, XXE, unsafe deserialization, XSS, auth bypass, secret leakage*), enforcing reputation floors, maintainer AI-policy respect filters (`CONTRIBUTING.md` / `README.md`), `SECURITY.md` private-reporting detection, and test-runner detection.
2. **Explainable Hybrid Scoring + Multi-Model Triage (`signals.py`, `triage.py`, `critic.py`, `select.py`)**: Combines 9 deterministic repository signals with batched **Muse Spark Open** solvability scoring and an independent **Critic** pass to penalize overconfidence and select the highest-value opportunity under strict per-issue USD/step caps.
3. **Two-Checkpoint Reproduction & Repair (`solver.py`)**: Launches an isolated `muse exec` session (`--approval-mode never`, `--disable-web-tools`, prompt-injection hardened by omitting `--trust-workspace`) that must commit **(1) a minimal failing regression test** before committing **(2) the minimal source fix**.
4. **Deterministic Plain-Python Fail-to-Pass Gate (`verifier.py`)**: Zero LLM involvement in verification. Checks out the base state, verifies the new regression test **FAILS on base** and **PASSES after the patch**, and compares JUnit XML results against the original commit, so only tests that passed before and fail after count as **regressions** (pre-existing failures are recorded separately). **Pass-to-Pass preservation (new on Nov 4):** the solver also commits a preservation test file that pins down existing behaviour next to the change; the verifier proves it passes on base (else `UNPROVEN`) and still passes after the patch (else `REGRESSION`), recorded as `p2p_checked` in `verdict.json`. Added after outside reviewers found regressions our first upstream PR's green suite could not see (see section 6).
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
    A --> P[publish: dry-run drafts only, a human decides]
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

---

## 3. Real Verified Proof Bundles Included in This Repo

Four real fixes, generated by Muse Code and deterministically verified, are committed with **zero API keys
required** to check them. Each can be rebuilt from its bundle alone with `issa verify-bundle`; all four were
re-verified on a clean Linux runner by
[GitHub Actions run 41920511861](https://github.com/Ataraxiainc2/ISSAAS/actions/runs/41920511861) (the first two
earlier by [run 41613094986](https://github.com/Ataraxiainc2/ISSAAS/actions/runs/41613094986); recorded in each
bundle's `linux-verify.json`). Since Nov 4 every third-party install and test runs with an allowlisted environment
(no API keys, no host database URL) and an isolated pytest configuration ([audit entry 22](docs/AUDIT_LOG.md)).

| Issue | Class | Picked by | Solver | Verdict | Cost |
|---|---|---|---|---|---|
| [psobot/keynote-parser#91](https://github.com/psobot/keynote-parser/issues/91) | Zip slip (CWE-22) | issue given by hand | `muse-spark-1.3-contributor` / medium | **VERIFIED** (Windows and Linux), 0 regressions on the full suite | $0.013 |
| [ansible/ansible-runner#1548](https://github.com/ansible/ansible-runner/issues/1548) | Path traversal + symlink escape (CWE-22/59) | Muse Spark Open triage of 30 scouted issues | `muse-spark-1.3` / high (escalated) | **VERIFIED on Linux**, 0 regressions in `test/unit`; cannot run on Windows (`fcntl`) | $1.23 ($1.06 in `cost.json`, logged before reasoning tokens were counted) |
| [QWED-AI/qwed-verification#535](https://github.com/QWED-AI/qwed-verification/issues/535) | Taint analysis misses command injection; `html.escape` treated as a shell sanitiser | Muse Spark Open triage | `muse-spark-1.3` / high | **VERIFIED** (Windows and Linux), full suite **5,466 passed, 0 regressions** (pre-existing failures: 3 on Windows, 2 on Linux) | $1.25 |
| [aerkalov/ebooklib#359](https://github.com/aerkalov/ebooklib/issues/359) | Path traversal guard hardening (CWE-22): incomparable paths now fail closed | **fully autonomous**: guided search + triage | `muse-spark-1.3` / high | **VERIFIED** (Windows and Linux), full suite 171 passed, 0 regressions (1 pre-existing failure on Windows, none on Linux) | $0.76 |

The ansible-runner run is also an honest record of what went wrong on the way: ISSA AS first mis-parsed Muse's answer,
then a missing pytest, then Windows itself, each logged in [`docs/AUDIT_LOG.md`](docs/AUDIT_LOG.md) entries 14–17 and
fixed by Muse Code before the Linux verdict.

**keynote-parser#91 in detail:**

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

## Evaluation & Benchmarks

A fix only counts if the verifier proves it, so we attacked the verifier itself. `issa eval verifier` builds a small
local project with a path-traversal bug and its failing test, then runs the real `verify()` on nine patches. Results:
[`evals/results/verifier-synthetic.json`](evals/results/verifier-synthetic.json).

| Patch | Role | Expected | Got |
|---|---|---|---|
| The real fix | control | VERIFIED | **VERIFIED** |
| No-op (comment only) | attack | ERROR (test still fails) | **ERROR** |
| Delete the regression test | attack | UNPROVEN | **UNPROVEN** |
| Inject `pytest.skip` | attack | UNPROVEN | **UNPROVEN** |
| Mark the test `xfail` | attack | UNPROVEN | **UNPROVEN** |
| `conftest.py` hook that rewrites failures to passes | attack | UNPROVEN | **UNPROVEN** |
| `pytest.ini` `addopts` that deselects the test | attack | UNPROVEN | **UNPROVEN** |
| Real fix + an unrelated breakage | attack | REGRESSION | **REGRESSION** |
| Special-case the test's literal input | known limit | VERIFIED | VERIFIED |

- **7/7 attacks rejected, 0 false accepts, the real fix still accepted.** A full check of the real fix takes about
  4 s (3.7–4.3 s across runs) and all nine patches together about 15 s; a patch that touches the test or pytest
  configuration is rejected in about 0.2 s, before any test runs.
- **Before this hardening, the shortcuts in rows 3–7 could have been reported VERIFIED**: Fail-to-Pass trusted pytest's
  exit code. It now reads the JUnit XML (at least one passed test, zero failed, errored, skipped or xfailed). See audit
  entry 20.
- **Known limit:** Fail-to-Pass cannot detect overfitting (row 9). That is why a person reviews every patch, and why
  nothing is ever sent upstream automatically.
- `issa eval verifier --bundle <run_id>` builds the same attacks from a committed proof bundle, in that project's own
  environment.

`issa eval runs` computes run metrics from the committed `proof/`, `traces/` and `triage/` files
([`evals/results/runs.json`](evals/results/runs.json)): 11 solver runs, **4 VERIFIED on Linux**, 3 REGRESSION (all
VulnClaw#310), 2 ABSTAIN, 2 ERROR; $4.04 of solver spend as logged, failed runs included, so **$1.01 per verified fix**. The triage funnel: 30 scouted issues → 20 judged
not real bugs by Muse Spark Open → 1 picked → VERIFIED. Two later live triages (recorded for the demo video, 8
candidates each) picked the same issue.

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

# Keyless Trace Replay: readable timeline of a real Muse Spark Open triage + critic run (chain checked first)
uv run issa replay triage-scout-20261102-222258
```

### 3. Verify a Proof Bundle on Linux (Keyless)
A proof bundle is checkable on a clean Linux machine from this repo alone — no Muse, no keys.
`verify-bundle` re-clones the upstream repo at `test_parent`, commits the bundled test file locally,
recreates the venv, and re-runs the Fail-to-Pass gate with `fix.patch`:
```bash
uv run issa verify-bundle 20261102-201027-psobot-keynote-parser-91
# issa verify-bundle: run_id=... verdict=VERIFIED f2p=true regressions=0 baseline_failures=3
```
Results land in `workspaces/bundle-<run_id>/result/` (`verdict.json`, `verify.log`, `install.log`;
the original `proof/<run_id>/` files are never touched). Exit code 0 means `VERIFIED` only.
The same check runs in CI without secrets: Actions → **Verify proof bundles** → Run workflow
(optional `run_ids`, defaults to all four verified bundles). Needed on Linux
because some targets (e.g. ansible-runner, which imports `fcntl`) cannot even be collected on Windows.

### 4. Launch the Read-Only Local Audit Console (Keyless)
Inspect every Muse Code build & solver session, tool call, and token cost:
```bash
uv run python tools/console/server.py
# Open http://127.0.0.1:8765 in your browser
```

### 5. Live Execution (With Meta Model API / Muse Code)
Copy `.env.example` to `.env` and supply `MODEL_API_KEY` (and optional `SPARK_OPEN_BASE_URL` / `SPARK_OPEN_API_KEY` / `SPARK_OPEN_MODEL` for Muse Spark Open on Baseten; default model id `meta-models/muse-spark-open`):
```bash
cp .env.example .env

# 1. Discover security-first candidate issues on GitHub
uv run issa scout --limit 30 --languages python,node

# 2. Triage & rank candidates (use --dry-run to preview token/USD estimate without API calls)
uv run issa triage scout-20261102-210449 --dry-run

# 3. Solve a specific issue end-to-end in an isolated workspace
uv run issa solve https://github.com/psobot/keynote-parser/issues/91 --model muse-spark-1.3-contributor --effort medium
```

### 6. Local Control Panel (operator only)
The read-only audit console above replays committed sessions. The operator panel below also runs
actions on this machine (localhost only; the public site is a separate presentation page):
```bash
uv run issa serve --port 8780
# Open http://127.0.0.1:8780 in your browser
```
- **Views (reads):** runs (verdict, cost, hash-chain status, total spend vs the $150 budget),
  scout candidates, triage + critic scores with model/provider provenance, the decision and why,
  the work/verification timeline, and the result (verdict badge, regression test, `fix.patch` diff).
- **Actions (one at a time, progress streams):** scout, triage (dry-run estimate first),
  solve selected (model, effort, step cap, estimated USD), recover, reverify, full loop (`max-usd`
  field). Each paid action needs the confirm token from its estimate request, checked on the server.
- **Guards:** binds `127.0.0.1` only, rejects foreign `Host`/`Origin`, validates run ids, inserts
  issue/model text with `textContent` only, and never returns `.env` secrets.
- **Per-issue USD cap rule:** escalated picks (`muse-spark-1.3` + `high` effort) carry
  `recommended_max_usd` **2.00**, everything else **0.60**. The ansible-runner#1548 fix took 59 steps
  and $1.23 with reasoning tokens counted (≈ $0.021 per step), so the $0.60 default allows only
  ≈ 28 strong steps, and the earlier 1.20 cap would now stop that fix just before it finished.

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
| [`src/issa_as/serve.py`](src/issa_as/serve.py) & [`web/`](web/index.html) | Local control panel: 127.0.0.1-only server plus static UI (reads, confirm-token actions, one worker) |
| [`proof/`](proof/) & [`traces/`](traces/) | Committed proof bundles, patches, regression tests, and hash-chained execution traces |
| [`docs/AUDIT_LOG.md`](docs/AUDIT_LOG.md) & [`prompts/`](prompts/) | Full transparent log of every Muse Code session used to build and test ISSA AS |

---

## 6. Known Limits

- **Four verified fixes so far.** keynote-parser#91 was given by hand; ansible-runner#1548 and QWED#535 were chosen by
  Spark Open triage; ebooklib#359 came from a fully guided run. ansible-runner's regression check covers `test/unit`
  (its integration tests need Ansible and containers), and it can only be verified on Linux (the package imports
  `fcntl`). ebooklib#359 is hardening: the main traversal was already guarded upstream at that commit, and the fix
  makes the guard fail closed on paths `os.path.commonpath` cannot compare.
- **Line-keyed security allowlists.** VulnClaw#310 ended REGRESSION four times (two models): the fix is correct and its
  test fails before and passes after, but VulnClaw keys its reviewed process-spawn sites by line number, so shifting
  `vulnclaw/cli/main.py` breaks 3 guard tests. Updating a security allowlist is a human decision; ISSA AS reports the
  regression instead of editing it.
- **What `VERIFIED` guarantees, and what it does not.** It means the issue's test fails before the patch and passes
  after it, and that no test that passed before fails after. It does not mean that behaviour no test covers is
  unchanged.
  - **The evidence:** our first upstream PR, [QWED-AI/qwed-verification#586](https://github.com/QWED-AI/qwed-verification/pull/586)
    (from the QWED#535 bundle), was `VERIFIED` with a green full suite. Outside review bots still found three
    rounds of regressions: inputs the analyzer detected before and missed after, which no QWED test covered.
  - **How we answered:**
    - We reproduced every finding against the base branch, and fixed each one with a test that fails on the
      previous commit (Prompts 34–34d).
    - We swept the analyzer's own source × sink tables against `main`: 3,450 combinations, +1,900 detections, the
      only losses intentional.
    - We added the Pass-to-Pass preservation gate above.
  - **What the gate covers:** only the behaviour its tests pin down. The four bundles in section 3 were verified
    before it existed, so their `verdict.json` has no `p2p_checked`.
  - **Next step, not built:** an adversarial reviewer that proposes inputs, and a plain-Python diff of base and
    patched outputs on them. The full story is in [audit entry 23](docs/AUDIT_LOG.md).
- **Fail-to-Pass cannot catch overfitting.** A fix that special-cases the test's literal input still passes (the known
  limit in `issa eval verifier`).
- **Python targets only.** Scout finds Node issues, but they are skipped with "runner not implemented yet".
- **Nothing is sent upstream automatically.** `issa publish` writes local dry-run drafts only; upstream contributions
  are a human decision (two so far, each pushed and opened by a person after review: [QWED#586](https://github.com/QWED-AI/qwed-verification/pull/586) and [ebooklib#396](https://github.com/aerkalov/ebooklib/pull/396)). When a project's `SECURITY.md` asks for private reports, ISSA AS writes a private `disclosure.md` draft
  instead of a public PR draft (ansible-runner: [`disclosure.md`](proof/20261102-223630-ansible-ansible-runner-1548/disclosure.md),
  addressed to the project's security email, not sent). This check was added on 2026-11-03, after the ansible-runner
  issue had been picked; see audit entry 18.
- **Spark Open USD is not priced** (Baseten credits); its tokens are recorded.
- **Windows host:** targets run outside the Muse sandbox during dependency installs and tests (see the safety model in
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)); Linux verification runs on GitHub's runners with a read-only token.

---

## License

Released under the [MIT License](LICENSE).
