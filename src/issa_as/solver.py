"""Solver: one ``muse exec`` session that writes a test commit then a fix commit.

The agent runs with the ARCHITECTURE.md runtime flags (``--workspace`` is
the cloned repo under ``workspaces/``) and a JSON output schema. It must
(a) write a failing regression test and commit it, then (b) write the fix
and commit it separately. This module parses the session result, resolves
the two commits, and emits ``fix_attempt`` and ``model_call`` trace events.
Model and effort are parameters. Nothing is ever sent to GitHub.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from issa_as import traces as trace_log
from issa_as.budget import IssueCaps, Ledger, TokenUsage, check_and_charge, cost_usd, price_known
from issa_as.workspace import DEFAULT_WORKSPACE_ROOT, IssueInfo

DEFAULT_MODEL = "muse-spark-1.3-contributor"

# Default step budgets per effort level when --max-steps is not given.
# The keys double as the accepted --reasoning-effort values.
EFFORT_MAX_STEPS = {"minimal": 5, "low": 15, "medium": 30, "high": 60, "xhigh": 120}
REASONING_EFFORTS = tuple(EFFORT_MAX_STEPS)

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "test_file": {"type": "string"},
        "test_command": {"type": "string"},
        "patch_summary": {"type": "string"},
        "root_cause": {"type": "string"},
        "confidence": {"type": "number"},
        "abstain_reason": {"type": "string"},
    },
    "required": ["test_file", "patch_summary"],
}


class SolverError(Exception):
    """The solver session failed (transport, parse, or commit errors)."""


@dataclass
class SolverResult:
    """Outcome of one solver session."""

    run_id: str
    issue_id: str
    abstained: bool = False
    abstain_reason: str = ""
    test_file: str = ""
    test_command: list[str] = field(default_factory=list)
    test_commit: str = ""
    fix_commit: str = ""
    test_parent: str = ""
    patch_text: str = ""
    patch_summary: str = ""
    root_cause: str = ""
    confidence: float = 0.0
    model: str = DEFAULT_MODEL
    provider: str = "muse-exec"
    in_tokens: int = 0
    out_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None
    known_price: bool = True


def build_prompt(issue: IssueInfo, effort: str) -> str:
    """Prompt for the reproduce-then-fix session."""
    body = (issue.body or "")[:4000]
    labels = ", ".join(issue.labels) or "none"
    depth = {
        "low": "Keep the change minimal and move quickly.",
        "high": "Be thorough: explore edge cases before settling on the fix.",
    }.get(effort, "Balance speed and care.")
    return (
        f"You are fixing GitHub issue {issue.repo_full}#{issue.number}: {issue.title}\n"
        f"Labels: {labels}\n\nIssue body:\n{body}\n\n{depth}\n\n"
        "Follow these steps in order:\n"
        "1. Explore the repo and understand the bug.\n"
        "2. Write a minimal failing regression test that reproduces the issue. "
        "Run it and confirm it fails for the right reason. "
        "Then `git add` ONLY the test file and commit it with message "
        "'test: add regression test'. Do not change source code yet.\n"
        "3. Implement the minimal source fix. Run the regression test (it must "
        "pass) and the closely related existing tests. Then `git add` ONLY the "
        "source files and commit with message 'fix: ...'. Keep the two commits "
        "separate.\n"
        "4. If you cannot reproduce the issue or cannot fix it, commit nothing "
        "and explain why.\n\n"
        "Reply with exactly the JSON the output schema requires."
    )


def build_command(
    repo: Path,
    schema_path: Path,
    prompt: str,
    max_steps: int,
    *,
    model: str,
    effort: str,
    muse_bin: str = "muse",
) -> list[str]:
    """The ARCHITECTURE.md fixer invocation.

    Passes ``--model`` and ``--reasoning-effort`` so the requested model and
    effort actually reach Muse, plus ``--approval-mode never`` so headless
    runs never block on an approval prompt.
    Never passes ``--trust-workspace``: a target repo's own agent rules and
    skills must not steer the solver (prompt-injection defense).
    """
    if effort not in REASONING_EFFORTS:
        raise SolverError(f"unknown reasoning effort: {effort!r}")
    return [
        muse_bin,
        "exec",
        "--workspace",
        str(repo),
        "--model",
        model,
        "--reasoning-effort",
        effort,
        "--approval-mode",
        "never",
        "--disable-web-tools",
        "--max-model-steps",
        str(max_steps),
        "--output-schema",
        str(schema_path),
        "--json",
        prompt,
    ]


def _git(
    repo: Path,
    *args: str,
    timeout_s: float = 60.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    run = runner or subprocess.run
    return run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout_s
    )


def _head(repo: Path, runner: Any = None) -> str:
    proc = _git(repo, "rev-parse", "HEAD", runner=runner)
    if proc.returncode != 0:
        raise SolverError(f"cannot read HEAD: {proc.stderr.strip()[-300:]}")
    return proc.stdout.strip()


def _new_commits(
    repo: Path, before: str, runner: Any = None
) -> list[str]:
    proc = _git(repo, "rev-list", f"{before}..HEAD", "--reverse", runner=runner)
    if proc.returncode != 0:
        raise SolverError(f"cannot list new commits: {proc.stderr.strip()[-300:]}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _show_patch(repo: Path, commit: str, runner: Any = None) -> str:
    proc = _git(repo, "diff", f"{commit}^", commit, "--", runner=runner)
    if proc.returncode != 0:
        raise SolverError(f"cannot read fix diff: {proc.stderr.strip()[-300:]}")
    return proc.stdout


def _coerce_payload(data: Any) -> dict[str, Any]:
    """Unwrap one level of `muse exec --json` envelope into the schema dict."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    if not isinstance(data, dict):
        return {}
    for key in ("structured_output", "output", "result", "response"):
        nested = data.get(key)
        if isinstance(nested, dict):
            data = nested
            break
        if isinstance(nested, str):
            try:
                parsed = json.loads(nested)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
    return data if isinstance(data, dict) else {}


