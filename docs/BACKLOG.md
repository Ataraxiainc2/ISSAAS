# Backlog — ideas to fold into the milestones

Ideas reviewed by the administrator and accepted for ISSA AS. Each one says where it lands.

| # | Idea | Why it matters | Lands in |
|---|------|----------------|----------|
| B1 | **Hash-chained trace.** Each JSONL event stores `prev_hash` and `hash` (SHA-256 of the canonical event + `prev_hash`). `issa verify <run>` recomputes the chain and reports the first tampered line. | Proves Keyless Replay runs were not edited after the fact. | M2 (traces.py + cli) |
| B2 | **External request IDs.** `model_call` and GitHub events record the request/response ID returned by the Meta API or GitHub. | Lets anyone cross-check a recorded call outside our system. | M2 (providers.py) |
| B3 | **Verdict labels.** `VERIFIED` only when the new test fails on base and passes with the patch (no regressions). A test that passes on base is `UNPROVEN`; no test is `NO_TEST`; anything else `ABSTAINED`. Only `VERIFIED` bundles can ever be published. | Makes the honesty rule mechanical, not a promise. | M2 (verifier.py) |
| B4 | **Explainable hybrid score.** Solvability = weighted mix of deterministic signals (has repro steps, stack trace, code snippet, test suite present, CI present, repo size) and the model's judgment, each component shown and the total labelled an estimate. | Judges can see why an issue was chosen; also the calibration baseline. | M3 (triage.py, select.py) |
| B5 | **Maintainer respect filters.** Skip repos whose CONTRIBUTING/README refuse AI-generated contributions, skip issues labelled security/vulnerability, disclose AI authorship in every draft, at most one draft PR per run. | Avoids spam and reputational risk; strong impact story. | M3 (scout.py), publish.py |
| B6 | **Cap stops are events.** Hitting a cost/step/time cap ends the stage with a `cap_blocked` event, rendered in the viewer like any other step. | Failures and limits are visible, never hidden. | Done in M1 (budget.py); render in M5 |
