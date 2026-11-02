"""Deterministic signals for the hybrid solvability score (B4).

Each signal is a named number in ``0..1`` with a fixed weight. The weighted
total is the deterministic part of the hybrid score; the viewer renders every
component so judges can see *why* an issue was chosen. No model calls here.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from pydantic import BaseModel, Field

# Signal name -> weight. Weights sum to 1.0.
SIGNAL_WEIGHTS: dict[str, float] = {
    "has_repro_steps": 0.20,
    "has_stack_trace": 0.10,
    "has_code_snippet": 0.15,
    "test_suite_present": 0.10,
    "ci_present": 0.10,
    "repo_size_ok": 0.05,
    "issue_freshness": 0.05,
    "discussion": 0.05,
    "security_class": 0.20,
}

SIGNAL_DESCRIPTIONS: dict[str, str] = {
    "has_repro_steps": "Issue body lists steps to reproduce the bug.",
    "has_stack_trace": "Issue body contains a stack trace or traceback.",
    "has_code_snippet": "Issue body contains a fenced code block or inline snippet.",
    "test_suite_present": "Repo has a detectable test suite (runner != none).",
    "ci_present": "Repo has a CI workflow (e.g. .github/workflows).",
    "repo_size_ok": "Repo size is within the small-suite budget (<= 50 MB scores 1).",
    "issue_freshness": "Issue is recent (<= 180 days scores 1, decays after).",
    "discussion": "Issue has maintainer/community discussion (comments).",
    "security_class": "Issue matches a known vulnerability class (B7).",
}

_REPRO_RE = re.compile(
    r"steps?\s+to\s+repro|repro(duce|duction)?\s+steps?|how\s+to\s+repro|"
    r"expected\s+behavior|actual\s+behavior|minimal.*example",
    re.IGNORECASE,
)
_TRACE_RE = re.compile(
    r"Traceback \(most recent call|^\s*at\s+\S+ \(|Exception in thread", re.MULTILINE
)
_SNIPPET_RE = re.compile(r"```|(?:^|\n)\s{4}\S")


def _text(body: str | None) -> str:
    return body or ""


def has_repro_steps(body: str | None) -> float:
    """1.0 when the body describes how to reproduce, else 0.0."""
    return 1.0 if _REPRO_RE.search(_text(body)) else 0.0


def has_stack_trace(body: str | None) -> float:
    """1.0 when the body contains a stack trace, else 0.0."""
    return 1.0 if _TRACE_RE.search(_text(body)) else 0.0


def has_code_snippet(body: str | None) -> float:
    """1.0 when the body contains a code block or indented snippet, else 0.0."""
    return 1.0 if _SNIPPET_RE.search(_text(body)) else 0.0


def repo_size_score(size_kb: int | None) -> float:
    """1.0 at 0 KB, linearly down to 0.0 at the 50 MB cap; unknown size scores 0."""
    if size_kb is None or size_kb < 0:
        return 0.0
    cap_kb = 50 * 1024
    return max(0.0, 1.0 - size_kb / cap_kb)


def issue_freshness(created_at: str | datetime | None, now: datetime | None = None) -> float:
    """1.0 for issues <= 180 days old, then linear decay to 0.0 at 720 days."""
    if not created_at:
        return 0.0
    if isinstance(created_at, str):
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    else:
        created = created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    age_days = max(0.0, (ref - created).total_seconds() / 86400.0)
    if age_days <= 180:
        return 1.0
    return max(0.0, 1.0 - (age_days - 180) / 540)


def discussion_score(comment_count: int | None) -> float:
    """Discussion saturates: min(comments, 10) / 10."""
    if comment_count is None or comment_count <= 0:
        return 0.0
    return min(1.0, comment_count / 10.0)


class SignalResult(BaseModel):
    """Deterministic score breakdown for one candidate issue."""

    values: dict[str, float] = Field(default_factory=dict)
    contributions: dict[str, float] = Field(default_factory=dict)
    total: float = 0.0

    def explain(self) -> list[str]:
        """One human-readable line per signal, for the viewer."""
        lines = []
        for name in SIGNAL_WEIGHTS:
            value = self.values.get(name, 0.0)
            weight = SIGNAL_WEIGHTS[name]
            got = self.contributions.get(name, 0.0)
            lines.append(f"{name}={value:.2f} x weight {weight:.2f} = {got:.3f}")
        lines.append(f"total={self.total:.3f} (deterministic estimate)")
        return lines


def compute_signals(
    issue: Mapping[str, Any],
    repo: Mapping[str, Any] | None = None,
    *,
    runner: str = "none",
    has_ci: bool = False,
) -> SignalResult:
    """Score one candidate from issue text, repo metadata and runner info."""
    repo = repo or {}
    body = issue.get("body")
    values = {
        "has_repro_steps": has_repro_steps(body),
        "has_stack_trace": has_stack_trace(body),
        "has_code_snippet": has_code_snippet(body),
        "test_suite_present": 1.0 if runner in ("python", "node") else 0.0,
        "ci_present": 1.0 if has_ci else 0.0,
        "repo_size_ok": repo_size_score(repo.get("size")),
        "issue_freshness": issue_freshness(issue.get("created_at")),
        "discussion": discussion_score(issue.get("comments")),
        "security_class": 1.0 if issue.get("security_class") else 0.0,
    }
    contributions = {n: values[n] * SIGNAL_WEIGHTS[n] for n in SIGNAL_WEIGHTS}
    total = sum(contributions.values())
    return SignalResult(values=values, contributions=contributions, total=total)
