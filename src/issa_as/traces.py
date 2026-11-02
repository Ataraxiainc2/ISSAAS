"""Typed JSONL trace events.

Every pipeline stage appends events to ``traces/<run_id>.jsonl`` (one JSON
object per line). The HTML viewer and Keyless Trace Replay read only these
files, so the schema here is the contract. Field names match
``docs/ARCHITECTURE.md`` section 4.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

GENESIS_PREV_HASH = "0" * 64


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


DEFAULT_TRACES_DIR = Path("traces")


class BaseEvent(BaseModel):
    """Common fields shared by every trace event."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    run_id: str
    seq: int = 0
    ts: datetime = Field(default_factory=utcnow)
    stage: str
    issue_id: str | None = None
    type: str
    prev_hash: str = ""
    hash: str = ""


class RunStarted(BaseEvent):
    type: Literal["run_started"] = "run_started"
    stage: str = "run"
    config_hash: str = ""
    model_routes: dict[str, str] = Field(default_factory=dict)
    caps: dict[str, Any] = Field(default_factory=dict)


class IssueDiscovered(BaseEvent):
    type: Literal["issue_discovered"] = "issue_discovered"
    stage: str = "scout"
    issue_url: str = ""
    repo: str = ""
    labels: list[str] = Field(default_factory=list)
    has_snippet: bool = False


class TriageScored(BaseEvent):
    type: Literal["triage_scored"] = "triage_scored"
    stage: str = "triage"
    solvability: float = 0.0
    rationale: str = ""
    difficulty: str = "medium"
    provider: str = ""
    model: str = ""


class CriticRescored(BaseEvent):
    type: Literal["critic_rescored"] = "critic_rescored"
    stage: str = "critic"
    solvability_v2: float = 0.0
    delta: float = 0.0
    flag: str = ""
    provider: str = ""
    model: str = ""


class Selected(BaseEvent):
    type: Literal["selected"] = "selected"
    stage: str = "select"
    reason: str = ""
    est_cost_usd: float = 0.0


class Skipped(BaseEvent):
    type: Literal["skipped"] = "skipped"
    stage: str = "select"
    reason: str = ""
    est_cost_usd: float = 0.0


class ReproResult(BaseEvent):
    type: Literal["repro_result"] = "repro_result"
    stage: str = "reproduce"
    test_path: str = ""
    failed_on_base: bool = False
    failure_reason: str = ""


class FixAttempt(BaseEvent):
    type: Literal["fix_attempt"] = "fix_attempt"
    stage: str = "fixer"
    effort: str = "medium"
    patch_hash: str = ""
    explanation: str = ""


class VerifyVerdict(BaseEvent):
    type: Literal["verify_verdict"] = "verify_verdict"
    stage: str = "verifier"
    verdict: str = "ERROR"
    reason: str = ""
    f2p_pass: bool = False
    regressions: int = 0
    log_ref: str = ""
    log: str = ""


class Abstained(BaseEvent):
    type: Literal["abstained"] = "abstained"
    stage: str = "attester"
    note_ref: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class ProofBundle(BaseEvent):
    type: Literal["proof_bundle"] = "proof_bundle"
    stage: str = "attester"
    bundle_path: str = ""
    test_ref: str = ""
    patch_ref: str = ""
    total_cost_usd: float | None = None


class ModelCall(BaseEvent):
    type: Literal["model_call"] = "model_call"
    model: str = ""
    provider: str = ""
    in_tokens: int = 0
    out_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None
    price_known: bool = True


class CapBlocked(BaseEvent):
    type: Literal["cap_blocked"] = "cap_blocked"
    cap: str = ""
    spent: float = 0.0
    limit: float = 0.0


class PublishDryrun(BaseEvent):
    type: Literal["publish_dryrun"] = "publish_dryrun"
    stage: str = "publish"
    pr_text_ref: str = ""
    approval_id: str = ""


class Published(BaseEvent):
    type: Literal["published"] = "published"
    stage: str = "publish"
    pr_text_ref: str = ""
    fork_url: str = ""
    approval_id: str = ""


Event = BaseEvent

_BY_TYPE: dict[str, type[BaseEvent]] = {
    "run_started": RunStarted,
    "issue_discovered": IssueDiscovered,
    "triage_scored": TriageScored,
    "critic_rescored": CriticRescored,
    "selected": Selected,
    "skipped": Skipped,
    "repro_result": ReproResult,
    "fix_attempt": FixAttempt,
    "verify_verdict": VerifyVerdict,
    "abstained": Abstained,
    "proof_bundle": ProofBundle,
    "model_call": ModelCall,
    "cap_blocked": CapBlocked,
    "publish_dryrun": PublishDryrun,
    "published": Published,
}


