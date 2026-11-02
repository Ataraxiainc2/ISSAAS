"""End-to-end pipeline for ``issa solve <github_issue_url>`` (Milestone 2).

Workspace (read-only fetch + shallow clone + venv) → solver (one
``muse exec`` session, test commit + fix commit) → ``verifier.verify()``
→ ``proof/<run_id>/`` bundle (verdict, test, patch, logs, cost) or an
abstention note. Every stage appends to the hash-chained trace. Nothing is
ever sent to GitHub.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from issa_as import traces as trace_log
from issa_as import verifier as verify_mod
from issa_as import workspace as ws
from issa_as.budget import IssueCaps, Ledger
from issa_as.solver import (
    DEFAULT_MODEL,
    DEFAULT_MUSE_RUNS_ROOT,
    EFFORT_MAX_STEPS,
    SolverError,
    SolverResult,
    run_solver,
)
from issa_as.verifier import VerifyResult
from issa_as.workspace import IssueInfo, WorkspaceError

DEFAULT_PROOF_ROOT = Path("proof")

_EXIT_CODE_RE = re.compile(r"\(exit (-?\d+)\)")


@dataclass
class SolveOutcome:
    """Outcome of one ``solve`` run."""

    run_id: str
    verdict: str
    bundle_dir: Path
    issue: IssueInfo | None = None
    solver: SolverResult | None = None
    verify: VerifyResult | None = None


def make_run_id(owner: str, repo: str, number: int) -> str:
    """Filesystem-safe run id for one solve attempt."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{owner}-{repo}-{number}"


def _config_hash(model: str, effort: str, max_steps: int | None) -> str:
    return hashlib.sha256(f"{model}|{effort}|{max_steps}".encode()).hexdigest()[:16]


