"""Deterministic selection of the issue to attempt (Milestone 3b).

Pure Python, no model calls. Picks the highest-scored triaged issue,
records ``selected``/``skipped`` with reasons, and maps difficulty to
solver effort. Skips model-judged non-bugs (``is_real_bug == False``),
skips every runner except ``python`` (no Node runner exists yet),
never re-attempts an already-solved issue (a ``proof/`` bundle with ``VERIFIED``
for the same issue URL, locally or via Linux CI), and skips already-attempted
(but unsolved) issues unless ``retry_attempted`` is set.

Solver routing rule table (deterministic, see :func:`route_solver`):

- default: ``muse-spark-1.3-contributor`` at difficulty-mapped
  effort, ``recommended_max_usd`` 0.60;
- difficulty high or sensitive security class: ``muse-spark-1.3`` at
  high effort, ``recommended_max_usd`` 2.00.

A security class is sensitive when it names injection (``command-injection``,
``sql-injection``), path traversal (``path-traversal``), deserialization
(``unsafe-deserialization``) or auth bypass (``auth-bypass``): those need the
strong model even at low/medium difficulty.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from issa_as import traces as trace_log
from issa_as import triage as triage_mod
from issa_as.budget import SPARK_CONTRIBUTOR_MODEL, SPARK_MODEL

# Difficulty reported by triage maps 1:1 onto solver effort levels.
EFFORT_FOR_DIFFICULTY: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
}

# Solver routing (deterministic). Default is the cheap contributor model;
# high difficulty or a sensitive security class escalates to the strong model.
#
# Per-issue USD cap rule: the ansible-runner#1548 fix took 59 steps on
# muse-spark-1.3 and cost $1.23 with reasoning tokens billed as output (the
# $1.06 first logged left them out; see AUDIT_LOG entry 20), ~$0.021/step.
# The $0.60 default cap allows only ~28 strong steps, so escalated
# recommendations (strong model + high effort) carry ``recommended_max_usd``
# 2.00 (~95 steps, above the 60-step high-effort limit); everything else
# keeps the 0.60 default.
SOLVER_MODEL_DEFAULT = SPARK_CONTRIBUTOR_MODEL
SOLVER_MODEL_STRONG = SPARK_MODEL
DEFAULT_MAX_USD = 0.60
ESCALATED_MAX_USD = 2.00
ESCALATE_SECURITY_CLASSES = frozenset(
    {
        "command-injection",
        "sql-injection",
        "path-traversal",
        "unsafe-deserialization",
        "auth-bypass",
    }
)

DEFAULT_PROOF_DIR = Path("proof")


def route_solver(difficulty: str, security_class: str | None) -> tuple[str, str, str]:
    """Route an issue to a solver model. Returns ``(model, effort, rationale)``.

    Escalates to :data:`SOLVER_MODEL_STRONG` with ``high`` effort when the
    difficulty is ``high`` or the security class is sensitive (injection,
    path traversal, deserialization or auth bypass); otherwise the cheap
    default model runs at the difficulty-mapped effort.
    """
    triggers: list[str] = []
    if difficulty == "high":
        triggers.append("high difficulty")
    if (security_class or "") in ESCALATE_SECURITY_CLASSES:
        triggers.append(f"{security_class} security class")
    if triggers:
        return (
            SOLVER_MODEL_STRONG,
            "high",
            f"escalated to {SOLVER_MODEL_STRONG}/high: {' + '.join(triggers)}",
        )
    effort = EFFORT_FOR_DIFFICULTY.get(difficulty, "medium")
    return (
        SOLVER_MODEL_DEFAULT,
        effort,
        f"default route: {SOLVER_MODEL_DEFAULT} fits {difficulty} difficulty",
    )


def recommended_max_usd(model: str, effort: str) -> float:
    """Per-issue USD cap for a solver recommendation.

    Escalated recommendations (strong model with ``high`` effort) get
    :data:`ESCALATED_MAX_USD` (2.00); every other routing keeps
    :data:`DEFAULT_MAX_USD` (0.60). Pure function of the routed model and
    effort, so ``issa run``, ``issa solve`` and the control panel agree.
    """
    if model == SOLVER_MODEL_STRONG and effort == "high":
        return ESCALATED_MAX_USD
    return DEFAULT_MAX_USD


class PickedIssue(BaseModel):
    """The single issue selected for a solve attempt."""

    issue_url: str = ""
    repo: str = ""
    number: int = 0
    effort: str = "medium"
    reason: str = ""
    final_score: float = 0.0
    recommended_solver_model: str = SOLVER_MODEL_DEFAULT
    recommended_effort: str = "medium"
    model_rationale: str = ""
    recommended_max_usd: float = DEFAULT_MAX_USD
    # Carried SECURITY.md policy (Prompt 18); publish reads it to choose
    # a private disclosure draft over a public PR draft.
    security_policy: dict | None = None


class SkippedIssue(BaseModel):
    """A candidate that was considered and passed over, with its reason."""

    issue_url: str = ""
    reason: str = ""


class Selection(BaseModel):
    """Outcome of :func:`pick`: one pick (or none) plus every skip reason."""

    picked: PickedIssue | None = None
    skipped: list[SkippedIssue] = []
    skipped_not_bug: int = 0


def _read_bundle_doc(bundle: Path, name: str) -> dict | None:
    """Read one JSON object from a proof bundle, or None when missing/corrupt."""
    try:
        doc = json.loads((bundle / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _bundle_issue_url(bundle: Path) -> str | None:
    """Issue URL for a proof bundle: ``verdict.json``, else ``issue.json``."""
    for name in ("verdict.json", "issue.json"):
        doc = _read_bundle_doc(bundle, name)
        if doc is not None and isinstance(doc.get("issue_url"), str) and doc["issue_url"]:
            return doc["issue_url"]
    return None


def _bundle_linux_solved(bundle: Path) -> bool:
    """Whether the bundle's Linux CI re-verification passed with no regressions."""
    doc = _read_bundle_doc(bundle, "linux-verify.json")
    return (
        doc is not None
        and doc.get("f2p_pass") is True
        and doc.get("regressions") == 0
    )


