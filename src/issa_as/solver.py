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
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from issa_as import traces as trace_log
from issa_as.budget import IssueCaps, Ledger, TokenUsage, check_and_charge, cost_usd, price_known
from issa_as.workspace import DEFAULT_WORKSPACE_ROOT, IssueInfo

DEFAULT_MODEL = "muse-spark-1.3-contributor"

MUSE_BIN_ENV = "MUSE_BIN"
DEFAULT_MUSE_RUNS_ROOT = Path("muse-runs")
PROMPT_FILENAME = "solver_prompt.md"
SCHEMA_FILENAME = "solver_schema.json"

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


def resolve_muse_bin(explicit: str | None = None) -> str:
    """Resolve the ``muse`` binary: explicit arg, else ``MUSE_BIN``, else PATH.

    A bare ``"muse"`` cannot start the Windows ``muse.cmd`` shim via
    :mod:`subprocess`, so the resolved path (which includes the ``.cmd``
    suffix from ``PATH`` lookup) is used instead. Raises :class:`SolverError`
    when neither source finds anything.
    """
    if explicit:
        return explicit
    env = os.environ.get(MUSE_BIN_ENV, "").strip()
    if env:
        return env
    found = shutil.which("muse")
    if found:
        return found
    raise SolverError(
        f"muse binary not found: set {MUSE_BIN_ENV} to its path "
        "or put muse on PATH (see .env.example)"
    )


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
    prompt_file: Path,
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
    The prompt travels via ``--prompt-file`` (a file under
    ``workspaces/<run_id>/``): long multi-line prompts are fragile as a
    positional argument through the Windows ``muse.cmd`` shim.
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
        "--prompt-file",
        str(prompt_file),
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


def _muse_meta(
    run_id: str,
    prompt_file: Path,
    model: str,
    effort: str,
    steps: int,
    cmd: list[str],
) -> dict[str, Any]:
    """Meta record for ``muse-runs/solver-<run_id>.meta.json``.

    Same shape as ``tools/console/run-muse.ps1`` writes, so the local
    console shows product runs live while they stream.
    """
    try:
        prompt_ref = os.path.relpath(prompt_file, Path.cwd())
    except ValueError:
        prompt_ref = str(prompt_file)
    return {
        "run": f"solver-{run_id}",
        "prompt_file": prompt_ref,
        "model": model,
        "reasoning_effort": effort,
        "max_model_steps": steps,
        "disable_web_tools": True,
        "command": shlex.join(cmd),
        "started": None,
        "finished": None,
        "exit_code": None,
    }