def parse(obj: Mapping[str, Any]) -> BaseEvent:
    """Parse one decoded JSON object into its typed event (lenient on unknown types)."""
    cls = _BY_TYPE.get(str(obj.get("type", "")), BaseEvent)
    return cls.model_validate(obj)


def _trace_path(run_id: str, traces_dir: Path | None) -> Path:
    directory = Path(traces_dir) if traces_dir is not None else DEFAULT_TRACES_DIR
    return directory / f"{run_id}.jsonl"


def canonical_json(data: Mapping[str, Any]) -> str:
    """Canonical JSON for hashing: sorted keys, no whitespace."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(data: Mapping[str, Any]) -> str:
    """SHA-256 hex of the canonical JSON of an event dict (without ``hash``)."""
    payload = {k: v for k, v in data.items() if k != "hash"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def append(
    run_id: str,
    event: BaseEvent | Mapping[str, Any],
    traces_dir: Path | None = None,
) -> BaseEvent:
    """Append one event to ``traces/<run_id>.jsonl``.

    Assigns monotonic ``seq``, UTC ``ts``, and hash-chain fields: ``prev_hash``
    is the previous line's ``hash`` (64 zeros for the first line) and ``hash``
    is SHA-256 over the canonical JSON of the event without ``hash``.
    """
    if isinstance(event, Mapping):
        data = dict(event)
        data["run_id"] = run_id
        typed = parse(data)
    else:
        if event.run_id != run_id:
            raise ValueError(f"event run_id {event.run_id!r} does not match {run_id!r}")
        typed = event

    path = _trace_path(run_id, traces_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    next_seq = 1
    prev_hash = GENESIS_PREV_HASH
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    seq = int(obj.get("seq", 0))
                except (ValueError, AttributeError):
                    continue
                next_seq = max(next_seq, seq + 1)
                if isinstance(obj.get("hash"), str) and obj["hash"]:
                    prev_hash = obj["hash"]
    typed.seq = next_seq
    if typed.ts.tzinfo is None:
        typed.ts = typed.ts.replace(tzinfo=timezone.utc)
    typed.prev_hash = prev_hash
    typed.hash = event_hash(typed.model_dump(mode="json"))

    with path.open("a", encoding="utf-8") as fh:
        fh.write(typed.model_dump_json() + "\n")
    return typed


def load(run_id: str, traces_dir: Path | None = None) -> list[BaseEvent]:
    """Load all events for a run, in file order. Missing run returns an empty list."""
    path = _trace_path(run_id, traces_dir)
    if not path.exists():
        return []
    events: list[BaseEvent] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(parse(json.loads(line)))
    return events


class ChainReport(BaseModel):
    """Result of verifying a run's hash chain."""

    run_id: str
    ok: bool
    bad_line: int | None = None
    reason: str = ""


def verify_chain(run_id: str, traces_dir: Path | None = None) -> ChainReport:
    """Recompute the hash chain of ``traces/<run_id>.jsonl``.

    Returns ``ok=True`` when every line links to its predecessor (genesis
    ``prev_hash`` is 64 zeros) and each stored ``hash`` matches the canonical
    content. Otherwise reports the first bad 1-based line number and why.
    """
    path = _trace_path(run_id, traces_dir)
    if not path.exists():
        return ChainReport(run_id=run_id, ok=False, reason="trace file not found")
    expected_prev = GENESIS_PREV_HASH
    line_no = 0
    checked = 0
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line_no += 1
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                return ChainReport(
                    run_id=run_id, ok=False, bad_line=line_no, reason="line is not valid JSON"
                )
            if not isinstance(obj, dict):
                return ChainReport(
                    run_id=run_id, ok=False, bad_line=line_no, reason="line is not a JSON object"
                )
            stored_hash = obj.get("hash")
            if not isinstance(stored_hash, str) or not stored_hash:
                return ChainReport(
                    run_id=run_id, ok=False, bad_line=line_no, reason="missing hash"
                )
            if obj.get("prev_hash") != expected_prev:
                return ChainReport(
                    run_id=run_id, ok=False, bad_line=line_no, reason="prev_hash mismatch"
                )
            if event_hash(obj) != stored_hash:
                return ChainReport(
                    run_id=run_id, ok=False, bad_line=line_no, reason="hash mismatch"
                )
            expected_prev = stored_hash
            checked += 1
    if checked == 0:
        return ChainReport(run_id=run_id, ok=False, reason="trace file is empty")
    return ChainReport(run_id=run_id, ok=True)
