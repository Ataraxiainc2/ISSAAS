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
import html
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


def check(langs: list[str], only: str | None = None) -> None:
    key = _key("MODEL_API_KEY", "meta")
    data = _load()
    report = []
    last = 0.0
    for lang in langs:
        for scene in data["scenes"]:
            if only and scene["id"] != only:
                continue
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
    path = VIDEO / ("check.json" if VERSION == "v1" else f"check-{VERSION}.json")
    if only and path.is_file():  # one scene re-checked: replace its rows, keep the rest
        kept = [
            r
            for r in json.loads(path.read_text(encoding="utf-8"))
            if not (r["scene"] == only and r["lang"] in langs)
        ]
        report = kept + report
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _screenshot(
    url: str, png: Path, wait_ms: int = 500, size: tuple[int, int] = (1920, 1080), scale: float = 1
) -> None:
    profile = ROOT / ".cache" / "edge-headless"
    subprocess.run(
        [
            EDGE,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            f"--user-data-dir={profile}",
            f"--window-size={size[0]},{size[1]}",
            f"--force-device-scale-factor={scale}",
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
    import random

    rng = random.Random(1548)  # fixed seed: the same bed on every build

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
        # pluck arpeggio two octaves above the bass, sixteenth notes: it carries the bed on laptop
        # and phone speakers, which barely play the bass and the pulse
        s16 = int(t / (beat / 4))
        tp = t - s16 * beat / 4
        f_p = _hz(bar[1][(0, 1, 2, 1, 2, 0, 1, 2)[s16 % 8]], 5)
        pluck = (
            (math.sin(two_pi * f_p * t) + 0.3 * math.sin(two_pi * 2 * f_p * t))
            * math.exp(-tp * 16)
            * (0.11 if s16 % 4 == 0 else 0.07)
        )
        # soft off-beat hat: deterministic noise with a fast decay
        th = (t + beat / 2) % beat
        hat = (rng.random() * 2 - 1) * math.exp(-th * 70) * 0.045
        # fade in over 3 s and out over the last 4 s
        fade = min(1.0, t / 3.0, max(0.0, (seconds - t) / 4.0))
        mix = bass + pad + kick + pluck + hat
        out.append(int(max(-1.0, min(1.0, mix * fade * 0.7)) * 32767))
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
            "lowpass=f=7000,aecho=0.8:0.6:180|360:0.25|0.15,aresample=48000,"
            "aformat=channel_layouts=stereo",
            str(final),
        ]
    )
    return final


# ---------------------------------------------------------------- v2: build from screen recordings
# Output is 2560x1440: YouTube serves 1440p uploads with a better codec and bitrate than 1080p, so the
# recorded UI text stays sharp. Each live clip sits in a "frame": a dark canvas with a header bar (step,
# what is running, key metric) rendered from HTML, so labels never cover the product.
VW, VH = 2560, 1440
CLIP_X, CLIP_Y, CLIP_W, CLIP_H = (
    160,
    150,
    2240,
    1260,
)  # record.py records at exactly CLIP_W x CLIP_H
MAX_SPEED = 2.5  # a recording longer than its narration is sped up at most this much, then cut
MUSIC_GAIN = 0.7  # music level before ducking; about 12 dB under the voice while it speaks
XFADE_S = 0.5  # title card to live footage
CAPTION_CHARS = 64  # one caption line at most
CAPTION_MIN = 22  # shorter lines are merged or rebalanced
LOGO = VIDEO / "assets" / "issa-as-logo.svg"

