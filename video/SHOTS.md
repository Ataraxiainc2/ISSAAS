# Live demo video v2: shot list

Record each clip separately with the Windows Snipping Tool (Win + Shift + R, "Record") or the Game Bar (Win + Alt + R).
1920×1080 if possible, browser zoom 125 %, terminal font large (≥ 18 pt), dark theme. No narration needed while
recording: the voice and captions are added afterwards. Keep each clip a little longer than its target; it is trimmed.
Save as `video/rec/<clip>.mp4` (the folder is git-ignored).

Before recording: `uv run issa serve --port 8780` running, panel open at http://127.0.0.1:8780, a terminal in
`D:\SC\ISSAAS`.

| Clip | Target | What to show |
|---|---|---|
| `a-scout` | 20 s | Terminal: `uv run issa scout --limit 10 --languages python`. Let the candidate lines scroll and the summary print. |
| `b-triage` | 30 s | Panel → Actions → Triage: pick the new scout run, press Estimate (cost shows), then Confirm. Let the progress stream until Muse Spark Open's scores appear. Then the Triage tab: scroll the table, open one rationale. |
| `c-result` | 25 s | Panel → Runs: ansible-runner #1548 with VERIFIED · Linux. Click it → Decision & result: the regression test, the `fix.patch` diff, both verdicts. |
| `d-ci` | 15 s | Browser: https://github.com/Ataraxiainc2/ISSAAS/actions/runs/41613094986 → open the job → scroll to the two `VERIFIED` summary lines. |
| `e-attacks` | 20 s | Terminal: `uv run issa eval verifier`. Let every attack line print and the summary `N/N attacks rejected as expected`. |
| `f-keyless` | 15 s | Terminal: `uv run issa verify triage-scout-20261102-222258` then `uv run issa replay triage-scout-20261102-222258 \| more` (first screen). |

Clip `b-triage` makes a real Muse Spark Open call on Baseten (hackathon credits, a few cents). Clip `a-scout` makes a
handful of GitHub search requests; run it once.
