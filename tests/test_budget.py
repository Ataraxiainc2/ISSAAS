"""Tests for budget.py: price math, caps, and cap_blocked emission."""

import pytest

from issa_as import traces
from issa_as.budget import (
    CapBlockedError,
    IssueCaps,
    Ledger,
    TokenUsage,
    check_and_charge,
    cost_usd,
)


def test_cost_math_spark_13() -> None:
    assert cost_usd("muse-spark-1.3", 1_000_000, 1_000_000) == pytest.approx(5.50)


def test_cost_math_contributor() -> None:
    assert cost_usd("muse-spark-1.3-contributor", 1_000_000, 1_000_000) == pytest.approx(0.30)


def test_cached_input_is_cheaper() -> None:
    full = cost_usd("muse-spark-1.3", 1_000_000, 0)
    cached = cost_usd("muse-spark-1.3", 1_000_000, 0, cached_tokens=1_000_000)
    assert cached < full


def test_unknown_model_costs_zero() -> None:
    assert cost_usd("no-such-model", 10_000, 5_000) == 0.0


def test_charge_within_caps_records_spend() -> None:
    ledger = Ledger(issue_id="i1")
    caps = IssueCaps(max_steps=10, max_seconds=600.0, max_usd=0.60)
    cost = check_and_charge(
        ledger, TokenUsage(model="muse-spark-1.3-contributor", in_tokens=1000, out_tokens=500), caps
    )
    assert cost == pytest.approx((1000 * 0.10 + 500 * 0.20) / 1_000_000)
    assert ledger.calls == 1
    assert ledger.steps_used == 1
    assert ledger.usd_spent == pytest.approx(cost)


def test_over_usd_cap_raises_and_emits(tmp_path) -> None:
    ledger = Ledger(issue_id="i2", usd_spent=0.59)
    caps = IssueCaps(max_steps=10, max_seconds=600.0, max_usd=0.60)
    with pytest.raises(CapBlockedError) as exc_info:
        check_and_charge(
            ledger,
            TokenUsage(model="muse-spark-1.3", in_tokens=100_000, out_tokens=100_000),
            caps,
            run_id="r-cap",
            stage="triage",
            issue_id="i2",
            traces_dir=tmp_path,
        )
    assert exc_info.value.cap == "usd"
    assert ledger.calls == 0  # nothing charged
    (event,) = traces.load("r-cap", tmp_path)
    assert event.type == "cap_blocked"
    assert (event.cap, event.limit) == ("usd", 0.60)


def test_over_steps_cap_raises(tmp_path) -> None:
    ledger = Ledger(issue_id="i3", steps_used=10)
    caps = IssueCaps(max_steps=10, max_seconds=600.0, max_usd=0.60)
    with pytest.raises(CapBlockedError, match="steps"):
        check_and_charge(
            ledger,
            TokenUsage(model="muse-spark-1.3-contributor"),
            caps,
            run_id="r-steps",
            stage="critic",
            traces_dir=tmp_path,
        )
    assert traces.load("r-steps", tmp_path)[0].type == "cap_blocked"


def test_over_time_cap_raises(tmp_path) -> None:
    ledger = Ledger(issue_id="i4", seconds_used=599.0)
    caps = IssueCaps(max_steps=10, max_seconds=600.0, max_usd=0.60)
    with pytest.raises(CapBlockedError, match="time"):
        check_and_charge(
            ledger,
            TokenUsage(model="muse-spark-1.3-contributor", seconds=5.0),
            caps,
            run_id="r-time",
            stage="fixer",
            traces_dir=tmp_path,
        )


def test_dry_run_checks_without_charging() -> None:
    ledger = Ledger(issue_id="i5")
    caps = IssueCaps(max_steps=1, max_seconds=600.0, max_usd=0.60)
    usage = TokenUsage(model="muse-spark-1.3-contributor", steps=5)
    with pytest.raises(CapBlockedError):
        check_and_charge(ledger, usage, caps, dry_run=True)
    assert ledger.calls == 0
    ok_cost = check_and_charge(
        ledger, TokenUsage(model="muse-spark-1.3-contributor", steps=1), caps, dry_run=True
    )
    assert ok_cost >= 0.0
    assert ledger.calls == 0