FRAME_CSS = """
html,body{margin:0;width:1280px;height:720px;overflow:hidden}
body{font-family:'Segoe UI',system-ui,sans-serif;color:#e8ecf6;
background:radial-gradient(760px 380px at 0% 0%,rgba(45,212,191,.13),transparent 62%),
radial-gradient(820px 460px at 100% 100%,rgba(96,165,250,.12),transparent 62%),#0b1020}
.bar{position:absolute;left:80px;right:80px;top:0;height:75px;display:flex;align-items:center;gap:14px}
.logo{width:32px;height:32px}
.brand{font-weight:800;font-size:16px;letter-spacing:.06em;margin-right:6px}
.brand b{color:#60a5fa}
.step{display:flex;align-items:center;gap:10px;background:#121a2e;border:1px solid #26314d;
border-radius:999px;padding:5px 16px 5px 5px}
.num{width:26px;height:26px;border-radius:50%;background:#2dd4bf;color:#0b1020;font-weight:800;
font-size:14px;display:flex;align-items:center;justify-content:center}
.name{font-weight:700;font-size:17px}
.detail{color:#9aa6c2;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.metric{margin-left:auto;white-space:nowrap;font-weight:800;font-size:18px;color:#34d399;
background:rgba(52,211,153,.10);border:1px solid rgba(52,211,153,.38);border-radius:999px;padding:6px 18px}
.win{position:absolute;left:79px;top:74px;width:1122px;height:632px;border-radius:4px;
box-shadow:0 0 0 1px #2a3656,0 22px 70px rgba(0,0,0,.6)}
.dot{width:8px;height:8px;border-radius:50%;background:#f87171;box-shadow:0 0 0 4px rgba(248,113,113,.18)}
"""

TITLE_CSS = """
html,body{margin:0;width:1280px;height:720px;overflow:hidden}
body{font-family:'Segoe UI',system-ui,sans-serif;color:#e8ecf6;display:flex;flex-direction:column;
align-items:center;justify-content:center;gap:0;padding-bottom:70px;box-sizing:border-box;
background:radial-gradient(640px 360px at 50% 38%,rgba(45,212,191,.16),transparent 70%),
radial-gradient(900px 500px at 100% 100%,rgba(96,165,250,.10),transparent 60%),#0b1020}
body:before{content:"";position:absolute;inset:0;opacity:.35;
background-image:linear-gradient(#1a2440 1px,transparent 1px),linear-gradient(90deg,#1a2440 1px,transparent 1px);
background-size:48px 48px;mask-image:radial-gradient(closest-side,#000,transparent)}
.pill{position:relative;font-weight:700;font-size:15px;letter-spacing:.08em;text-transform:uppercase;
color:#bfeee6;background:rgba(45,212,191,.10);border:1px solid rgba(45,212,191,.40);border-radius:999px;
padding:8px 20px;margin-bottom:34px}
.logo{position:relative;width:132px;height:132px;margin-bottom:18px;filter:drop-shadow(0 10px 30px rgba(45,212,191,.25))}
h1{position:relative;margin:0;font-size:86px;font-weight:800;letter-spacing:.02em;line-height:1}
h1 b{color:#60a5fa}
p{position:relative;margin:18px 0 0;font-size:28px;color:#9aa6c2;font-weight:500}
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _frame_html(labels: dict) -> str:
    parts = [p.strip() for p in labels.get("tag", "").split("·") if p.strip()]
    num = parts.pop(0) if parts and parts[0].isdigit() else ""
    name = parts.pop(0) if parts else ""
    detail = " · ".join(parts)
    step = (f'<span class="num">{_esc(num)}</span>' if num else '<span class="dot"></span>') + (
        f'<span class="name">{_esc(name)}</span>'
    )
    metric = f'<span class="metric">{_esc(labels["metric"])}</span>' if labels.get("metric") else ""
    return (
        f'<!doctype html><meta charset="utf-8"><style>{FRAME_CSS}</style>'
        f'<div class="bar"><img class="logo" src="{LOGO.as_uri()}"><span class="brand">ISSA <b>AS</b></span>'
        f'<span class="step">{step}</span><span class="detail">{_esc(detail)}</span>{metric}</div>'
        '<div class="win"></div>'
    )


def _title_html(data: dict, lang: str) -> str:
    return (
        f'<!doctype html><html lang="{lang}"><meta charset="utf-8"><style>{TITLE_CSS}</style>'
        f'<div class="pill">{_esc(data["banner"][lang])}</div>'
        f'<img class="logo" src="{LOGO.as_uri()}"><h1>ISSA <b>AS</b></h1>'
        f"<p>{_esc(data['title_card'][lang])}</p></html>"
    )


def _render(html_text: str, png: Path, work: Path) -> Path:
    src = work / (png.stem + ".html")
    src.write_text(html_text, encoding="utf-8")
    _screenshot(src.as_uri(), png, wait_ms=800, size=(1280, 720), scale=2)
    return png


def _chunks(sentence: str) -> list[str]:
    """Split a sentence into caption lines of at most CAPTION_CHARS, at punctuation when possible."""
    if len(sentence) <= CAPTION_CHARS:
        return [sentence]
    pieces = [p for p in re.split(r"(?<=[,;:])\s+", sentence) if p]
    out: list[str] = []
    for piece in pieces:
        while len(piece) > CAPTION_CHARS:
            cut = piece.rfind(" ", 0, CAPTION_CHARS)
            cut = cut if cut > CAPTION_CHARS // 2 else CAPTION_CHARS
            out.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if out and len(out[-1]) + 1 + len(piece) <= CAPTION_CHARS:
            out[-1] = out[-1] + " " + piece
        elif piece:
            out.append(piece)
    # no orphans ("abierto,"): a short line joins its neighbour, or the pair is re-split evenly
    i = 0
    while len(out) > 1 and i < len(out):
        if len(out[i]) >= CAPTION_MIN:
            i += 1
            continue
        j = i - 1 if i > 0 else i + 1
        a, b = min(i, j), max(i, j)
        both = out[a] + " " + out[b]
        if len(both) <= CAPTION_CHARS + 10:
            out[a : b + 1] = [both]
        else:
            mid = len(both) // 2
            left, right = both.rfind(" ", 0, mid), both.find(" ", mid)
            cut = left if right < 0 or (left >= 0 and mid - left <= right - mid) else right
            out[a : b + 1] = [both[:cut].strip(), both[cut:].strip()]
            i = b + 1
    return out


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass(cues: list[tuple[float, float, str, str]]) -> str:
    """Burned-in captions as ASS at the output resolution (ffmpeg's SRT reader drops styling)."""
    fields = (
        "Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    common = "&H00FFFFFF,&H00FFFFFF,&H2820100B,&H2820100B,-1,0,0,0,100,100,0,0,4,16,0,2"
    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {VW}",
        f"PlayResY: {VH}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        f"Format: {fields}",
        f"Style: Default,Segoe UI Semibold,50,{common},200,200,70,1",
        f"Style: High,Segoe UI Semibold,50,{common},200,200,230,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = [
        f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},{style},,0,0,0,,{text}"
        for a, b, style, text in cues
    ]
    return "\n".join(head + events) + "\n"


