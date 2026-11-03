# Demo video: how it was made

Three versions of the same video, about 2.5 minutes each: English, Latin American Spanish, Brazilian Portuguese.
Everything is rebuilt by [`video/make_video.py`](../video/make_video.py) from [`video/script.json`](../video/script.json),
the committed proof bundles, the triage report and the traces. Every number in the video comes from those files.

| Step | Model | Provider | Calls | Cost (USD) |
|---|---|---|---|---|
| Write the English script | — (administrator, from the README and proof bundles) | — | — | — |
| Translate to es-419 and pt-BR | `muse-spark-1.3` | Meta Model API | 3 (one reply was malformed JSON, one was re-run after the script was shortened) | 0.0995 |
| Narrate 9 scenes × 3 languages | `gemini-3.8-flash-tts`, voice "Charon" | Google Gemini API | 54 (three full rounds, see below) | 0.2341 |
| Listen back and compare with the script | `muse-wonder-1.0` | Meta Model API | 28 + 3 probes | 0.5868 + ≈ 0.03 |
| Slides, panel screenshot, MP4, subtitles | — (headless Edge, ffmpeg) | local | — | — |
| **Total** | | | | **≈ $0.95** (≈ $0.72 Meta budget, $0.23 Google) |

Token counts for every call are in [`video/costs.jsonl`](../video/costs.jsonl); the translation calls are also in the
hash-chained trace `traces/video-20261103.jsonl`. Prices: Muse Spark 1.3 from `issa_as.budget`; Gemini 3.8 Flash TTS
$0.50 / $9.00 per 1M tokens (Google's introductory rate); Muse Wonder $4 / $20 per 1M tokens (OpenRouter's Meta
listing; no separate audio-input price is published, so audio is counted as input tokens). Most of the Muse Wonder
cost is reasoning tokens, even at `reasoning_effort: low`.

## Why the voice is not a Meta model

On 2026-11-03 the hackathon's Meta Model API key listed `muse-wonder-1.0`, `muse-voice-transcribe-1.0`,
`muse-image-1.0`, `sam-3.1` and the Muse Spark models. None of them produces speech:

- `POST /v1/audio/speech` returns HTTP 404.
- Muse Wonder accepts audio input but only returns text.
- `muse-voice-transcribe-1.0` is listed by `/v1/models` but returns 404 both on `/v1/audio/transcriptions` and on
  chat completions ("model_not_found"), so it could not even be used for the listen-back check.

So the narration uses the best-ranked text-to-speech model we hold a key for. The Artificial Analysis speech
leaderboard on 2026-11-03 ranked Gemini 3.8 Flash TTS 5th; the only ones above it were ElevenLabs (no key) and Alibaba
Qwen-Audio TTS. Gemini also gives the same voice in all three languages.

**For the judges and the Meta team:** we would have preferred a Meta voice. A text-to-speech model on the Meta Model
API, multilingual (at least es-419, en-US, pt-BR) and usable in published videos, would let a project like this one keep
its whole pipeline on Meta models: Muse Spark writes and translates, a Meta voice narrates, and Muse Wonder checks the
audio. We would switch to it as soon as it exists.

## What the listen-back check caught

The first narration round sent the style instruction inline ("Read the following as a calm, clear, confident
documentary narrator..."). Muse Wonder's transcript of one clip showed that Gemini **read that instruction aloud** and
dropped the first sentence of the scene. Gemini 3.8 Flash TTS rejects a separate system instruction ("Developer
instruction is not enabled for this model"), so the final round sends the narration text only. In the final check
every clip matches the script word for word; the scores below 100 % come from numbers transcribed as digits ("59"
for "fifty-nine"). One Muse Wonder reply said "no audio received"; the re-check of the same file matched 100 %. Results
are in [`video/check.json`](../video/check.json).

The first full round also ran 3:07 to 3:32, over the 3-minute target; the English script was shortened by about 15 %
and re-translated before the final round.

## Rebuild

Needs ffmpeg and Microsoft Edge on the machine, a Meta Model API key (`MODEL_API_KEY`) and a Gemini key
(`GEMINI_API_KEY`, or the `gemini` label in the ISSASODA key store). Outputs go to `video/out/` (git-ignored).

```bash
uv run python video/make_video.py translate
uv run python video/make_video.py voice
uv run python video/make_video.py check
uv run python video/make_video.py panel     # needs `uv run issa serve --port 8780` running
uv run python video/make_video.py slides
uv run python video/make_video.py build
uv run python video/make_video.py costs
```

## YouTube upload text

Upload `video/out/ISSA-AS-<lang>.mp4` with its `ISSA-AS-<lang>.srt` as the subtitle track.

**en** — *ISSA AS: security fixes you can prove (Meta Global AI Developer Hackathon 2026)*

> ISSA AS scouts public security issues on GitHub, lets Meta's open-weight Muse Spark Open pick what is worth fixing,
> has Muse Code write a failing test and the smallest fix, and verifies it with plain Python, not a model. Two path
> traversal fixes were rebuilt and verified on Linux CI from their proof bundles alone.
> Code and evidence: https://github.com/Ataraxiainc2/ISSAAS · Project page: https://issasodaweb.com/issaas/?lang=en
> Narration: Google Gemini 3.8 Flash TTS (the Meta Model API has no speech model yet). Script translated by Muse Spark
> 1.3; audio checked by Muse Wonder.

**es** — *ISSA AS: correcciones de seguridad que puedes probar (Meta Global AI Developer Hackathon 2026)*

> ISSA AS busca fallas de seguridad públicas en GitHub, deja que Muse Spark Open, el modelo abierto de Meta, elija qué
> vale la pena corregir, hace que Muse Code escriba una prueba que falla y la corrección mínima, y la verifica con
> Python, no con un modelo. Dos correcciones de path traversal se reconstruyeron y verificaron en CI Linux solo desde
> sus paquetes de prueba.
> Código y evidencia: https://github.com/Ataraxiainc2/ISSAAS · Página: https://issasodaweb.com/issaas/?lang=es
> Narración: Google Gemini 3.8 Flash TTS (la API de Meta aún no tiene modelo de voz). Guion traducido por Muse Spark
> 1.3; audio revisado por Muse Wonder.

**pt** — *ISSA AS: correções de segurança que você pode provar (Meta Global AI Developer Hackathon 2026)*

> O ISSA AS busca falhas de segurança públicas no GitHub, deixa o Muse Spark Open, modelo aberto da Meta, escolher o
> que vale corrigir, faz o Muse Code escrever um teste que falha e a menor correção, e a verifica com Python, não com um
> modelo. Duas correções de path traversal foram reconstruídas e verificadas em CI Linux só a partir dos pacotes de prova.
> Código e evidências: https://github.com/Ataraxiainc2/ISSAAS · Página: https://issasodaweb.com/issaas/?lang=pt
> Narração: Google Gemini 3.8 Flash TTS (a API da Meta ainda não tem modelo de voz). Roteiro traduzido pelo Muse Spark
> 1.3; áudio conferido pelo Muse Wonder.
