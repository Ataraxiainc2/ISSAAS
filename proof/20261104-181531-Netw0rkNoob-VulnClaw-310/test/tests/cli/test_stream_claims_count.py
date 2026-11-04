"""Regression test for Netw0rkNoob/VulnClaw#310.

``AgentState.get_summary()`` reports ``verified_claims`` as an int count,
but the ``--stream`` epilogues of ``run`` and ``solve`` sliced it like a
list of claim dicts::

    (agent_state.get("verified_claims") or [])[:50]

Any successful run (>= 1 verified claim) therefore crashed with
``TypeError: 'int' object is not subscriptable`` before the terminal
``complete`` event was emitted.
"""

from __future__ import annotations

from vulnclaw.agent.agent_state import AgentState
from vulnclaw.cli._helpers import build_stream_findings


def _real_summary_with_claims() -> dict:
    """Build a summary exactly as production does (count, not a list)."""
    state = AgentState()
    state.record_verified_claim("SQL injection confirmed", evidence_ids=[])
    summary = state.get_summary()
    assert isinstance(summary["verified_claims"], int)
    assert summary["verified_claims"] == 1
    return dict(summary)


def test_claim_count_degrades_to_empty_findings():
    """Real get_summary() shape (int count > 0) must not raise."""
    findings = build_stream_findings(_real_summary_with_claims(), "example.com")

    assert findings == []


def test_zero_claim_count_degrades_to_empty_findings():
    summary = _real_summary_with_claims() | {"verified_claims": 0}

    assert build_stream_findings(summary, "example.com") == []


def test_claim_list_still_maps_to_findings():
    """List summaries (older shape) keep producing TUI findings."""
    summary = _real_summary_with_claims() | {
        "verified_claims": [
            {"id": "claim-1", "claim": "SQL injection confirmed"},
            "non-dict claims are ignored",
        ]
    }

    assert build_stream_findings(summary, "example.com") == [
        {
            "id": "claim-1",
            "severity": "high",
            "title": "SQL injection confirmed",
            "target": "example.com",
        }
    ]


def test_missing_or_garbage_claims_degrade_to_empty_findings():
    assert build_stream_findings({}, "example.com") == []
    assert (
        build_stream_findings({"verified_claims": "c001"}, "example.com") == []
    )
