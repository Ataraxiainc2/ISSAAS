# Live demo video v2: shots

The v2 video is built from real recordings of the product (`ISSA_VIDEO_VERSION=v2`, script in
[`script-v2.json`](script-v2.json)). [`record.py`](record.py) records them: it drives a headless Microsoft Edge through
the DevTools protocol and keeps what Edge renders (`Page.startScreencast`), with a visible cursor for every click.
Terminal clips run the real command at that moment and stream its output into a terminal-styled page as it prints.
Long waits (the triage job) are cut, and the build speeds clips up at most 2.5× to fit the narration.

| Clip | Scene | What it shows |
|---|---|---|
| `a0-panel` | opening | The local control panel: tabs, the Runs list with both fixes VERIFIED · Linux. |
| `a-scout` | 1 · Scout | Actions → Scout: a new scout run (8 candidates, Python), job progress streaming. |
| `b-triage` | 2 · Triage | Actions → Triage on that scout run: estimate, confirm, the live Muse Spark Open job log, then the Triage tab. |
| `g-solve` | 3 · Solve | The Muse console on the real Muse Code session that fixed ansible-runner #1548: prompt, 59 model calls, usage. |
| `c-result` | 4 · Verify | Decision & result for ansible-runner #1548: verdicts, the Linux verification block, the regression test, `fix.patch`. |
| `e-attacks` | 5 · Attacks | `issa eval verifier` and the result table. |
| `d-ci` | 6 · Linux CI | The GitHub Actions run (Success) and its green "Verify each bundle (clean-room rebuild)" step. |
| `f-keyless` | 7 · Keyless | `issa verify` and `issa replay` on the triage trace. |
| `g-muse` | 8 · Built by Muse Code | The Muse console on the Prompt 22 session that built part of ISSA AS. |

Recording (needs the panel on port 8782 and the Muse console on 8765):

```bash
uv run --no-sync python video/record.py all
```

`b-triage` makes real Muse Spark Open calls on Baseten (hackathon credits, a few cents); `a-scout` makes a handful of
GitHub search requests. GitHub job logs need a sign-in, so `d-ci` shows the public run and step status; the Linux
verdicts themselves are shown in `c-result`, read from `linux-verify.json` in the proof bundles.

Build:

```bash
ISSA_VIDEO_VERSION=v2 uv run python video/make_video.py translate
ISSA_VIDEO_VERSION=v2 uv run python video/make_video.py voice
ISSA_VIDEO_VERSION=v2 uv run python video/make_video.py check
ISSA_VIDEO_VERSION=v2 uv run python video/make_video.py slides
ISSA_VIDEO_VERSION=v2 uv run python video/make_video.py build
```