def _probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    return float(out)


def _scene_video(
    scene: dict, lang: str, work: Path, dur: float, image_s: float
) -> tuple[list, str]:
    """ffmpeg inputs and the filter that makes this scene's [v] stream (audio is input index 'a')."""
    inputs: list[str] = []
    chains: list[str] = []
    still = ROOT / scene["image"].format(lang=lang) if "image" in scene else None
    if "clip" in scene:
        src = REC / f"{scene['clip']}.mp4"
        if not src.is_file():
            raise SystemExit(f"missing recording: {src.relative_to(ROOT)} (see video/SHOTS.md)")
        live = dur - image_s + (XFADE_S if image_s else 0)
        start = float(scene.get("clip_start", 0))
        left = max(0.1, _probe_seconds(src) - start)
        if scene.get("clip_len"):  # use only this window of the recording
            left = min(left, float(scene["clip_len"]))
        speed = min(MAX_SPEED, left / live) if left > live else 1.0
        frame = _render(
            _frame_html(scene.get("labels", {}).get(lang, {})),
            work / f"{scene['id']}-frame.png",
            work,
        )
        inputs += ["-loop", "1", "-framerate", "30", "-t", f"{live:.3f}", "-i", str(frame)]
        inputs += ["-ss", f"{start:.2f}", "-t", f"{left:.2f}", "-i", str(src)]
        chains.append(
            f"[1:v]setpts=PTS/{speed:.4f},fps=30,"
            f"scale={CLIP_W}:{CLIP_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={CLIP_W}:{CLIP_H}:(ow-iw)/2:(oh-ih)/2:color=0x0b1020,setsar=1,"
            f"tpad=stop_mode=clone:stop_duration={live:.3f}[clip]"
        )
        chains.append(
            f"[0:v]setsar=1,fps=30[bg];[bg][clip]overlay={CLIP_X}:{CLIP_Y}:shortest=1,"
            f"format=yuv420p,trim=duration={live:.3f},setpts=PTS-STARTPTS,fps=30[live]"
        )
        if still and image_s:
            inputs += ["-loop", "1", "-framerate", "30", "-t", f"{image_s:.3f}", "-i", str(still)]
            chains.append(
                f"[2:v]scale={VW}:{VH}:flags=lanczos,setsar=1,fps=30,format=yuv420p[card];"
                f"[card][live]xfade=transition=fade:duration={XFADE_S}:offset={image_s - XFADE_S:.3f}[pre]"
            )
        else:
            chains.append("[live]null[pre]")
    else:
        inputs += ["-loop", "1", "-framerate", "30", "-t", f"{dur:.3f}", "-i", str(still)]
        chains.append(f"[0:v]scale={VW}:{VH}:flags=lanczos,setsar=1,fps=30,format=yuv420p[pre]")
    chains.append(f"[pre]fade=t=in:st=0:d={FADE_S},fade=t=out:st={dur - FADE_S:.3f}:d={FADE_S}[v]")
    return inputs, ";".join(chains)


