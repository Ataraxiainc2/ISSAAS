"""Tests for traces.py: typed events, monotonic seq, UTC ts."""

from datetime import timezone

from issa_as import traces
from issa_as.traces import (
    BaseEvent,
    CapBlocked,
    ModelCall,
    TriageScored,
)


def test_append_and_load_round_trip(tmp_path) -> None:
    traces.append(
        "r1", TriageScored(run_id="r1", solvability=0.8, rationale="clear repro"), tmp_path
    )
    traces.append(
        "r1",
        ModelCall(
            run_id="r1", stage="triage", model="m-x", provider="p",
            in_tokens=10, out_tokens=5, cost_usd=0.1,
        ),
        tmp_path,
    )
    events = traces.load("r1", tmp_path)
    assert [e.seq for e in events] == [1, 2]
    assert isinstance(events[0], TriageScored)
    assert events[0].solvability == 0.8
    assert isinstance(events[1], ModelCall)
    assert events[1].cost_usd == 0.1
    for event in events:
        assert event.ts.tzinfo is not None
        assert event.ts.utcoffset() == timezone.utc.utcoffset(None)


def test_seq_is_monotonic_over_100_appends(tmp_path) -> None:
    for i in range(100):
        traces.append("bulk", TriageScored(run_id="bulk", solvability=i / 100), tmp_path)
    events = traces.load("bulk", tmp_path)
    assert [e.seq for e in events] == list(range(1, 101))


def test_required_common_fields_present(tmp_path) -> None:
    traces.append(
        "r2",
        CapBlocked(run_id="r2", stage="triage", cap="usd", spent=0.5, limit=0.6),
        tmp_path,
    )
    events = traces.load("r2", tmp_path)
    event = events[0]
    assert (event.run_id, event.seq, event.stage, event.type) == ("r2", 1, "triage", "cap_blocked")


def test_dict_events_are_accepted(tmp_path) -> None:
    traces.append(
        "r3",
        {"type": "triage_scored", "stage": "triage", "solvability": 0.3},
        tmp_path,
    )
    (event,) = traces.load("r3", tmp_path)
    assert isinstance(event, TriageScored)
    assert event.solvability == 0.3


def test_unknown_type_loads_leniently(tmp_path) -> None:
    traces.append(
        "r4",
        {"type": "future_event", "stage": "run", "custom": "kept"},
        tmp_path,
    )
    (event,) = traces.load("r4", tmp_path)
    assert isinstance(event, BaseEvent)
    assert event.type == "future_event"
    assert event.model_extra is not None and event.model_extra.get("custom") == "kept"


def test_load_missing_run_returns_empty(tmp_path) -> None:
    assert traces.load("nope", tmp_path) == []
