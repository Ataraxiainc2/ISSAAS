"""Build the 3-minute ISSA AS demo video in English, Latin American Spanish and Brazilian Portuguese.

Administrator tool, not part of the product. Steps (run in order, each one can be re-run):

    uv run python video/make_video.py translate   # Muse Spark 1.3 (Meta Model API) translates script.json
    uv run python video/make_video.py voice       # Google Gemini TTS narrates every scene in every language
    uv run python video/make_video.py check       # Muse Wonder (Meta Model API) listens back, word match
    uv run python video/make_video.py slides      # headless Edge renders slides.html to PNG per scene
    uv run python video/make_video.py build       # ffmpeg joins slides + narration into MP4 + SRT
    uv run python video/make_video.py costs       # spend per step from video/costs.jsonl

Why Gemini for the voice: on 2026-11-03 the Meta Model API offered no text-to-speech model
(`POST /v1/audio/speech` returned 404; Muse Wonder outputs text only). See docs/VIDEO.md.

Keys come from the environment (`.env`) or the ISSASODA key store (`D:/SC/AK`, read by label); none is
ever printed. Every paid call is appended to video/costs.jsonl with its token counts.
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / "video"
# ISSA_VIDEO_VERSION=v2 builds the live-footage cut from script-v2.json and the screen recordings in video/rec/
VERSION = os.environ.get("ISSA_VIDEO_VERSION", "v1")
OUT = VIDEO / "out" if VERSION == "v1" else VIDEO / "out" / VERSION
SCRIPT = VIDEO / ("script.json" if VERSION == "v1" else f"script-{VERSION}.json")
REC = Path(os.environ.get("ISSA_VIDEO_REC", VIDEO / "rec")).resolve()  # screen recordings for v2
COSTS = VIDEO / "costs.jsonl"
LANGS = ["en", "es", "pt"]
LANG_NAME = {"en": "American English", "es": "Latin American Spanish", "pt": "Brazilian Portuguese"}

TRANSLATE_MODEL = "muse-spark-1.3"
TTS_MODEL = "gemini-3.8-flash-tts"
TTS_VOICE = "Charon"
CHECK_MODEL = "muse-wonder-1.0"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
META_API = "https://api.meta.ai/v1"
MIN_GAP_S = 2.0  # one request at a time, at least this far apart
# USD per 1M tokens (in, out), as published on 2026-11-03: Gemini 3.8 Flash TTS by Google (introductory rate);
# Muse Wonder on OpenRouter's Meta listing (no separate audio-input price shown). Muse Spark 1.3 is priced by
# issa_as.budget and logged with its USD directly.
PRICES = {TTS_MODEL: (0.50, 9.00), CHECK_MODEL: (4.00, 20.00)}


def _key(env_name: str, store_label: str) -> str:
    """Key from the environment, else from the ISSASODA key store. Never printed."""
    sys.path.insert(0, str(ROOT / "src"))
    from issa_as.dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if os.environ.get(env_name):
        return os.environ[env_name]
    store = Path(os.environ.get("ISSASODA_KEYS_DIR", "D:/SC/AK"))
    sys.path.insert(0, str(store))
    import issa_keys  # noqa: PLC0415

    value = issa_keys.key(store_label)
    if not value:
        raise SystemExit(
            f"missing key: set {env_name} or add label '{store_label}' to the key store"
        )
    return value


def _usd(model: str, in_tokens: int, out_tokens: int) -> float:
    price_in, price_out = PRICES[model]
    return round((in_tokens * price_in + out_tokens * price_out) / 1e6, 6)


def _tokens(entry: dict) -> tuple[int, int]:
    usage = entry.get("usage") or {}
    return (
        entry.get("in_tokens") or usage.get("prompt_tokens") or 0,
        entry.get("out_tokens") or usage.get("completion_tokens") or 0,
    )


def _log_cost(entry: dict) -> None:
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    if entry.get("usd") is None and entry.get("model") in PRICES:
        entry["usd"] = _usd(entry["model"], *_tokens(entry))
    with COSTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _load() -> dict:
    return json.loads(SCRIPT.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    SCRIPT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- translate (Meta Muse Spark 1.3)
def translate() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from issa_as.dotenv import load_dotenv
    from issa_as.providers import meta_provider

    load_dotenv(ROOT / ".env")
    data = _load()
    source = {
        "title": data["title"]["en"],
        **({"banner": data["banner"]["en"]} if "banner" in data else {}),
        "scenes": {
            s["id"]: {"narration": s["narration"]["en"], "labels": s["labels"]["en"]}
            for s in data["scenes"]
        },
    }
    prompt = (
        "Translate this narration and these slide labels for a 3-minute product video into Latin American "
        'Spanish (key "es", neutral, not Spain) and Brazilian Portuguese (key "pt").\n'
        "Rules: natural spoken style for a voice-over, similar length to the English (do not expand); keep "
        "product and model names exactly (ISSA AS, Muse Spark Open, Muse Spark 1.3, Muse Code, GitHub, "
        "GitHub Actions, SECURITY.md, ansible-runner, keynote-parser, issa verify, issa replay, issa scout, "
        "issa eval verifier, pytest, Meta Global AI Developer Hackathon 2026); keep every "
        "number and fact; numbers spelled out in the English narration stay spelled out in words; slide "
        "labels stay short. Keep the same JSON keys.\n"
        'Return only JSON: {"es": {"title": ..., "scenes": {<id>: {"narration": ..., "labels": {...}}}}, '
        '"pt": {same shape}}.\n\n' + json.dumps(source, ensure_ascii=False)
    )
    completion = meta_provider(TRANSLATE_MODEL).complete(
        stage="video-translate",
        prompt=prompt,
        effort="medium",
        run_id="video-20261103",
        traces_dir=ROOT / "traces",
        max_tokens=12000,
    )
    _log_cost(
        {
            "step": "translate",
            "provider": "meta",
            "model": TRANSLATE_MODEL,
            "in_tokens": completion.in_tokens,
            "out_tokens": completion.out_tokens,
            "cached_tokens": completion.cached_tokens,
            "usd": completion.cost_usd,
        }
    )
    text = completion.text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    (VIDEO / "out").mkdir(exist_ok=True)
    (VIDEO / "out" / "translate-raw.txt").write_text(completion.text, encoding="utf-8")
    result = json.loads(text)
    for lang in ("es", "pt"):
        data["title"][lang] = result[lang]["title"]
        if "banner" in data:
            data["banner"][lang] = result[lang]["banner"]
        for scene in data["scenes"]:
            got = result[lang]["scenes"][scene["id"]]
            scene["narration"][lang] = got["narration"]
            scene["labels"][lang] = got["labels"]
    for scene in data["scenes"]:  # reviewed by the administrator: currency in local style
        if scene["id"] == "s08-built":
            scene["labels"]["es"]["k3"] = "≈ $1.80 de $150"
            scene["labels"]["pt"]["k2"] = "Build ≈ US$ 0,70 · fixes US$ 1,07"
            scene["labels"]["pt"]["k3"] = "≈ US$ 1,80 de US$ 150"
    _save(data)
    print(
        f"translated: {completion.in_tokens} in / {completion.out_tokens} out tokens, ${completion.cost_usd}"
    )


# ---------------------------------------------------------------- voice (Google Gemini TTS)
def _tts(text: str, lang: str, key: str) -> tuple[bytes, dict]:
    # Text only: an inline style prompt was sometimes read aloud, and this model rejects a system
    # instruction ("Developer instruction is not enabled"). The voice is chosen below; the language
    # follows the text.
    body = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": TTS_VOICE}}},
        },
    }
    req = urllib.request.Request(
        GEMINI_URL.format(model=TTS_MODEL),
        data=json.dumps(body).encode(),
        method="POST",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(float(e.headers.get("Retry-After") or 5 * 2**attempt))
                continue
            raise SystemExit(f"gemini tts http {e.code}: {e.read()[:300]!r}")
    part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
    rate = (
        int(re.search(r"rate=(\d+)", part.get("mimeType", "")).group(1))
        if "rate=" in part.get("mimeType", "")
        else 24000
    )
    usage = data.get("usageMetadata", {})
    return base64.b64decode(part["data"]), {"rate": rate, **usage}


def voice(langs: list[str], only: str | None = None) -> None:
    key = _key("GEMINI_API_KEY", "gemini")
    data = _load()
    last = 0.0
    for lang in langs:
        (OUT / lang).mkdir(parents=True, exist_ok=True)
        for scene in data["scenes"]:
            if only and scene["id"] != only:
                continue
            wav = OUT / lang / f"{scene['id']}.wav"
            if wav.exists() and not only:
                continue
            time.sleep(max(0.0, MIN_GAP_S - (time.time() - last)))
            pcm, meta = _tts(scene["narration"][lang], lang, key)
            last = time.time()
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(meta["rate"])
                w.writeframes(pcm)
            seconds = len(pcm) / 2 / meta["rate"]
            _log_cost(
                {
                    "step": "voice",
                    "provider": "google",
                    "model": TTS_MODEL,
                    "lang": lang,
                    "scene": scene["id"],
                    "seconds": round(seconds, 2),
                    "in_tokens": meta.get("promptTokenCount"),
                    "out_tokens": meta.get("candidatesTokenCount"),
                }
            )
            print(f"{lang} {scene['id']}: {seconds:.1f}s")


# ---------------------------------------------------------------- check (Meta Muse Voice Transcribe)
def _transcribe(wav: Path, lang: str, key: str) -> dict:
    # muse-voice-transcribe-1.0 is listed by /v1/models but not callable on this account (404 on
    # /audio/transcriptions and on chat), so Muse Wonder, which accepts audio input, listens instead.
    body = {
        "model": CHECK_MODEL,
        "reasoning_effort": "low",
        "max_tokens": 3000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Transcribe this {LANG_NAME[lang]} audio verbatim, from the first word to "
                        "the last. Reply with the transcript only.",
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": base64.b64encode(wav.read_bytes()).decode(),
                            "format": "wav",
                        },
                    },
                ],
            }
        ],
    }
    req = urllib.request.Request(
        META_API + "/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
            return {
                "text": data["choices"][0]["message"].get("content") or "",
                "usage": data.get("usage"),
            }
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(float(e.headers.get("Retry-After") or 5 * 2**attempt))
                continue
            raise SystemExit(f"transcribe http {e.code}: {e.read()[:300]!r}")
    raise SystemExit("transcribe retries exhausted")


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def check(langs: list[str]) -> None:
    key = _key("MODEL_API_KEY", "meta")
    data = _load()
    report = []
    last = 0.0
    for lang in langs:
        for scene in data["scenes"]:
            wav = OUT / lang / f"{scene['id']}.wav"
            time.sleep(max(0.0, MIN_GAP_S - (time.time() - last)))
            result = _transcribe(wav, lang, key)
            last = time.time()
            heard = result.get("text", "")
            ratio = difflib.SequenceMatcher(
                None, _words(scene["narration"][lang]), _words(heard)
            ).ratio()
            _log_cost(
                {
                    "step": "check",
                    "provider": "meta",
                    "model": CHECK_MODEL,
                    "lang": lang,
                    "scene": scene["id"],
                    "usage": result.get("usage"),
                }
            )
            report.append(
                {"lang": lang, "scene": scene["id"], "match": round(ratio, 3), "heard": heard}
            )
            print(f"{lang} {scene['id']}: word match {ratio:.0%}")
    (VIDEO / ("check.json" if VERSION == "v1" else f"check-{VERSION}.json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------- slides (headless Edge, real data)
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
ANSIBLE = "20261102-223630-ansible-ansible-runner-1548"
KEYNOTE = "20261102-201027-psobot-keynote-parser-91"
CSS = """
*{box-sizing:border-box;margin:0}
body{width:1920px;height:1080px;overflow:hidden;color:#e8ecf6;padding:90px 120px;font-family:'Segoe UI',system-ui,sans-serif;
  background:radial-gradient(1200px 700px at 85% -10%,#1a2a55 0,#0b1020 60%)}
h1{font-size:72px;font-weight:700;letter-spacing:-1px;margin-bottom:48px}
.big{font-size:150px;font-weight:800;letter-spacing:-3px}
.sub{font-size:44px;color:#aeb8d4;margin-top:20px;max-width:1500px}
.foot{position:absolute;left:120px;bottom:80px;font-size:30px;color:#7f8bab}
.list{font-size:46px;line-height:1.5}
.list div{padding-left:56px;position:relative}
.list div:before{content:'';position:absolute;left:0;top:28px;width:22px;height:22px;border-radius:6px;background:#4c8dff}
.chips{display:flex;flex-wrap:wrap;gap:16px;margin:10px 0 50px}
.chip{font-size:30px;padding:10px 24px;border:2px solid #2d4580;border-radius:40px;color:#c9d6ff}
table{border-collapse:collapse;font-size:32px;width:100%}
th{text-align:left;color:#7f8bab;font-weight:600;padding:12px 18px;border-bottom:2px solid #2d4580}
td{padding:14px 18px;border-bottom:1px solid #1c2a50}
tr.pick td{background:rgba(76,141,255,.18);color:#fff;font-weight:600}
.note{font-size:34px;color:#aeb8d4;margin-top:36px}
pre{font-family:'Cascadia Code',Consolas,monospace;font-size:25px;line-height:1.42;background:#070b16;border:1px solid #1c2a50;
  border-radius:16px;padding:26px 30px;white-space:pre;overflow:hidden}
.add{color:#7ee2a8}.del{color:#ff8f8f}.hun{color:#7f8bab}
.row{display:flex;gap:48px;align-items:flex-start}.col{flex:1;min-width:0}
.steps{display:flex;flex-direction:column;gap:22px;font-size:40px}
.step{padding:26px 34px;border-radius:18px;background:#121a33;border:2px solid #23345f}
.step b{color:#4c8dff;margin-right:14px}.ok{border-color:#2f9e63}.bad{border-color:#b04a4a}
.verd{margin-top:46px;font-size:34px;letter-spacing:2px;color:#c9d6ff;font-family:'Cascadia Code',Consolas,monospace}
.card{background:#121a33;border:2px solid #23345f;border-radius:18px;padding:28px 34px;font-size:36px;margin-bottom:24px}
.badge{display:inline-block;background:#1f7a4a;color:#fff;font-weight:700;font-size:26px;padding:6px 16px;border-radius:10px;
  margin-right:18px;vertical-align:middle}
.mono{font-family:'Cascadia Code',Consolas,monospace;font-size:26px;color:#9fb0d8}
.quote{font-size:38px;border-left:8px solid #4c8dff;padding:10px 30px;color:#dfe6fa;margin:40px 0 30px}
.bar{height:56px;border-radius:12px;background:#121a33;border:2px solid #23345f;overflow:hidden;margin-top:30px}
.bar i{display:block;height:100%;background:#4c8dff}
.kpi{font-size:56px;font-weight:700;margin:18px 0}.dim{color:#7f8bab}
.shot{width:100%;border-radius:18px;border:2px solid #23345f}
.links{font-size:40px;line-height:1.6;color:#c9d6ff;font-family:'Cascadia Code',Consolas,monospace}
"""
CLASSES = [
    "path traversal",
    "command injection",
    "SQL injection",
    "SSRF",
    "ReDoS",
    "open redirect",
    "XXE",
    "unsafe deserialization",
    "XSS",
    "auth bypass",
    "secret leakage",
]


def _esc(text: object) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _patch_excerpt() -> str:
    # the zip-member checks and where extraction now uses them (patch lines 25-37 and 49, 52-58)
    lines = (ROOT / "proof" / ANSIBLE / "fix.patch").read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[24:28] + lines[30:37] + ["…"] + lines[48:49] + lines[51:58]:
        cls = (
            "add"
            if line.startswith("+")
            else "del"
            if line.startswith("-")
            else "hun"
            if line.startswith("@@")
            else ""
        )
        out.append(f'<span class="{cls}">{_esc(line[:92])}</span>')
    return "\n".join(out)


def _triage_rows() -> str:
    report = json.loads(
        (ROOT / "triage" / "triage-scout-20261102-222258.json").read_text(encoding="utf-8")
    )
    rows = []
    for item in sorted(report["scored"], key=lambda x: -x["final_score"])[:7]:
        pick = item["repo"] == "ansible/ansible-runner" and item["number"] == 1548
        critic = "—" if item.get("solvability_v2") is None else f"{item['solvability_v2']:.2f}"
        rows.append(
            f'<tr class="{"pick" if pick else ""}"><td>{_esc(item["repo"])} #{item["number"]}</td>'
            f"<td>{_esc(item.get('security_class') or '')}</td><td>{item.get('solvability') or 0:.2f}</td>"
            f"<td>{critic}</td><td>{item['final_score']:.3f}</td></tr>"
        )
    return "".join(rows)


def _trace_lines() -> str:
    raw = (ROOT / "traces" / f"{KEYNOTE}.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(x) for x in raw]
    lines = [
        f"seq {e['seq']:>2}  {(e['type'] + ' ' + e.get('verdict', '')).strip():<25} prev {e['prev_hash'][:12]}…  "
        f"hash {e['hash'][:12]}…"
        for e in events[:3] + events[-1:]
    ]
    return "\n".join(lines[:3] + ["  …"] + lines[3:])


def _slide(scene: dict, lang: str) -> str:
    lb = {k: _esc(v) for k, v in scene["labels"][lang].items()}
    v = scene["visual"]
    if v == "title":
        body = (
            f'<div style="margin-top:220px"><div class="big">{lb["h"]}</div><div class="sub">{lb["sub"]}</div></div>'
            f'<div class="foot">{lb["foot"]}</div>'
        )
    elif v == "scout":
        chips = "".join(f'<span class="chip">{c}</span>' for c in CLASSES)
        body = (
            f'<h1>{lb["h"]}</h1><div class="note" style="margin:0 0 14px">{lb["classes"]}</div>'
            f'<div class="chips">{chips}</div>'
            f'<div class="list"><div>{lb["f1"]}</div><div>{lb["f2"]}</div><div>{lb["f3"]}</div></div>'
        )
    elif v == "triage":
        head = "".join(f"<th>{lb[k]}</th>" for k in ("c1", "c2", "c3", "c4", "c5"))
        body = f'<h1>{lb["h"]}</h1><table><tr>{head}</tr>{_triage_rows()}</table><div class="note">{lb["note"]}</div>'
    elif v == "solve":
        body = (
            f'<h1>{lb["h"]}</h1><div class="row"><div style="width:520px" class="steps">'
            f'<div class="step bad"><b>1</b>{lb["t1"]}</div><div class="step ok"><b>2</b>{lb["t2"]}</div>'
            f'<div class="note" style="margin-top:10px">{lb["t3"]}</div></div>'
            f'<div class="col"><pre>{_patch_excerpt()}</pre>'
            f'<div class="mono" style="margin-top:12px">ansible-runner · fix.patch</div></div></div>'
        )
    elif v == "verify":
        body = (
            f'<h1>{lb["h"]}</h1><div class="steps"><div class="step bad"><b>✗</b>{lb["b1"]}</div>'
            f'<div class="step ok"><b>✓</b>{lb["b2"]}</div><div class="step ok"><b>✓</b>{lb["b3"]}</div></div>'
            f'<div class="verd">{lb["v"]}</div>'
        )
    elif v == "proof":
        body = (
            f'<h1>{lb["h"]}</h1><div class="card"><span class="badge">VERIFIED · LINUX</span>{lb["r1"]}</div>'
            f'<div class="card"><span class="badge">VERIFIED · LINUX</span>{lb["r2"]}</div>'
            f'<div class="note" style="margin:26px 0 8px">{lb["ci"]}</div>'
            f'<div class="mono">github.com/Ataraxiainc2/ISSAAS/actions/runs/41613094986</div>'
            f'<div class="note" style="margin:30px 0 12px">{lb["tr"]}</div>'
            f'<pre style="font-size:24px">{_esc(_trace_lines())}</pre>'
        )
    elif v == "safety":
        body = (
            f'<h1>{lb["h"]}</h1><div class="list"><div>{lb["p1"]}</div><div>{lb["p2"]}</div>'
            f'<div>{lb["p3"]}</div></div><div class="quote">“MUST NOT be reported through public channels such as '
            f'GitHub issues, pull requests”<div class="mono" style="margin-top:10px">ansible-runner · SECURITY.md</div></div>'
            f'<pre style="font-size:26px">{lb["draft"]}\nTo: security@ansible.com\n'
            f"proof/{ANSIBLE}/disclosure.md</pre>"
        )
    elif v == "built":
        body = (
            f'<h1>{lb["h"]}</h1><div class="kpi">{lb["k1"]}</div><div class="kpi dim">{lb["k2"]}</div>'
            f'<div class="kpi" style="margin-top:60px">{lb["k3"]}</div><div class="bar"><i style="width:1.2%"></i></div>'
            f'<div class="mono" style="margin-top:30px">docs/AUDIT_LOG.md · prompts/ · muse-runs/</div>'
        )
    else:  # close
        shot = OUT / "panel-crop.png"
        img = f'<img class="shot" src="{shot.as_uri()}">' if shot.exists() else ""
        body = (
            f'<div class="row"><div style="width:700px"><h1 style="margin-top:110px">{lb["h"]}</h1>'
            f'<div class="links">{lb["l1"]}<br>{lb["l2"]}</div></div>'
            f'<div class="col" style="margin-top:40px">{img}</div></div>'
            f'<div class="foot" style="font-size:26px">{lb["credit"]}</div>'
        )
    return f'<!doctype html><html lang="{lang}"><meta charset="utf-8"><style>{CSS}</style><body>{body}</body></html>'


def _screenshot(url: str, png: Path, wait_ms: int = 500) -> None:
    profile = ROOT / ".cache" / "edge-headless"
    subprocess.run(
        [
            EDGE,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            f"--user-data-dir={profile}",
            "--window-size=1920,1080",
            f"--virtual-time-budget={wait_ms}",
            f"--screenshot={png}",
            url,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def slides(langs: list[str]) -> None:
    data = _load()
    for lang in langs:
        (OUT / lang).mkdir(parents=True, exist_ok=True)
        for scene in data["scenes"]:
            if "clip" in scene:  # v2: live screen recording, no slide
                continue
            html = OUT / lang / f"{scene['id']}.html"
            html.write_text(_slide(scene, lang), encoding="utf-8")
            _screenshot(html.as_uri(), OUT / lang / f"{scene['id']}.png")
            print(f"{lang} {scene['id']}.png")


# ---------------------------------------------------------------- build (ffmpeg)
LEAD_S, TAIL_S, FADE_S = 0.4, 0.6, 0.3


def _wav_seconds(wav: Path) -> float:
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / w.getframerate()


def _srt_time(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def build(langs: list[str]) -> None:
    data = _load()
    for lang in langs:
        work = OUT / lang
        clips, srt, t, n = [], [], 0.0, 1
        for scene in data["scenes"]:
            wav, png, clip = (
                work / f"{scene['id']}.wav",
                work / f"{scene['id']}.png",
                work / f"{scene['id']}.mp4",
            )
            speech = _wav_seconds(wav)
            dur = LEAD_S + speech + TAIL_S
            _ffmpeg(
                [
                    "-loop",
                    "1",
                    "-framerate",
                    "30",
                    "-i",
                    str(png),
                    "-i",
                    str(wav),
                    "-filter_complex",
                    f"[0:v]format=yuv420p,fade=t=in:st=0:d={FADE_S},fade=t=out:st={dur - FADE_S:.3f}:d={FADE_S}[v];"
                    f"[1:a]adelay={int(LEAD_S * 1000)}:all=1,apad,aresample=48000,aformat=channel_layouts=stereo[a]",
                    "-map",
                    "[v]",
                    "-map",
                    "[a]",
                    "-t",
                    f"{dur:.3f}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-tune",
                    "stillimage",
                    "-crf",
                    "18",
                    "-r",
                    "30",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    str(clip),
                ]
            )
            clips.append(clip)
            # subtitles: one cue per sentence, timed by its share of the scene's characters
            sentences = [
                x for x in re.split(r"(?<=[.!?:])\s+", scene["narration"][lang].strip()) if x
            ]
            total = sum(len(x) for x in sentences)
            start = t + LEAD_S
            for sentence in sentences:
                end = start + speech * len(sentence) / total
                srt.append(f"{n}\n{_srt_time(start)} --> {_srt_time(end)}\n{sentence}\n")
                n, start = n + 1, end
            t += dur
        listing = work / "clips.txt"
        listing.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
        final = OUT / f"ISSA-AS-{lang}.mp4"
        _ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c:v",
                "copy",
                "-af",
                "loudnorm=I=-14:TP=-1.5:LRA=11",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                str(final),
            ]
        )
        (OUT / f"ISSA-AS-{lang}.srt").write_text("\n".join(srt), encoding="utf-8")
        print(f"{lang}: {final.name} {t:.1f}s, {n - 1} subtitle cues")


# ---------------------------------------------------------------- v2: music bed (generated here, no samples)
MUSIC_RATE = 24000
NOTE = {
    n: 440.0 * 2 ** ((i - 9) / 12) for i, n in enumerate("C C# D D# E F F# G G# A A# B".split())
}


def _hz(name: str, octave: int) -> float:
    return NOTE[name] * 2 ** (octave - 4)


def music(seconds: float) -> Path:
    """An original, quiet synthwave bed: minor-key bass arpeggio, soft pad, muted pulse. Deterministic."""
    import array
    import math

    bpm, n = 96.0, int(seconds * MUSIC_RATE)
    beat = 60.0 / bpm
    # Am - F - C - G, one bar (4 beats) each: (root, chord tones) with octaves
    bars = [
        ("A", ["A", "C", "E"]),
        ("F", ["F", "A", "C"]),
        ("C", ["C", "E", "G"]),
        ("G", ["G", "B", "D"]),
    ]
    arp_pattern = [0, 1, 2, 1, 0, 2, 1, 2]  # eighth notes over the chord tones
    out = array.array("h")
    two_pi = 2 * math.pi
    for i in range(n):
        t = i / MUSIC_RATE
        bar = bars[int(t / (4 * beat)) % 4]
        # bass arpeggio: eighth notes, soft saw (3 harmonics), short decay
        step = int(t / (beat / 2))
        tone = bar[1][arp_pattern[step % 8]]
        f_bass = _hz(tone, 2)
        tb = t - step * beat / 2
        env_b = math.exp(-tb * 6.0)
        ph = two_pi * f_bass * t
        bass = (math.sin(ph) + 0.5 * math.sin(2 * ph) + 0.25 * math.sin(3 * ph)) * env_b * 0.32
        # pad: chord tones one octave up, slow swell, slight detune
        tbar = (t % (4 * beat)) / (4 * beat)
        env_p = 0.55 + 0.45 * math.sin(math.pi * tbar)
        pad = (
            sum(
                math.sin(two_pi * _hz(x, 4) * t) + math.sin(two_pi * _hz(x, 4) * 1.003 * t)
                for x in bar[1]
            )
            * env_p
            * 0.045
        )
        # muted pulse on every beat (a low thump, not a drum kit)
        tk = t % beat
        kick = math.sin(two_pi * (48 + 60 * math.exp(-tk * 30)) * tk) * math.exp(-tk * 14) * 0.35
        # fade in over 3 s and out over the last 4 s
        fade = min(1.0, t / 3.0, max(0.0, (seconds - t) / 4.0))
        out.append(int(max(-1.0, min(1.0, (bass + pad + kick) * fade * 0.7)) * 32767))
    raw = OUT / "music-raw.wav"
    raw.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(MUSIC_RATE)
        w.writeframes(out.tobytes())
    final = OUT / "music.wav"
    _ffmpeg(
        [
            "-i",
            str(raw),
            "-af",
            "lowpass=f=2400,aecho=0.8:0.6:180|360:0.25|0.15,aresample=48000,"
            "aformat=channel_layouts=stereo",
            str(final),
        ]
    )
    return final


# ---------------------------------------------------------------- v2: build from screen recordings
FONT = "C\\:/Windows/Fonts/segoeui.ttf"
FONT_BOLD = "C\\:/Windows/Fonts/segoeuib.ttf"
BANNER_S = 18.0  # provenance banner stays on screen for the first 18 seconds
MAX_SPEED = 2.5  # a recording longer than its narration is sped up at most this much, then cut
MUSIC_GAIN = 0.22  # music level before ducking under the voice


def _probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    return float(out)


def _text_file(work: Path, name: str, text: str) -> str:
    (work / name).write_text(text, encoding="utf-8")
    return name  # ffmpeg runs with cwd=work, so a bare name needs no escaping


def build_v2(langs: list[str]) -> None:
    data = _load()
    for lang in langs:
        work = OUT / lang
        clips, srt, t, n = [], [], 0.0, 1
        for scene in data["scenes"]:
            wav = work / f"{scene['id']}.wav"
            out_clip = work / f"{scene['id']}.mp4"
            speech = _wav_seconds(wav)
            dur = LEAD_S + speech + TAIL_S
            labels = scene["labels"][lang]
            overlays = []
            tag_y = 130 if t < BANNER_S else 40
            if labels.get("tag"):
                name = _text_file(work, f"{scene['id']}-tag.txt", labels["tag"])
                overlays.append(
                    f"drawtext=fontfile='{FONT_BOLD}':textfile={name}:fontsize=34:fontcolor=white:"
                    f"box=1:boxcolor=0x0b1020@0.82:boxborderw=18:x=40:y={tag_y}"
                )
            if labels.get("metric"):
                name = _text_file(work, f"{scene['id']}-metric.txt", labels["metric"])
                overlays.append(
                    f"drawtext=fontfile='{FONT_BOLD}':textfile={name}:fontsize=40:fontcolor=0x7ee2a8:"
                    f"box=1:boxcolor=0x0b1020@0.85:boxborderw=20:x=w-tw-60:y={tag_y}"
                )
            if "clip" in scene:
                src = REC / f"{scene['clip']}.mp4"
                if not src.is_file():
                    raise SystemExit(
                        f"missing recording: {src.relative_to(ROOT)} (see video/SHOTS.md)"
                    )
                start = float(scene.get("clip_start", 0))
                left = max(0.1, _probe_seconds(src) - start)
                if scene.get("clip_len"):  # use only this window of the recording
                    left = min(left, float(scene["clip_len"]))
                speed = min(MAX_SPEED, left / dur) if left > dur else 1.0
                inputs = ["-ss", f"{start:.2f}", "-t", f"{left:.2f}", "-i", str(src)]
                base = (
                    f"[0:v]setpts=PTS/{speed:.4f},fps=30,"
                    "scale=1920:1080:force_original_aspect_ratio=decrease,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x0b1020,setsar=1,"
                    f"tpad=stop_mode=clone:stop_duration={dur:.3f}"
                )
            else:
                inputs = ["-loop", "1", "-framerate", "30", "-i", str(work / f"{scene['id']}.png")]
                base = "[0:v]setsar=1"
            vf = ",".join(
                [
                    base,
                    *overlays,
                    "format=yuv420p",
                    f"fade=t=in:st=0:d={FADE_S}",
                    f"fade=t=out:st={dur - FADE_S:.3f}:d={FADE_S}[v]",
                ]
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    *inputs,
                    "-i",
                    str(wav),
                    "-filter_complex",
                    vf + f";[1:a]adelay={int(LEAD_S * 1000)}:all=1,apad,aresample=48000,"
                    "aformat=channel_layouts=stereo[a]",
                    "-map",
                    "[v]",
                    "-map",
                    "[a]",
                    "-t",
                    f"{dur:.3f}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "18",
                    "-r",
                    "30",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    out_clip.name,
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                cwd=work,
            )
            clips.append(out_clip)
            sentences = [
                x for x in re.split(r"(?<=[.!?:])\s+", scene["narration"][lang].strip()) if x
            ]
            total = sum(len(x) for x in sentences)
            cue = t + LEAD_S
            for sentence in sentences:
                end = cue + speech * len(sentence) / total
                srt.append(f"{n}\n{_srt_time(cue)} --> {_srt_time(end)}\n{sentence}\n")
                n, cue = n + 1, end
            t += dur
            print(f"{lang} {scene['id']}: {dur:.1f}s")
        (work / "clips.txt").write_text(
            "".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8"
        )
        _ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(work / "clips.txt"),
                "-c",
                "copy",
                str(work / "joined.mp4"),
            ]
        )
        srt_name = f"ISSA-AS-{lang}.srt"
        (work / srt_name).write_text("\n".join(srt), encoding="utf-8")
        (OUT / srt_name).write_text("\n".join(srt), encoding="utf-8")
        bed = OUT / "music.wav"
        if not bed.is_file() or _wav_seconds(bed) < t + 1:
            bed = music(t + 2)
        banner = _text_file(work, "banner.txt", data["banner"][lang])
        style = (
            "FontName=Segoe UI,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,"
            "BorderStyle=4,Outline=0,Shadow=0,MarginV=45"
        )
        video = (
            f"[0:v]drawtext=fontfile='{FONT_BOLD}':textfile={banner}:fontsize=40:fontcolor=white:"
            f"box=1:boxcolor=0x1a3fbf@0.92:boxborderw=22:x=(w-tw)/2:y=40:enable='lt(t,{BANNER_S})',"
            f"subtitles={srt_name}:force_style='{style}'[v]"
        )
        audio = (
            f"[0:a]asplit=2[voice][key];[1:a]volume={MUSIC_GAIN}[bed];"
            "[bed][key]sidechaincompress=threshold=0.015:ratio=10:attack=15:release=450[duck];"
            "[voice][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        )
        final = OUT / f"ISSA-AS-{lang}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "joined.mp4",
                "-i",
                str(bed),
                "-filter_complex",
                video + ";" + audio,
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "19",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                str(final),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            cwd=work,
        )
        print(f"{lang}: {final.relative_to(ROOT)} {t:.1f}s, {n - 1} burned-in captions")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    langs = [a for a in sys.argv[2:] if a in LANGS] or LANGS
    only = next((a for a in sys.argv[2:] if a.startswith("s0")), None)
    if cmd == "translate":
        translate()
    elif cmd == "voice":
        voice(langs, only)
    elif cmd == "check":
        check(langs)
    elif cmd == "slides":
        slides(langs)
    elif cmd == "build":
        build(langs) if VERSION == "v1" else build_v2(langs)
    elif cmd == "music":
        print(music(float(sys.argv[2]) if len(sys.argv) > 2 else 180.0).relative_to(ROOT))
    elif cmd == "costs":
        totals: dict[tuple[str, str], list[float]] = {}
        for raw in COSTS.read_text(encoding="utf-8").splitlines():
            entry = json.loads(raw)
            in_t, out_t = _tokens(entry)
            usd = entry.get("usd")
            if usd is None:  # entries logged before prices were added
                usd = _usd(entry["model"], in_t, out_t)
            row = totals.setdefault((entry["step"], entry["model"]), [0, 0, 0, 0.0])
            row[0], row[1], row[2], row[3] = row[0] + 1, row[1] + in_t, row[2] + out_t, row[3] + usd
        for (step, model), (calls, in_t, out_t, usd) in totals.items():
            print(
                f"{step:<10} {model:<22} {calls:>3} calls {in_t:>7} in {out_t:>7} out  ${usd:.4f}"
            )
        print(f"total ${sum(r[3] for r in totals.values()):.4f}")
    elif cmd == "panel":  # screenshot of the running local panel (`issa serve --port 8780`)
        _screenshot("http://127.0.0.1:8780/", OUT / "panel.png", wait_ms=6000)
        # header, tabs and the three keynote-parser runs (the panel does not read linux-verify.json yet)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(OUT / "panel.png"),
                "-vf",
                "crop=760:392:580:0",
                str(OUT / "panel-crop.png"),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
        )
    else:
        raise SystemExit(__doc__)
