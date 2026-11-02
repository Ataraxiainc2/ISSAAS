"""Per-issue budget caps and cost ledger. Plain Python — no LLM calls.

Prices are USD per 1M tokens from ``docs/ARCHITECTURE.md`` section 5.
Cached-input rates are working defaults (no published Meta figure yet);
adjust the table when Meta publishes cached pricing.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

SPARK_MODEL = "muse-spark-1.3"
SPARK_CONTRIBUTOR_MODEL = "muse-spark-1.3-contributor"
SPARK_OPEN_MODEL = "meta-models/muse-spark-open-1026"

# USD per 1M tokens. The hosted Spark Open model is billed provider-side, so
# its trace cost is 0.0 here; step/time caps still apply to those calls.
MODEL_PRICES: dict[str, dict[str, float]] = {
    SPARK_MODEL: {
        "input_per_1m": 1.25,
        "cached_input_per_1m": 0.25,
        "output_per_1m": 4.25,
    },
    SPARK_CONTRIBUTOR_MODEL: {
        "input_per_1m": 0.10,
        "cached_input_per_1m": 0.02,
        "output_per_1m": 0.20,
    },
    SPARK_OPEN_MODEL: {
        "input_per_1m": 0.0,
        "cached_input_per_1m": 0.0,
        "output_per_1m": 0.0,
    },
}


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

    def record(self, usage: TokenUsage, cost_usd: float) -> None:
        self.steps_used += usage.steps
        self.seconds_used += usage.seconds
        self.usd_spent += cost_usd
        self.calls += 1


class CapBlockedError(Exception):
    """Raised when a call would exceed an issue cap. Nothing is charged."""

    def __init__(self, cap: str, spent: float, limit: float) -> None:
        super().__init__(f"cap blocked: {cap} spent={spent} limit={limit}")
        self.cap = cap
        self.spent = spent
        self.limit = limit


def cost_usd(model: str, in_tokens: int, out_tokens: int, cached_tokens: int = 0) -> float:
    """USD cost of one call. Unknown models cost 0.0 (billed elsewhere)."""
    prices = MODEL_PRICES.get(model)
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
) -> float:
    """Gate a model call on the issue caps.

    Returns the USD cost. If the call would exceed a cap, emits a
    ``cap_blocked`` event (when ``run_id`` is given) and raises
    :class:`CapBlockedError` before anything is charged. With
    ``dry_run=True`` the caps are checked without recording any spend, so
    callers can gate on an estimate before the call and charge actuals after.
    """
    from issa_as import traces as trace_log

    cost = cost_usd(usage.model, usage.in_tokens, usage.out_tokens, usage.cached_tokens)
    breach = _breach(ledger, usage, cost, caps)
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