def _coerce_usage(data: dict[str, Any]) -> tuple[int, int, int]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        for key in ("structured_output", "output", "result", "response"):
            nested = data.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("usage"), dict):
                usage = nested["usage"]
                break
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    in_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    out_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    cached = usage.get("cached_tokens", details.get("cached_tokens", 0)) or 0
    return int(in_tokens), int(out_tokens), int(cached)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _exec_events(stdout: str) -> list[dict[str, Any]]:
    """Parse ``muse exec --json`` JSONL stdout into event dicts."""
    events: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _terminal_text(events: list[dict[str, Any]]) -> str:
    """Final answer text: terminal event first, output deltas as fallback."""
    for event in events:
        if event.get("payload_type") == "run.terminal.completed":
            payload = event.get("payload")
            if isinstance(payload, dict) and str(payload.get("text", "")).strip():
                return str(payload["text"])
    deltas = [
        str(event.get("payload", {}).get("text", ""))
        for event in events
        if event.get("payload_type") == "run.output.delta"
        and isinstance(event.get("payload"), dict)
    ]
    return "".join(deltas)


def _coerce_run_usage(events: list[dict[str, Any]]) -> tuple[str | None, int, int, int]:
    """(model, in_tokens, out_tokens, cached_tokens) from ``run.usage`` events.

    Token counts are summed over every reported model; the model name is the
    entry with the most tokens. Returns ``(None, 0, 0, 0)`` when no usage was
    reported, so the caller can fall back to the requested model.
    """
    entries: list[dict[str, Any]] = []
    for event in events:
        if event.get("payload_type") != "run.usage":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        models = payload.get("models")
        if isinstance(models, list):
            entries = [m for m in models if isinstance(m, dict)]
    if not entries:
        return None, 0, 0, 0
    in_tokens = sum(_to_int(m.get("input_tokens")) for m in entries)
    out_tokens = sum(_to_int(m.get("output_tokens")) for m in entries)
    cached = sum(_to_int(m.get("cached_tokens")) for m in entries)
    best = max(
        entries, key=lambda m: _to_int(m.get("input_tokens")) + _to_int(m.get("output_tokens"))
    )
    name = best.get("model")
    return (str(name) if name else None), in_tokens, out_tokens, cached


def _coerce_test_command(raw: Any, test_file: str) -> list[str]:
    if isinstance(raw, list) and raw and all(isinstance(p, str) for p in raw):
        return list(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parts = shlex.split(raw.strip(), posix=os.name != "nt")
        except ValueError:
            parts = raw.strip().split()
        if parts:
            return parts
    target = test_file or "tests"
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target]