def solved_issue_details(
    proof_dir: str | Path = DEFAULT_PROOF_DIR,
) -> dict[str, tuple[str, str]]:
    """Solved issues mapped to ``(run_id, evidence)``.

    A bundle counts as solved when its ``verdict.json`` says ``VERIFIED``
    (local evidence) or when its ``linux-verify.json`` reports
    ``f2p_pass: true`` with ``regressions: 0`` (Linux CI evidence, e.g. a
    Windows ``ERROR`` bundle whose fix passed on Linux). The issue URL comes
    from ``verdict.json`` (or ``issue.json`` when missing there). Corrupt or
    missing files are ignored, as today. ``evidence`` is ``"local"`` or
    ``"Linux CI"``.
    """
    solved: dict[str, tuple[str, str]] = {}
    root = Path(proof_dir)
    if not root.is_dir():
        return solved
    for bundle in sorted(p for p in root.iterdir() if p.is_dir()):
        url = _bundle_issue_url(bundle)
        if not url or url in solved:
            continue
        verdict_doc = _read_bundle_doc(bundle, "verdict.json")
        done_run = bundle.name
        if isinstance(verdict_doc, dict) and isinstance(verdict_doc.get("run_id"), str):
            done_run = verdict_doc["run_id"] or done_run
        if isinstance(verdict_doc, dict) and verdict_doc.get("verdict") == "VERIFIED":
            solved[url] = (done_run, "local")
        elif _bundle_linux_solved(bundle):
            solved[url] = (done_run, "Linux CI")
    return solved


def solved_issue_urls(proof_dir: str | Path = DEFAULT_PROOF_DIR) -> set[str]:
    """Issue URLs with a solved proof bundle under ``proof_dir``.

    Solved means a ``VERIFIED`` local verdict, or a ``linux-verify.json``
    with ``f2p_pass: true`` and ``regressions: 0`` (the URL then comes from
    ``verdict.json`` or ``issue.json``). Corrupt or missing files are
    ignored (never a reason to re-attempt blindly, never a crash).
    """
    return set(solved_issue_details(proof_dir))


def attempted_issues(
    proof_dir: str | Path = DEFAULT_PROOF_DIR,
) -> dict[str, tuple[str, str]]:
    """Every attempted issue mapped to ``(verdict, run_id)``.

    Any proof bundle with a readable ``verdict.json`` counts as an attempt,
    whatever its verdict (``VERIFIED`` bundles are reported here too, so
    callers must check :func:`solved_issue_details` first). Corrupt or
    missing files are ignored.
    """
    attempted: dict[str, tuple[str, str]] = {}
    root = Path(proof_dir)
    if not root.is_dir():
        return attempted
    for verdict_path in sorted(root.glob("*/verdict.json")):
        bundle = verdict_path.parent
        doc = _read_bundle_doc(bundle, "verdict.json")
        if doc is None:
            continue
        url = _bundle_issue_url(bundle)
        if not url or url in attempted:
            continue
        verdict = doc.get("verdict")
        run_id = doc.get("run_id")
        attempted[url] = (
            verdict if isinstance(verdict, str) and verdict else "UNKNOWN",
            run_id if isinstance(run_id, str) and run_id else bundle.name,
        )
    return attempted


