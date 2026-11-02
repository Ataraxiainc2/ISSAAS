"""Per-issue budget caps and cost ledger. Plain Python — no LLM calls.

Prices are USD per 1M tokens. Meta API prices are fixed below; hosted
Spark Open prices are read from the environment at call time (see
``.env.example``). When the Spark Open price env vars are unset, the cost
is unknown: :func:`cost_usd` returns ``None`` (never ``0.0``) and
:func:`price_known` returns ``False``.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

SPARK_MODEL = "muse-spark-1.3"
SPARK_CONTRIBUTOR_MODEL = "muse-spark-1.3-contributor"
# Live-verified (2026-11-02, Baseten OpenAI-compatible endpoint): the working
# model id is ``meta-models/muse-spark-open``. The hackathon guide's id
# ``meta-models/muse-spark-open-1026`` returns HTTP 404 on this account.
SPARK_OPEN_MODEL_DEFAULT = "meta-models/muse-spark-open"
SPARK_OPEN_MODEL_LEGACY = "meta-models/muse-spark-open-1026"
SPARK_OPEN_MODEL_ENV = "SPARK_OPEN_MODEL"
# Backwards-compatible alias: the configured default (not the env override).
SPARK_OPEN_MODEL = SPARK_OPEN_MODEL_DEFAULT

SPARK_OPEN_PRICE_IN_ENV = "SPARK_OPEN_PRICE_IN_PER_1M"
SPARK_OPEN_PRICE_OUT_ENV = "SPARK_OPEN_PRICE_OUT_PER_1M"


def spark_open_model() -> str:
    """Configured Spark Open model id (env ``SPARK_OPEN_MODEL`` or default)."""
    return os.environ.get(SPARK_OPEN_MODEL_ENV, "").strip() or SPARK_OPEN_MODEL_DEFAULT


def _is_spark_open_model(model: str) -> bool:
    """Whether ``model`` belongs to the hosted Spark Open family.

    Covers the default id, the legacy ``-1026`` id, the current env override
    and any future ``meta-models/muse-spark-open*`` id so pricing stays
    env-driven for the whole family.
    """
    if model in (SPARK_OPEN_MODEL_DEFAULT, SPARK_OPEN_MODEL_LEGACY, spark_open_model()):
        return True
    return model.startswith("meta-models/muse-spark-open")

# USD per 1M tokens. Cached-input rates are the published Meta figures.
MODEL_PRICES: dict[str, dict[str, float]] = {
    SPARK_MODEL: {
        "input_per_1m": 1.25,
        "cached_input_per_1m": 0.15,
        "output_per_1m": 4.25,
    },
    SPARK_CONTRIBUTOR_MODEL: {
        "input_per_1m": 0.10,
        "cached_input_per_1m": 0.002,
        "output_per_1m": 0.20,
    },
}


def _env_float_opt(name: str) -> float | None:
    """Read a price env var; unset/empty/unparsable means unknown (None)."""
    raw = os.environ.get(name, "")
    if not str(raw).strip():
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _prices_for(model: str) -> dict[str, float] | None:
    """USD-per-1M price table for a model, or None when unknown/billed elsewhere."""
    if _is_spark_open_model(model):
        price_in = _env_float_opt(SPARK_OPEN_PRICE_IN_ENV)
        price_out = _env_float_opt(SPARK_OPEN_PRICE_OUT_ENV)
        if price_in is None or price_out is None:
            return None
        return {
            "input_per_1m": price_in,
            "cached_input_per_1m": price_in,
            "output_per_1m": price_out,
        }
    return MODEL_PRICES.get(model)


def price_known(model: str) -> bool:
    """Whether a USD cost can be computed for ``model`` right now.

    Spark Open prices come from the environment; when the env vars are
    unset the price is unknown (``False``). Unknown third-party models are
    billed elsewhere and treated as known-zero by :func:`cost_usd`.
    """
    if _is_spark_open_model(model):
        return _prices_for(model) is not None
    return True


class TokenUsage(BaseModel):
    """Token usage (actual or estimated) for one model call."""

    model: str
    in_tokens: int = 0
    out_tokens: int = 0
    cached_tokens: int = 0
    seconds: float = 0.0
    steps: int = 1


class IssueCaps(BaseModel):
    """Hard per-issue limits. Any breach blocks the call before it is made."""

    max_steps: int = 10
    max_seconds: float = 600.0
    max_usd: float = 0.60


class Ledger(BaseModel):
    """Running spend for one issue."""

    issue_id: str
    steps_used: int = 0
    seconds_used: float = 0.0
    usd_spent: float = 0.0
    calls: int = 0

    def record(self, usage: TokenUsage, cost_usd: float | None) -> None:
        self.steps_used += usage.steps
        self.seconds_used += usage.seconds
        self.usd_spent += cost_usd if cost_usd is not None else 0.0
        self.calls += 1


class CapBlockedError(Exception):
    """Raised when a call would exceed an issue cap. Nothing is charged."""

    def __init__(self, cap: str, spent: float, limit: float) -> None:
        super().__init__(f"cap blocked: {cap} spent={spent} limit={limit}")
        self.cap = cap
        self.spent = spent
        self.limit = limit


def cost_usd(
    model: str, in_tokens: int, out_tokens: int, cached_tokens: int = 0
) -> float | None:
    """USD cost of one call, or None when the price is unknown.

    Unknown Spark Open prices (env vars unset) return None, never 0.0, so
    the benchmark never shows a free call. Unknown third-party models cost
    0.0 (billed elsewhere).
    """
    if _is_spark_open_model(model) and not price_known(model):
        return None
    prices = _prices_for(model)
    if prices is None:
        return 0.0
    fresh_in = max(in_tokens - cached_tokens, 0)
    return (
        fresh_in * prices["input_per_1m"]
        + cached_tokens * prices["cached_input_per_1m"]
        + out_tokens * prices["output_per_1m"]
    ) / 1_000_000


def _breach(
    ledger: Ledger, usage: TokenUsage, cost: float, caps: IssueCaps
) -> tuple[str, float, float] | None:
    if ledger.steps_used + usage.steps > caps.max_steps:
        return ("steps", float(ledger.steps_used), float(caps.max_steps))
    if ledger.seconds_used + usage.seconds > caps.max_seconds:
        return ("seconds", ledger.seconds_used, caps.max_seconds)
    if ledger.usd_spent + cost > caps.max_usd:
        return ("usd", ledger.usd_spent, caps.max_usd)
    return None


def check_and_charge(
    ledger: Ledger,
    usage: TokenUsage,
    caps: IssueCaps,
    *,
    run_id: str | None = None,
    stage: str = "unknown",
    issue_id: str | None = None,
    traces_dir: Path | None = None,
    dry_run: bool = False,
) -> float | None:
    """Gate a model call on the issue caps.

    Returns the USD cost, or None when the price is unknown (unknown Spark
    Open prices are never reported as 0.0). Unknown cost counts as 0.0 for
    cap purposes so an unpriced call is never cap-blocked on USD. If the
    call would exceed a cap, emits a ``cap_blocked`` event (when ``run_id``
    is given) and raises :class:`CapBlockedError` before anything is
    charged. With ``dry_run=True`` the caps are checked without recording
    any spend, so callers can gate on an estimate before the call and charge
    actuals after.
    """
    from issa_as import traces as trace_log

    cost = cost_usd(usage.model, usage.in_tokens, usage.out_tokens, usage.cached_tokens)
    breach = _breach(ledger, usage, cost if cost is not None else 0.0, caps)
    if breach is not None:
        cap, spent, limit = breach
        if run_id is not None:
            trace_log.append(
                run_id,
                trace_log.CapBlocked(
                    run_id=run_id,
                    stage=stage,
                    issue_id=issue_id,
                    cap="time" if cap == "seconds" else cap,
                    spent=spent,
                    limit=limit,
                ),
                traces_dir,
            )
        raise CapBlockedError("time" if cap == "seconds" else cap, spent, limit)
    if not dry_run:
        ledger.record(usage, cost)
    return cost