def _confidence(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def run_solver(
    repo_path: Path | str,
    issue: IssueInfo,
    *,
    run_id: str,
    model: str = DEFAULT_MODEL,
    effort: str = "medium",
    max_steps: int | None = None,
    workspaces_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    traces_dir: Path | None = None,
    ledger: Ledger | None = None,
    caps: IssueCaps | None = None,
    timeout_s: float = 1500.0,
    muse_bin: str = "muse",
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> SolverResult:
    """Run one reproduce-then-fix session and resolve its two commits."""
    if effort not in EFFORT_MAX_STEPS:
        raise SolverError(f"unknown effort: {effort!r}")
    steps = max_steps if max_steps is not None else EFFORT_MAX_STEPS[effort]
    if steps <= 0:
        raise SolverError(f"max_steps must be positive, got {max_steps!r}")
    repo = Path(repo_path).resolve()
    root = Path(workspaces_root).resolve()
    if repo != root and root not in repo.parents:
        raise SolverError(f"repo path escapes workspace root: {repo}")
    if not repo.is_dir():
        raise SolverError(f"repo not found: {repo}")

    issue_id = f"{issue.repo_full}#{issue.number}"
    head_before = _head(repo, runner=runner)

    schema_path = repo.parent / "solver_schema.json"
    if runner is None:
        schema_path.write_text(json.dumps(OUTPUT_SCHEMA, indent=2), encoding="utf-8")
    prompt = build_prompt(issue, effort)
    cmd = build_command(
        repo, schema_path, prompt, steps, model=model, effort=effort, muse_bin=muse_bin
    )

    run = runner or subprocess.run
    try:
        proc = run(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise SolverError(f"muse exec timed out after {timeout_s}s") from exc
    except OSError as exc:
        raise SolverError(f"muse exec failed to start: {exc}") from exc
    if proc.returncode != 0:
        raise SolverError(f"muse exec failed: {(proc.stderr or '').strip()[-500:]}")
    events = _exec_events(proc.stdout or "")
    if len(events) == 1 and "payload_type" not in events[0]:
        # Legacy single-object stub payload (no JSONL envelope).
        payload = _coerce_payload(events[0])
        in_tokens, out_tokens, cached_tokens = _coerce_usage(payload)
        model_used = str(payload.get("model", model) or model)
    else:
        answer = _terminal_text(events)
        if not answer.strip():
            raise SolverError("muse exec returned invalid JSON")
        try:
            payload = _coerce_payload(json.loads(answer))
        except ValueError as exc:
            raise SolverError("muse exec returned invalid JSON") from exc
        usage_model, in_tokens, out_tokens, cached_tokens = _coerce_run_usage(events)
        model_used = usage_model or model
    cost = cost_usd(model_used, in_tokens, out_tokens, cached_tokens)
    known = price_known(model_used)
    if ledger is not None and caps is not None:
        check_and_charge(
            ledger,
            TokenUsage(
                model=model_used,
                in_tokens=in_tokens,
                out_tokens=out_tokens,
                cached_tokens=cached_tokens,
            ),
            caps,
            run_id=run_id,
            stage="fixer",
            issue_id=issue_id,
            traces_dir=traces_dir,
        )

    test_file = str(payload.get("test_file", "") or "").strip()
    test_command = _coerce_test_command(payload.get("test_command"), test_file)
    abstain_reason = str(payload.get("abstain_reason", "") or "").strip()

    result = SolverResult(
        run_id=run_id,
        issue_id=issue_id,
        test_file=test_file,
        test_command=test_command,
        patch_summary=str(payload.get("patch_summary", "") or ""),
        root_cause=str(payload.get("root_cause", "") or ""),
        confidence=_confidence(payload.get("confidence", 0.0)),
        model=model_used,
        in_tokens=in_tokens,
        out_tokens=out_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost,
        known_price=known,
    )

    new_commits = _new_commits(repo, head_before, runner=runner)
    if abstain_reason or len(new_commits) < 2 or not test_file:
        result.abstained = True
        if not abstain_reason:
            if not test_file:
                abstain_reason = "solver returned no test file"
            else:
                abstain_reason = (
                    f"solver produced {len(new_commits)} commit(s), need 2 "
                    "(test commit + fix commit)"
                )
        result.abstain_reason = abstain_reason
        emit_solver_events(run_id, issue_id, result, effort, traces_dir)
        return result

    result.test_commit = new_commits[0]
    result.fix_commit = new_commits[-1]
    parent = _git(repo, "rev-parse", f"{result.test_commit}^", runner=runner)
    if parent.returncode != 0:
        raise SolverError("cannot read parent of the test commit")
    result.test_parent = parent.stdout.strip()
    result.patch_text = _show_patch(repo, result.fix_commit, runner=runner)
    result.abstain_reason = ""

    emit_solver_events(run_id, issue_id, result, effort, traces_dir)
    return result


def emit_solver_events(
    run_id: str,
    issue_id: str,
    result: SolverResult,
    effort: str,
    traces_dir: Path | None,
) -> None:
    """Emit ``fix_attempt`` + ``model_call`` events for a solver result.

    Test stubs that bypass :func:`run_solver` call this so the trace keeps
    the same shape. Abstained results emit only ``model_call`` (no patch).
    """
    _ = effort
    if not result.abstained and result.patch_text:
        trace_log.append(
            run_id,
            trace_log.FixAttempt(
                run_id=run_id,
                stage="fixer",
                issue_id=issue_id,
                effort=effort,
                patch_hash=hashlib.sha256(result.patch_text.encode("utf-8")).hexdigest(),
                explanation=result.patch_summary,
            ),
            traces_dir,
        )
    trace_log.append(
        run_id,
        trace_log.ModelCall(
            run_id=run_id,
            stage="fixer",
            issue_id=issue_id,
            model=result.model,
            provider=result.provider,
            in_tokens=result.in_tokens,
            out_tokens=result.out_tokens,
            cached_tokens=result.cached_tokens,
            cost_usd=result.cost_usd,
            price_known=result.known_price,
        ),
        traces_dir,
    )


__all__ = [
    "DEFAULT_MODEL",
    "EFFORT_MAX_STEPS",
    "OUTPUT_SCHEMA",
    "SolverError",
    "SolverResult",
    "REASONING_EFFORTS",
    "build_command",
    "build_prompt",
    "emit_solver_events",
    "run_solver",
]