def _git_head(repo: Path) -> str:
    """Best-effort ``git rev-parse HEAD`` ("" when the repo is unreadable)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def solve_issue(
    issue_url: str,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = "medium",
    max_steps: int | None = None,
    run_id: str | None = None,
    workspaces_root: Path = ws.DEFAULT_WORKSPACE_ROOT,
    traces_dir: Path | None = None,
    proof_root: Path = DEFAULT_PROOF_ROOT,
    muse_runs_root: Path = DEFAULT_MUSE_RUNS_ROOT,
    suite_cmd: list[str] | None = None,
    client: Any = None,
    fetch_fn: Callable[[str], IssueInfo] | None = None,
    clone_fn: Callable[[str, Path], Path] | None = None,
    venv_fn: Callable[[Path], Any] | None = None,
    solver_fn: Callable[..., SolverResult] | None = None,
    ledger: Ledger | None = None,
    caps: IssueCaps | None = None,
) -> SolveOutcome:
    """Solve one issue end to end; never touches the network except reads.

    ``fetch_fn``/``clone_fn``/``venv_fn``/``solver_fn`` are injectable seams
    for offline tests. ``venv_fn`` returning None skips venv setup.
    ``ledger``/``caps`` are an optional shared run budget (used by
    ``issa run`` to enforce one USD cap across stages); when omitted a
    fresh per-issue ledger and step-based caps are created.
    Solver infrastructure failures (Muse cannot start, times out, or
    crashes) yield verdict ``ERROR``; ``ABSTAIN`` is reserved for the model
    honestly declining.
    """
    owner, repo_name, number = ws.parse_issue_url(issue_url)
    rid = run_id or make_run_id(owner, repo_name, number)
    ws.validate_run_id(rid)
    issue_id = f"{owner}/{repo_name}#{number}"
    if effort not in EFFORT_MAX_STEPS:
        raise SolverError(f"unknown effort: {effort!r}")
    steps = max_steps if max_steps is not None else EFFORT_MAX_STEPS[effort]
    if steps <= 0:
        raise SolverError(f"max_steps must be positive, got {max_steps!r}")
    if caps is None:
        caps = IssueCaps(max_steps=steps)
    if ledger is None:
        ledger = Ledger(issue_id=issue_id)

    trace_log.append(
        rid,
        trace_log.RunStarted(
            run_id=rid,
            issue_id=issue_id,
            config_hash=_config_hash(model, effort, max_steps),
            model_routes={"fixer": model},
            caps=caps.model_dump(),
        ),
        traces_dir,
    )

    bundle_dir = Path(proof_root) / rid
    bundle_dir.mkdir(parents=True, exist_ok=True)

    try:
        issue = fetch_fn(issue_url) if fetch_fn else ws.fetch_issue(issue_url, client=client)
    except WorkspaceError as exc:
        return _abstain(
            rid, issue_id, None, bundle_dir, traces_dir, f"issue fetch failed: {exc}"
        )
    trace_log.append(
        rid,
        trace_log.IssueDiscovered(
            run_id=rid,
            issue_id=issue_id,
            issue_url=issue_url,
            repo=issue.repo_full,
            labels=list(issue.labels),
            has_snippet="```" in (issue.body or ""),
        ),
        traces_dir,
    )
    (bundle_dir / "issue.json").write_text(
        json.dumps(issue.__dict__, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    try:
        dest = ws.repo_dir(rid, issue.repo, workspaces_root)
        repo_path = clone_fn(issue.clone_url, dest) if clone_fn else ws.clone_repo(
            issue.clone_url, dest, workspaces_root=workspaces_root
        )
        venv_log = ""
        venv_created = False
        if venv_fn is not None:
            venv_info = venv_fn(repo_path)
            if venv_info is not None:
                venv_log = str(getattr(venv_info, "log", ""))
                venv_created = bool(getattr(venv_info, "created", False))
        else:
            try:
                venv_info = ws.setup_venv(repo_path, workspaces_root=workspaces_root)
                venv_log = venv_info.log
                venv_created = venv_info.created
            except WorkspaceError as exc:
                venv_log = f"venv setup failed (continuing): {exc}"
        (bundle_dir / "install.log").write_text(venv_log, encoding="utf-8")
        trace_log.append(
            rid,
            trace_log.WorkspaceReady(
                run_id=rid,
                issue_id=issue_id,
                repo=issue.repo_full,
                repo_sha=_git_head(repo_path),
                venv_created=venv_created,
                install_log_ref=str(bundle_dir / "install.log"),
                install_exit_codes=[int(n) for n in _EXIT_CODE_RE.findall(venv_log)],
            ),
            traces_dir,
        )
    except WorkspaceError as exc:
        return _abstain(
            rid, issue_id, issue, bundle_dir, traces_dir, f"workspace setup failed: {exc}"
        )

    ledger = Ledger(issue_id=issue_id)
    try:
        if solver_fn is not None:
            solver = solver_fn(repo_path, issue, run_id=rid)
        else:
            solver = run_solver(
                repo_path,
                issue,
                run_id=rid,
                model=model,
                effort=effort,
                max_steps=max_steps,
                workspaces_root=workspaces_root,
                traces_dir=traces_dir,
                ledger=ledger,
                caps=caps,
                muse_runs_root=muse_runs_root,
            )
    except Exception as exc:  # infrastructure failure -> ERROR, never ABSTAIN
        return _error(
            rid, issue_id, issue, bundle_dir, traces_dir, f"solver failed: {exc}"
        )
    if solver.abstained:
        return _abstain(
            rid, issue_id, issue, bundle_dir, traces_dir, solver.abstain_reason, solver
        )

    # The verify base is the test commit itself: the regression test is
    # present and failing there, and the fix-commit diff is applied on top.
    # The parent of the test commit is the baseline for the regression
    # check: only tests that passed there and fail after the fix count.
    effective_suite = suite_cmd
    if effective_suite is None:
        effective_suite = _default_suite_cmd(Path(repo_path))
    verify_result = verify_mod.verify(
        repo_path,
        solver.test_commit,
        solver.patch_text,
        solver.test_file,
        solver.test_command,
        suite_cmd=effective_suite,
        baseline_commit=solver.test_parent or None,
        workspace_root=workspaces_root,
        run_id=rid,
        issue_id=issue_id,
        traces_dir=traces_dir,
    )
    trace_log.append(
        rid,
        trace_log.ReproResult(
            run_id=rid,
            issue_id=issue_id,
            test_path=solver.test_file,
            test_commit=solver.test_commit,
            failed_on_base=verify_result.base_returncode not in (None, 0),
            failure_reason=_repro_failure_reason(verify_result),
        ),
        traces_dir,
    )

    test_ref = f"test/{solver.test_file}"
    test_dest = bundle_dir / test_ref
    test_dest.parent.mkdir(parents=True, exist_ok=True)
    test_src = Path(repo_path) / solver.test_file
    test_dest.write_bytes(test_src.read_bytes() if test_src.is_file() else b"")
    patch_ref = "fix.patch"
    (bundle_dir / patch_ref).write_text(solver.patch_text, encoding="utf-8")
    (bundle_dir / "verify.log").write_text(verify_result.log, encoding="utf-8")
    cost = {
        "model": solver.model,
        "provider": solver.provider,
        "in_tokens": solver.in_tokens,
        "out_tokens": solver.out_tokens,
        "cached_tokens": solver.cached_tokens,
        "cost_usd": solver.cost_usd,
        "price_known": solver.known_price,
    }
    (bundle_dir / "cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")
    verdict_doc = {
        "run_id": rid,
        "issue_url": issue_url,
        "issue_id": issue_id,
        "verdict": verify_result.verdict,
        "reason": verify_result.reason,
        "base_commit": solver.test_commit,
        "test_parent": solver.test_parent,
        "test_commit": solver.test_commit,
        "fix_commit": solver.fix_commit,
        "test_file": solver.test_file,
        "test_command": solver.test_command,
        "patch_summary": solver.patch_summary,
        "root_cause": solver.root_cause,
        "confidence": solver.confidence,
        "f2p_pass": verify_result.verdict == "VERIFIED",
        "regressions": verify_result.regressions,
        "baseline_failures": list(verify_result.baseline_failures),
        "model": solver.model,
        "effort": effort,
        "cost_usd": solver.cost_usd,
        "price_known": solver.known_price,
    }
    (bundle_dir / "verdict.json").write_text(
        json.dumps(verdict_doc, indent=2), encoding="utf-8"
    )
    trace_log.append(
        rid,
        trace_log.ProofBundle(
            run_id=rid,
            issue_id=issue_id,
            bundle_path=str(bundle_dir),
            test_ref=test_ref,
            patch_ref=patch_ref,
            total_cost_usd=solver.cost_usd,
        ),
        traces_dir,
    )
    return SolveOutcome(
        run_id=rid,
        verdict=verify_result.verdict,
        bundle_dir=bundle_dir,
        issue=issue,
        solver=solver,
        verify=verify_result,
    )


def _repro_failure_reason(result: VerifyResult) -> str:
    """One-line reason for the ``repro_result`` event from a verify outcome."""
    if result.base_returncode is None:
        return "test not present on base commit (added by the test commit)"
    if result.base_returncode == 0:
        return "test passed on base commit (no failure to reproduce)"
    if result.reason:
        return result.reason[:300]
    tail = (result.log or "").strip().splitlines()
    return tail[-1][:300] if tail else "test failed on base commit"


def _default_suite_cmd(repo: Path) -> list[str]:
    """Default existing-suite command: full pytest collection with the venv interpreter.

    The interpreter is always absolute (the workspace venv), so
    :func:`verifier._resolve_cmd` never anchors it at the repo again.
    """
    repo_abs = Path(repo).resolve()
    if os.name == "nt":
        venv_py = repo_abs / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = repo_abs / ".venv" / "bin" / "python"
    python = str(venv_py) if venv_py.is_file() else sys.executable
    return [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]


def _find_run_repo(run_id: str, workspaces_root: Path) -> Path:
    """Locate the cloned repo under ``workspaces/<run_id>/`` (no Muse involved)."""
    ws.validate_run_id(run_id)
    run_dir = Path(workspaces_root) / run_id
    if not run_dir.is_dir():
        raise WorkspaceError(f"workspace not found for run: {run_id}")
    candidates = sorted(
        p for p in run_dir.iterdir() if p.is_dir() and (p / ".git").is_dir()
    )
    if not candidates:
        raise WorkspaceError(f"no git repo under workspaces/{run_id}")
    if len(candidates) == 1:
        return candidates[0]
    return candidates[0]


def reverify_run(
    run_id: str,
    *,
    workspaces_root: Path | str = ws.DEFAULT_WORKSPACE_ROOT,
    proof_root: Path | str = DEFAULT_PROOF_ROOT,
    traces_dir: Path | None = None,
    suite_cmd: list[str] | None = None,
    timeout_s: float = verify_mod.DEFAULT_TIMEOUT_S,
) -> VerifyResult:
    """Re-run verification for a stored run without calling Muse.

    Reads ``proof/<run_id>/verdict.json`` (commits, test file/command) and
    ``proof/<run_id>/fix.patch``, verifies the existing workspace repo,
    rewrites ``verdict.json``/``verify.log`` and appends a fresh
    ``verify_verdict`` event to the same hash-chained trace.
    """
    ws.validate_run_id(run_id)
    bundle_dir = Path(proof_root) / run_id
    verdict_path = bundle_dir / "verdict.json"
    if not verdict_path.is_file():
        raise WorkspaceError(f"proof bundle not found for run: {run_id}")
    try:
        doc = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceError(f"cannot read verdict for run {run_id}: {exc}") from exc
    if not isinstance(doc, dict):
        raise WorkspaceError(f"cannot read verdict for run {run_id}: not an object")
    repo = _find_run_repo(run_id, Path(workspaces_root))
    patch_file = bundle_dir / "fix.patch"
    try:
        patch_text = patch_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkspaceError(f"cannot read fix.patch for run {run_id}: {exc}") from exc
    test_commit = str(doc.get("test_commit") or doc.get("base_commit") or "")
    test_parent = str(doc.get("test_parent") or "")
    test_file = str(doc.get("test_file") or "")
    test_command = doc.get("test_command") or []
    issue_id = str(doc.get("issue_id") or "")
    if not test_commit or not test_file or not test_command:
        raise WorkspaceError(f"verdict for run {run_id} has no verifiable fix")
    effective_suite = suite_cmd if suite_cmd is not None else _default_suite_cmd(repo)
    result = verify_mod.verify(
        repo,
        test_commit,
        patch_text,
        test_file,
        [str(p) for p in test_command],
        suite_cmd=effective_suite,
        baseline_commit=test_parent or None,
        timeout_s=timeout_s,
        workspace_root=workspaces_root,
        run_id=run_id,
        issue_id=issue_id or None,
        traces_dir=traces_dir,
    )
    (bundle_dir / "verify.log").write_text(result.log, encoding="utf-8")
    doc["verdict"] = result.verdict
    doc["reason"] = result.reason
    doc["f2p_pass"] = result.verdict == "VERIFIED"
    doc["regressions"] = result.regressions
    doc["baseline_failures"] = list(result.baseline_failures)
    (bundle_dir / "verdict.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result


def _error(
    run_id: str,
    issue_id: str,
    issue: IssueInfo | None,
    bundle_dir: Path,
    traces_dir: Path | None,
    reason: str,
    solver: SolverResult | None = None,
) -> SolveOutcome:
    """Infrastructure failure: verdict ``ERROR``, never ``ABSTAIN``.

    ``ABSTAIN`` is reserved for the model honestly declining (or missing
    evidence upstream); a solver that cannot start, times out, or crashes
    must not pollute the abstention-accuracy metric.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    note_path = bundle_dir / "error.md"
    title = issue.title if issue else "(unknown issue)"
    if solver is not None:
        cost = {
            "model": solver.model,
            "provider": solver.provider,
            "in_tokens": solver.in_tokens,
            "out_tokens": solver.out_tokens,
            "cached_tokens": solver.cached_tokens,
            "cost_usd": solver.cost_usd,
            "price_known": solver.known_price,
        }
    else:
        cost = {
            "model": None,
            "provider": None,
            "in_tokens": 0,
            "out_tokens": 0,
            "cached_tokens": 0,
            "cost_usd": None,
            "price_known": False,
        }
    (bundle_dir / "cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")
    note_path.write_text(
        f"# Solver error — {issue_id}\n\n**Issue:** {title}\n\n"
        f"The solver session failed before producing a result.\n\n**Reason:** {reason}\n",
        encoding="utf-8",
    )
    trace_log.append(
        run_id,
        trace_log.RunError(
            run_id=run_id,
            issue_id=issue_id,
            reason=reason,
            note_ref=str(note_path),
        ),
        traces_dir,
    )
    return SolveOutcome(
        run_id=run_id,
        verdict="ERROR",
        bundle_dir=bundle_dir,
        issue=issue,
        solver=solver,
        verify=None,
    )


