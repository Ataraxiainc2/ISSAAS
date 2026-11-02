"""Deterministic selection of the issue to attempt (Milestone 3b).

Pure Python, no model calls. Picks the highest-scored triaged issue,
records ``selected``/``skipped`` with reasons, and maps difficulty to
solver effort. Never picks ``runner: none`` (no local runner exists) or
an already-solved issue (a ``proof/`` bundle with ``VERIFIED`` for the
same issue URL).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from issa_as import traces as trace_log
from issa_as import triage as triage_mod

# Difficulty reported by triage maps 1:1 onto solver effort levels.
EFFORT_FOR_DIFFICULTY: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
}

DEFAULT_PROOF_DIR = Path("proof")


class PickedIssue(BaseModel):
    """The single issue selected for a solve attempt."""

    issue_url: str = ""
    repo: str = ""
    number: int = 0
    effort: str = "medium"
    reason: str = ""
    final_score: float = 0.0


class SkippedIssue(BaseModel):
    """A candidate that was considered and passed over, with its reason."""

    issue_url: str = ""
    reason: str = ""


class Selection(BaseModel):
    """Outcome of :func:`pick`: one pick (or none) plus every skip reason."""

    picked: PickedIssue | None = None
    skipped: list[SkippedIssue] = []


def solved_issue_urls(proof_dir: str | Path = DEFAULT_PROOF_DIR) -> set[str]:
    """Issue URLs with a ``VERIFIED`` verdict bundle under ``proof_dir``.

    Reads ``proof/*/verdict.json``; corrupt or missing files are ignored
    (never a reason to re-attempt blindly, never a crash).
    """
    solved: set[str] = set()
    root = Path(proof_dir)
    if not root.is_dir():
        return solved
    for verdict_path in sorted(root.glob("*/verdict.json")):
        try:
            doc = json.loads(verdict_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(doc, dict)
            and doc.get("verdict") == "VERIFIED"
            and isinstance(doc.get("issue_url"), str)
        ):
            solved.add(doc["issue_url"])
    return solved


def pick(
    scored: list[triage_mod.TriageResult],
    *,
    proof_dir: str | Path = DEFAULT_PROOF_DIR,
    run_id: str | None = None,
    traces_dir: Path | None = None,
) -> Selection:
    """Pick the top-scored issue that has a runner and is not solved yet."""
    ordered = sorted(scored, key=lambda r: -r.final_score)
    solved = solved_issue_urls(proof_dir)
    selection = Selection()
    for item in ordered:
        if item.runner == "none":
            selection.skipped.append(
                SkippedIssue(
                    issue_url=item.issue_url,
                    reason="no local runner for this ecosystem (runner: none)",
                )
            )
            continue
        if item.issue_url in solved:
            selection.skipped.append(
                SkippedIssue(
                    issue_url=item.issue_url,
                    reason="already solved (VERIFIED proof bundle exists)",
                )
            )
            continue
        if selection.picked is None:
            effort = EFFORT_FOR_DIFFICULTY.get(item.difficulty, "medium")
            selection.picked = PickedIssue(
                issue_url=item.issue_url,
                repo=item.repo,
                number=item.number,
                effort=effort,
                reason=(
                    f"top hybrid score {item.final_score:.3f} "
                    f"(signals {item.signals_score:.3f}, "
                    f"solvability {item.solvability:.2f})"
                ),
                final_score=item.final_score,
            )
        else:
            selection.skipped.append(
                SkippedIssue(
                    issue_url=item.issue_url,
                    reason=f"lower score ({item.final_score:.3f})",
                )
            )
    if run_id is not None:
        if selection.picked is not None:
            trace_log.append(
                run_id,
                trace_log.Selected(
                    run_id=run_id,
                    issue_id=selection.picked.issue_url,
                    reason=selection.picked.reason,
                ),
                traces_dir,
            )
        for skipped in selection.skipped:
            trace_log.append(
                run_id,
                trace_log.Skipped(
                    run_id=run_id,
                    issue_id=skipped.issue_url,
                    reason=skipped.reason,
                ),
                traces_dir,
            )
    return selection


__all__ = [
    "EFFORT_FOR_DIFFICULTY",
    "PickedIssue",
    "Selection",
    "SkippedIssue",
    "pick",
    "solved_issue_urls",
]