def _write_run_files(
    runs_root: Path,
    run_id: str,
    meta: dict[str, Any],
    stdout_text: str,
) -> None:
    """Persist the streamed JSONL log and its meta record."""
    runs_root.mkdir(parents=True, exist_ok=True)
    stem = f"solver-{run_id}"
    text = stdout_text or ""
    if text and not text.endswith("\n"):
        text += "\n"
    (runs_root / f"{stem}.jsonl").write_text(text, encoding="utf-8")
    (runs_root / f"{stem}.meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def _run_streaming(
    cmd: list[str],
    repo: Path,
    run_id: str,
    prompt_file: Path,
    model: str,
    effort: str,
    steps: int,
    timeout_s: float,
    runs_root: Path,
) -> tuple[str, str]:
    """Run ``muse exec`` for real, streaming stdout to ``muse-runs/`` live.

    A pump thread copies ``muse exec --json`` stdout line by line into
    ``muse-runs/solver-<run_id>.jsonl`` while the main thread waits (with
    the timeout); stderr goes to ``solver-<run_id>.err.txt``. Returns
    ``(stdout, stderr)``; nonzero exit and timeouts raise :class:`SolverError`.
    """
    stem = f"solver-{run_id}"
    runs_root.mkdir(parents=True, exist_ok=True)
    log_path = runs_root / f"{stem}.jsonl"
    err_path = runs_root / f"{stem}.err.txt"
    meta_path = runs_root / f"{stem}.meta.json"
    meta = _muse_meta(run_id, prompt_file, model, effort, steps, cmd)
    meta["started"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    try:
        err_fh = open(err_path, "w", encoding="utf-8")
    except OSError as exc:
        raise SolverError(f"muse exec failed to start: {exc}") from exc
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=err_fh,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        err_fh.close()
        raise SolverError(f"muse exec failed to start: {exc}") from exc
    assert proc.stdout is not None
    chunks: list[str] = []

    def _pump() -> None:
        try:
            with log_path.open("w", encoding="utf-8") as fh:
                for line in proc.stdout:
                    chunks.append(line)
                    fh.write(line if line.endswith("\n") else line + "\n")
        finally:
            try:
                proc.stdout.close()
            except OSError:
                pass

    pump = threading.Thread(target=_pump, name=f"solver-pump-{run_id}", daemon=True)
    pump.start()
    try:
        exit_code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        raise SolverError(f"muse exec timed out after {timeout_s}s") from exc
    pump.join(timeout=30.0)
    try:
        err_fh.close()
    except OSError:
        pass
    meta["finished"] = datetime.now(timezone.utc).isoformat()
    meta["exit_code"] = exit_code
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    stdout_text = "".join(chunks)
    try:
        stderr_text = err_path.read_text(encoding="utf-8")
    except OSError:
        stderr_text = ""
    if exit_code != 0:
        raise SolverError(f"muse exec failed: {stderr_text.strip()[-500:]}")
    return stdout_text, stderr_text


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
    muse_bin: str | None = None,
    muse_runs_root: Path | str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> SolverResult:
    """Run one reproduce-then-fix session and resolve its two commits.

    The prompt is written to ``workspaces/<run_id>/solver_prompt.md`` and
    passed via ``--prompt-file``; ``muse exec --json`` stdout streams line by
    line into ``muse-runs/solver-<run_id>.jsonl`` with a matching
    ``.meta.json`` so the local console shows the run live.
    """
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

    run_dir = repo.parent
    schema_path = run_dir / SCHEMA_FILENAME
    schema_path.write_text(json.dumps(OUTPUT_SCHEMA, indent=2), encoding="utf-8")
    prompt_file = run_dir / PROMPT_FILENAME
    prompt_file.write_text(build_prompt(issue, effort), encoding="utf-8")
    resolved_bin = resolve_muse_bin(muse_bin) if runner is None else (muse_bin or "muse")
    cmd = build_command(
        repo, schema_path, prompt_file, steps,
        model=model, effort=effort, muse_bin=resolved_bin,
    )
    runs_root = Path(muse_runs_root) if muse_runs_root is not None else DEFAULT_MUSE_RUNS_ROOT

    if runner is None:
        stdout_text, stderr_text = _run_streaming(
            cmd, repo, run_id, prompt_file, model, effort, steps, timeout_s, runs_root
        )
    else:
        try:
            proc = runner(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise SolverError(f"muse exec timed out after {timeout_s}s") from exc
        except OSError as exc:
            raise SolverError(f"muse exec failed to start: {exc}") from exc
        if proc.returncode != 0:
            raise SolverError(f"muse exec failed: {(proc.stderr or '').strip()[-500:]}")
        stdout_text = proc.stdout or ""
        meta = _muse_meta(run_id, prompt_file, model, effort, steps, cmd)
        now = datetime.now(timezone.utc).isoformat()
        meta["started"] = now
        meta["finished"] = now
        meta["exit_code"] = proc.returncode
        _write_run_files(runs_root, run_id, meta, stdout_text)
    events = _exec_events(stdout_text)
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
    "DEFAULT_MUSE_RUNS_ROOT",
    "EFFORT_MAX_STEPS",
    "MUSE_BIN_ENV",
    "OUTPUT_SCHEMA",
    "PROMPT_FILENAME",
    "SCHEMA_FILENAME",
    "SolverError",
    "SolverResult",
    "REASONING_EFFORTS",
    "build_command",
    "build_prompt",
    "emit_solver_events",
    "resolve_muse_bin",
    "run_solver",
]