def _cards(data: dict, lang: str, work: Path) -> None:
    """The title card (new, minimal) and Argon's close card, both at 2560x1440."""
    (VIDEO / "assets").mkdir(exist_ok=True)
    _render(_title_html(data, lang), VIDEO / "assets" / f"title-{lang}.png", work)
    close = VIDEO / "assets" / f"close-{lang}.html"
    if close.is_file():
        _screenshot(
            close.resolve().as_uri(),
            VIDEO / "assets" / f"close-{lang}.png",
            wait_ms=1500,
            size=(1920, 1080),
            scale=VW / 1920,
        )


def build_v2(langs: list[str]) -> None:
    data = _load()
    for lang in langs:
        work = OUT / lang
        work.mkdir(parents=True, exist_ok=True)
        _cards(data, lang, work)
        clips, srt, burn, t, n = [], [], [], 0.0, 1
        for scene in data["scenes"]:
            wav = work / f"{scene['id']}.wav"
            out_clip = work / f"{scene['id']}.mp4"
            speech = _wav_seconds(wav)
            dur = LEAD_S + speech + TAIL_S
            sentences = [
                x for x in re.split(r"(?<=[.!?])\s+", scene["narration"][lang].strip()) if x
            ]
            total = sum(len(x) for x in sentences)
            image_s = 0.0
            if scene.get("image_until_sentence") and "clip" in scene:  # card, then live footage
                k = int(scene["image_until_sentence"])
                image_s = LEAD_S + speech * sum(len(x) for x in sentences[:k]) / total + 0.3
            inputs, vf = _scene_video(scene, lang, work, dur, image_s)
            audio_in = inputs.count("-i")
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
                    vf + f";[{audio_in}:a]adelay={int(LEAD_S * 1000)}:all=1,apad,aresample=48000,"
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
                    "veryfast",
                    "-crf",
                    "8",  # intermediate; the final pass is the only real compression
                    "-r",
                    "30",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "256k",
                    out_clip.name,
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                cwd=work,
            )
            clips.append(out_clip)
            style_name = scene.get("caption_style", "Default")
            lines = [c for x in sentences for c in _chunks(x)]
            chars = sum(len(x) for x in lines)
            cue = t + LEAD_S
            for line in lines:
                end = cue + speech * len(line) / chars
                srt.append(f"{n}\n{_srt_time(cue)} --> {_srt_time(end)}\n{line}\n")
                burn.append((cue, end, style_name, line))
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
        srt_name, ass_name = f"ISSA-AS-{lang}.srt", f"ISSA-AS-{lang}.ass"
        (work / ass_name).write_text(_ass(burn), encoding="utf-8")  # burned in
        (OUT / srt_name).write_text("\n".join(srt), encoding="utf-8")  # clean track for YouTube
        bed = OUT / "music.wav"
        if not bed.is_file() or _wav_seconds(bed) < t + 1:
            bed = music(t + 2)
        video = f"[0:v]subtitles={ass_name}[v]"
        audio = (
            f"[0:a]asplit=2[voice][key];[1:a]volume={MUSIC_GAIN}[bed];"
            "[bed][key]sidechaincompress=threshold=0.04:ratio=3:attack=40:release=700[duck];"
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
                "slow",
                "-crf",
                "16",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "256k",
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
        check(langs, only)
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