def pick(
    scored: list[triage_mod.TriageResult],
    *,
    proof_dir: str | Path = DEFAULT_PROOF_DIR,
    run_id: str | None = None,
    traces_dir: Path | None = None,
    retry_attempted: bool = False,
) -> Selection:
    """Pick the top-scored issue that is a real bug, runnable and not solved yet.

    Skips model-judged non-bugs (counted in ``skipped_not_bug``), every
    runner except ``python`` (no Node runner exists yet), already-solved
    issues (local ``VERIFIED`` or Linux CI evidence), and already-attempted
    (but unsolved) issues unless ``retry_attempted`` is set. Solved issues
    are never re-attempted. The pick carries the deterministic solver routing
    (:func:`route_solver`).
    """
    ordered = sorted(scored, key=lambda r: -r.final_score)
    solved = solved_issue_details(proof_dir)
    attempted = attempted_issues(proof_dir)
    selection = Selection()
    for item in ordered:
        if not item.is_real_bug:
            selection.skipped.append(
                SkippedIssue(
                    issue_url=item.issue_url,
                    reason=(
                        "not a real bug (is_real_bug=false: feature request, "
                        "docs, question or scanner noise)"
                    ),
                )
            )
            selection.skipped_not_bug += 1
            continue
        if item.runner != "python":
            selection.skipped.append(
                SkippedIssue(
                    issue_url=item.issue_url,
                    reason=f"runner not implemented yet ({item.runner})",
                )
            )
            continue
        if item.issue_url in solved:
            done_run, evidence = solved[item.issue_url]
            if evidence == "local":
                already_reason = (
                    "already solved (VERIFIED proof bundle exists, "
                    f"run {done_run}, local evidence)"
                )
            else:
                already_reason = (
                    "already solved (VERIFIED on Linux CI, "
                    f"run {done_run}, Linux CI evidence)"
                )
            selection.skipped.append(
                SkippedIssue(issue_url=item.issue_url, reason=already_reason)
            )
            continue
        if item.issue_url in attempted and not retry_attempted:
            verdict, done_run = attempted[item.issue_url]
            selection.skipped.append(
                SkippedIssue(
                    issue_url=item.issue_url,
                    reason=f"already attempted ({verdict}, run {done_run})",
                )
            )
            continue
        if selection.picked is None:
            effort = EFFORT_FOR_DIFFICULTY.get(item.difficulty, "medium")
            model, recommended_effort, rationale = route_solver(
                item.difficulty, item.security_class
            )
            reason = (
                f"top hybrid score {item.final_score:.3f} "
                f"(signals {item.signals_score:.3f}, "
                f"solvability {item.solvability:.2f})"
            )
            # The issue is still fixed locally when it is already public;
            # only disclosure changes, so a private policy never skips it.
            policy = item.security_policy or {}
            if isinstance(policy, dict) and policy.get("private_reporting"):
                contact = policy.get("contact") or "see SECURITY.md"
                reason += f"; private disclosure required (report privately to {contact})"
            selection.picked = PickedIssue(
                issue_url=item.issue_url,
                repo=item.repo,
                number=item.number,
                effort=effort,
                reason=reason,
                final_score=item.final_score,
                recommended_solver_model=model,
                recommended_effort=recommended_effort,
                model_rationale=rationale,
                recommended_max_usd=recommended_max_usd(model, recommended_effort),
                security_policy=(
                    dict(policy) if isinstance(policy, dict) and policy else None
                ),
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
                    reason=(
                        f"{selection.picked.reason} "
                        f"Model: {selection.picked.model_rationale}"
                    ),
                    model=selection.picked.recommended_solver_model,
                    effort=selection.picked.recommended_effort,
                    model_rationale=selection.picked.model_rationale,
                    final_score=selection.picked.final_score,
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
    "DEFAULT_MAX_USD",
    "EFFORT_FOR_DIFFICULTY",
    "ESCALATED_MAX_USD",
    "ESCALATE_SECURITY_CLASSES",
    "SOLVER_MODEL_DEFAULT",
    "SOLVER_MODEL_STRONG",
    "PickedIssue",
    "Selection",
    "SkippedIssue",
    "attempted_issues",
    "pick",
    "recommended_max_usd",
    "route_solver",
    "solved_issue_details",
    "solved_issue_urls",
]
