"""Model triage over scout candidates (B4, B7).

ONE batched model call scores all candidates (cheap). The final score is
a documented weighted blend of the deterministic ``signals`` score and
the model's solvability judgment (B4) — both components are shown, and
the total is labelled an estimate:

    final_score = TRIAGE_SIGNAL_WEIGHT * signals_score
                + TRIAGE_MODEL_WEIGHT  * model_solvability

Provider routing: Spark Open when ``SPARK_OPEN_*`` is set, else
``muse-spark-1.3-contributor`` via the Meta Model API (``MODEL_API_KEY``).
Every call emits a ``model_call`` trace event with tokens, USD and the
provider actually used. Tests use ``FakeProvider`` — no real model calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel

from issa_as import traces as trace_log
from issa_as.budget import SPARK_CONTRIBUTOR_MODEL, cost_usd, price_known
from issa_as.providers import ChatProvider, meta_provider, spark_open_provider

# Hybrid blend weights (B4). Both components are shown on every result;
# the total is an estimate, not a guarantee. Weights sum to 1.0.
TRIAGE_SIGNAL_WEIGHT = 0.5
TRIAGE_MODEL_WEIGHT = 0.5

# Token estimate for the JSON answer per candidate (dry-run + prompts).
EST_OUT_TOKENS_PER_CANDIDATE = 150

Difficulty = Literal["low", "medium", "high"]

TRIAGE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "issue_url",
        "solvability",
        "difficulty",
        "is_real_bug",
        "security_relevant",
        "rationale",
    ],
    "properties": {
        "issue_url": {"type": "string"},
        "solvability": {"type": "number", "minimum": 0, "maximum": 1},
        "difficulty": {"type": "string", "enum": ["low", "medium", "high"]},
        "is_real_bug": {"type": "boolean"},
        "security_relevant": {"type": "boolean"},
        "rationale": {"type": "string"},
        # Optional fields must be nullable (strict-schema rule).
        "flag": {"type": ["string", "null"]},
    },
}

TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["issues"],
    "properties": {
        "issues": {"type": "array", "items": TRIAGE_ITEM_SCHEMA},
    },
}


class TriageResult(BaseModel):
    """Hybrid score for one candidate: both components shown."""

    issue_url: str = ""
    repo: str = ""
    number: int = 0
    title: str = ""
    runner: str = "none"
    signals_score: float = 0.0
    solvability: float = 0.0
    difficulty: Difficulty = "medium"
    is_real_bug: bool = True
    security_relevant: bool = False
    rationale: str = ""
    final_score: float = 0.0
    solvability_v2: float | None = None
    critic_flag: str = ""


def default_provider() -> ChatProvider:
    """Triage route: Spark Open when configured, else contributor via Meta API."""
    open_provider = spark_open_provider()
    if open_provider is not None:
        return open_provider
    return meta_provider(model=SPARK_CONTRIBUTOR_MODEL)


def estimate_tokens(prompt: str, n_candidates: int) -> tuple[int, int]:
    """(est_in_tokens, est_out_tokens) for the batched triage call."""
    return max(len(prompt) // 4, 1), EST_OUT_TOKENS_PER_CANDIDATE * max(n_candidates, 1)


def dry_run_info(
    candidates: list[Mapping[str, Any]], provider: ChatProvider | None = None
) -> dict[str, Any]:
    """Exact-request preview for ``--dry-run``: no network, no provider call."""
    prov = provider if provider is not None else default_provider()
    prompt = build_prompt(candidates)
    est_in, est_out = estimate_tokens(prompt, len(candidates))
    return {
        "model": prov.model,
        "provider": prov.name,
        "n_candidates": len(candidates),
        "est_in_tokens": est_in,
        "est_out_tokens": est_out,
        "est_cost_usd": cost_usd(prov.model, est_in, est_out),
        "price_known": price_known(prov.model),
        "prompt_chars": len(prompt),
    }


def _candidate_dict(candidate: Mapping[str, Any] | Any) -> dict[str, Any]:
    if hasattr(candidate, "model_dump"):
        return dict(candidate.model_dump())
    return dict(candidate)


def build_prompt(candidates: list[Mapping[str, Any] | Any]) -> str:
    """One cheap batched prompt covering every candidate (metadata only)."""
    lines = [
        "You triage GitHub issues for an automated fixing agent.",
        "Score EACH issue below. Reply with JSON only, matching this schema:",
        json.dumps(TRIAGE_SCHEMA, separators=(",", ":")),
        "Rules: solvability is 0..1 (can this agent plausibly fix it: has "
        "repro/snippet, small repo, test suite present); difficulty is "
        "low/medium/high; is_real_bug is false for feature requests, docs "
        "tasks, questions or scanner noise; security_relevant is true only "
        "for real, exploitable security bugs; rationale is at most 2 sentences.",
    ]
    for candidate in candidates:
        info = _candidate_dict(candidate)
        lines.append(
            json.dumps(
                {
                    "issue_url": info.get("issue_url", ""),
                    "title": info.get("title", ""),
                    "labels": info.get("labels", []),
                    "security_class": info.get("security_class"),
                    "runner": info.get("runner", "none"),
                    "stars": info.get("stars", 0),
                    "signals_score": info.get("score", 0.0),
                    "signals": info.get("signals", {}),
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return stripped


def parse_response(
    text: str, candidates: list[Mapping[str, Any] | Any]
) -> list[TriageResult]:
    """Parse and validate the batched triage answer (strict: no silent fixes)."""
    try:
        data = json.loads(_strip_fences(text))
    except ValueError as exc:
        raise ValueError(f"triage answer is not JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ValueError("triage answer must be an object with an 'issues' array")
    by_url = {str(item.get("issue_url", "")): item for item in data["issues"]}
    results: list[TriageResult] = []
    for candidate in candidates:
        info = _candidate_dict(candidate)
        url = str(info.get("issue_url", ""))
        if url not in by_url or not isinstance(by_url[url], dict):
            raise ValueError(f"triage answer misses issue {url!r}")
        item = by_url[url]
        try:
            solvability = float(item["solvability"])
            difficulty = str(item["difficulty"])
            if difficulty not in ("low", "medium", "high"):
                raise ValueError(f"bad difficulty {difficulty!r}")
            result = TriageResult(
                issue_url=url,
                repo=str(info.get("repo", "")),
                number=int(info.get("number", 0) or 0),
                title=str(info.get("title", "")),
                runner=str(info.get("runner", "none")),
                signals_score=float(info.get("score", 0.0) or 0.0),
                solvability=solvability,
                difficulty=difficulty,  # type: ignore[arg-type]
                is_real_bug=bool(item["is_real_bug"]),
                security_relevant=bool(item["security_relevant"]),
                rationale=str(item["rationale"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"triage item for {url!r} is invalid: {exc}") from exc
        if not 0.0 <= result.solvability <= 1.0:
            raise ValueError(f"triage solvability out of range for {url!r}")
        result.final_score = (
            TRIAGE_SIGNAL_WEIGHT * result.signals_score
            + TRIAGE_MODEL_WEIGHT * result.solvability
        )
        results.append(result)
    return results


def score(
    candidates: list[Mapping[str, Any] | Any],
    provider: ChatProvider | None = None,
    *,
    run_id: str | None = None,
    traces_dir: Path | None = None,
) -> list[TriageResult]:
    """Score all candidates with ONE model call; emit ``triage_scored`` events."""
    if not candidates:
        return []
    prov = provider if provider is not None else default_provider()
    prompt = build_prompt(candidates)
    completion = prov.complete(
        stage="triage",
        prompt=prompt,
        effort="low",
        run_id=run_id,
        traces_dir=traces_dir,
    )
    results = parse_response(completion.text, candidates)
    if run_id is not None:
        for result in results:
            trace_log.append(
                run_id,
                trace_log.TriageScored(
                    run_id=run_id,
                    issue_id=result.issue_url,
                    solvability=result.solvability,
                    rationale=result.rationale,
                    difficulty=result.difficulty,
                    provider=completion.provider,
                    model=completion.model,
                ),
                traces_dir,
            )
    return results


__all__ = [
    "Difficulty",
    "TRIAGE_ITEM_SCHEMA",
    "TRIAGE_MODEL_WEIGHT",
    "TRIAGE_SCHEMA",
    "TRIAGE_SIGNAL_WEIGHT",
    "TriageResult",
    "build_prompt",
    "default_provider",
    "dry_run_info",
    "estimate_tokens",
    "parse_response",
    "score",
]