def _abstain(
    run_id: str,
    issue_id: str,
    issue: IssueInfo | None,
    bundle_dir: Path,
    traces_dir: Path | None,
    reason: str,
    solver: SolverResult | None = None,
) -> SolveOutcome:
    note_path = bundle_dir / "abstention.md"
    title = issue.title if issue else "(unknown issue)"
    cost = (
        {
            "model": solver.model,
            "provider": solver.provider,
            "in_tokens": solver.in_tokens,
            "out_tokens": solver.out_tokens,
            "cached_tokens": solver.cached_tokens,
            "cost_usd": solver.cost_usd,
            "price_known": solver.known_price,
        }
        if solver is not None
        else {
            "model": None,
            "provider": None,
            "in_tokens": 0,
            "out_tokens": 0,
            "cached_tokens": 0,
            "cost_usd": None,
            "price_known": False,
        }
    )
    (bundle_dir / "cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")
    note_path.write_text(
        f"# Abstention — {issue_id}\n\n**Issue:** {title}\n\n"
        f"ISSA AS could not produce a verified fix.\n\n**Reason:** {reason}\n",
        encoding="utf-8",
    )
    trace_log.append(
        run_id,
        trace_log.Abstained(
            run_id=run_id,
            issue_id=issue_id,
            note_ref=str(note_path),
            evidence_refs=[str(bundle_dir / "issue.json")],
        ),
        traces_dir,
    )
    return SolveOutcome(
        run_id=run_id,
        verdict="ABSTAIN",
        bundle_dir=bundle_dir,
        issue=issue,
        solver=solver,
        verify=None,
    )


__all__ = [
    "DEFAULT_PROOF_ROOT",
    "SolveOutcome",
    "make_run_id",
    "reverify_run",
    "solve_issue",
]
