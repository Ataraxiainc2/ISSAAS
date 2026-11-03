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
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from issa_as import traces as trace_log
from issa_as.verifier import neutral_test_command
from issa_as.budget import IssueCaps, Ledger, TokenUsage, cost_usd, price_known
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
        "test_file": {"type": ["string", "null"]},
        "test_command": {"type": ["string", "null"]},
        "patch_summary": {"type": ["string", "null"]},
        "root_cause": {"type": ["string", "null"]},
        "confidence": {"type": ["number", "null"]},
        "abstain_reason": {"type": ["string", "null"]},
    },
    "required": [
        "test_file",
        "test_command",
        "patch_summary",
        "root_cause",
        "confidence",
        "abstain_reason",
    ],
    "additionalProperties": False,
}

# Conservative per-step USD cost per model, used to derive --max-model-steps
# from the remaining USD budget. Live-verified 2026-11-02: one
# muse-spark-1.3 session at high effort with caching spent ~$1.06 over 59
# model calls, i.e. ~$0.018/step. The contributor model is ~40x cheaper per
# token (see budget.MODEL_PRICES); 0.002 USD/step overestimates a typical
# cached step by several times on purpose. Unknown models fall back to the
# spark-1.3 figure so the cap still bites.
PER_STEP_USD: dict[str, float] = {
    "muse-spark-1.3": 0.018,
    "muse-spark-1.3-contributor": 0.002,
}
DEFAULT_PER_STEP_USD = 0.018


def per_step_cost_usd(model: str, effort: str = "medium") -> float:
    """Conservative USD cost of one model step for ``model``.

    ``effort`` is accepted for future effort scaling; the table is keyed by
    model only so every effort level stays conservative.
    """
    _ = effort
    return PER_STEP_USD.get(model, DEFAULT_PER_STEP_USD)


def derive_max_steps(
    model: str,
    effort: str,
    configured_steps: int,
    remaining_usd: float | None,
) -> int:
    """Derive ``--max-model-steps`` from the remaining USD budget.

    Returns ``min(configured_steps, floor(remaining / per_step_cost))`` with
    a floor of 1, so the cap bites by shrinking the session instead of only
    being observed afterwards. ``None`` remaining means no USD cap applies.
    """
    if configured_steps <= 0:
        raise SolverError(f"max_steps must be positive, got {configured_steps!r}")
    if remaining_usd is None:
        return configured_steps
    per_step = per_step_cost_usd(model, effort)
    if per_step <= 0:
        return configured_steps
    import math

    affordable = math.floor(remaining_usd / per_step)
    return max(1, min(configured_steps, affordable))


class SolverError(Exception):
    """The solver session failed (transport, parse, or commit errors).

    When the failure happens after ``run.usage`` was parsed, the usage is
    attached so the pipeline can still write ``cost.json`` and a
    ``model_call`` event instead of reporting zero tokens.
    """

    def __init__(self, message: str, **usage: Any) -> None:
        super().__init__(message)
        self.model: str | None = usage.get("model")
        self.in_tokens: int = int(usage.get("in_tokens", 0) or 0)
        self.out_tokens: int = int(usage.get("out_tokens", 0) or 0)
        self.cached_tokens: int = int(usage.get("cached_tokens", 0) or 0)
        self.cost_usd: float | None = usage.get("cost_usd")
        self.known_price: bool = bool(usage.get("known_price", True))
        self.answer_objects: int = int(usage.get("answer_objects", 0) or 0)
        # Set once the error-path ``model_call``/``cap_exceeded`` events have
        # been appended, so the pipeline does not emit them a second time.
        self.events_emitted: bool = bool(usage.get("events_emitted", False))


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
    answer_objects: int = 0


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


def _iter_json_objects(text: str) -> list[Any]:
    """Scan ``text`` for every concatenated JSON value (``raw_decode``).

    The solver sometimes emits two answers back to back (e.g. a null draft
    followed by the real answer with no separator), which plain
    :func:`json.loads` rejects. Returns all decoded values in order;
    undecodable stretches are skipped one character at a time.
    """
    decoder = json.JSONDecoder()
    objs: list[Any] = []
    idx = 0
    end = len(text)
    while idx < end:
        while idx < end and text[idx].isspace():
            idx += 1
        if idx >= end:
            break
        try:
            obj, next_idx = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            idx += 1
            continue
        objs.append(obj)
        idx = next_idx if next_idx > idx else idx + 1
    return objs


def _matches_schema(payload: Any) -> bool:
    """Whether a coerced payload looks like a solver answer.

    Lenient on purpose: the object matches when it carries ``test_file``
    and uses only known schema keys. Stub and older answers may omit
    ``abstain_reason``; envelope wrapping is removed by
    :func:`_coerce_payload` first.
    """
    if not isinstance(payload, dict) or "test_file" not in payload:
        return False
    allowed = set(OUTPUT_SCHEMA.get("properties", {})) | set(
        OUTPUT_SCHEMA.get("required", [])
    )
    return set(payload) <= allowed


def _is_all_null(payload: dict[str, Any]) -> bool:
    """Whether every required schema field is null (a non-answer draft)."""
    required = OUTPUT_SCHEMA.get("required", [])
    return bool(required) and all(payload.get(key) is None for key in required)


def parse_answer(text: str) -> tuple[dict[str, Any], int]:
    """Parse the terminal answer text into the schema payload.

    Uses the last decoded object that matches the output schema and is not
    all-null. Returns ``(payload, objects_found)``; raises
    :class:`SolverError` (with ``answer_objects`` attached) when no decoded
    object matches.
    """
    objs = _iter_json_objects(text or "")
    candidates = []
    for obj in objs:
        payload = _coerce_payload(obj)
        if _matches_schema(payload) and not _is_all_null(payload):
            candidates.append(payload)
    if not candidates:
        raise SolverError(
            "muse exec returned invalid JSON "
            f"(found {len(objs)} JSON object(s), none matching the output schema)",
            answer_objects=len(objs),
        )
    return candidates[-1], len(objs)


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
    """Model-provided test command in interpreter-neutral form (Prompt 17c)."""
    if isinstance(raw, list) and raw and all(isinstance(p, str) for p in raw):
        return neutral_test_command(list(raw))
    if isinstance(raw, str) and raw.strip():
        try:
            parts = shlex.split(raw.strip(), posix=os.name != "nt")
        except ValueError:
            parts = raw.strip().split()
        if parts:
            return neutral_test_command(parts)
    target = test_file or "tests"
    return ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", target]


def _opt_str(raw: Any) -> str:
    """Optional schema string: null behaves exactly like a missing key."""
    if raw is None:
        return ""
    return str(raw)


def _confidence(raw: Any) -> float:
    if raw is None:
        return 0.0
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


def _live_usage_totals(lines: list[str]) -> tuple[int, int, int]:
    """Latest cumulative ``run.usage`` totals seen in streamed lines."""
    events = _exec_events("\n".join(lines))
    _, in_tokens, out_tokens, cached_tokens = _coerce_run_usage(events)
    return in_tokens, out_tokens, cached_tokens


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
    caps: IssueCaps | None = None,
    ledger: Ledger | None = None,
    issue_id: str = "",
) -> tuple[str, str]:
    """Run ``muse exec`` for real, streaming stdout to ``muse-runs/`` live.

    A pump thread copies ``muse exec --json`` stdout line by line into
    ``muse-runs/solver-<run_id>.jsonl`` while the main thread waits (with
    the timeout); stderr goes to ``solver-<run_id>.err.txt``. When the
    streamed log exposes ``run.usage`` and the running cost crosses the USD
    cap, the session is terminated early with :class:`SolverError`.
    Returns ``(stdout, stderr)``; nonzero exit and timeouts raise
    :class:`SolverError`.
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
    cap_hit: dict[str, Any] = {"hit": False}

    def _pump() -> None:
        try:
            with log_path.open("w", encoding="utf-8") as fh:
                for line in proc.stdout:
                    chunks.append(line)
                    fh.write(line if line.endswith("\n") else line + "\n")
                    if caps is not None and ledger is not None and not cap_hit["hit"]:
                        try:
                            obj = json.loads(line.strip() or "{}")
                        except ValueError:
                            continue
                        if (
                            isinstance(obj, dict)
                            and obj.get("payload_type") == "run.usage"
                        ):
                            in_t, out_t, cached_t = _live_usage_totals(chunks)
                            spent = cost_usd(model, in_t, out_t, cached_t)
                            spent = spent if spent is not None else 0.0
                            if ledger.usd_spent + spent > caps.max_usd:
                                cap_hit["hit"] = True
                                cap_hit.update(
                                    {
                                        "in_tokens": in_t,
                                        "out_tokens": out_t,
                                        "cached_tokens": cached_t,
                                    }
                                )
                                try:
                                    proc.kill()
                                except OSError:
                                    pass
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
    if cap_hit["hit"]:
        _, in_t, out_t, cached_t = _coerce_run_usage(_exec_events(stdout_text))
        cost = cost_usd(model, in_t, out_t, cached_t)
        raise SolverError(
            f"solver exceeded USD cap {caps.max_usd if caps else '?'} "
            "during the muse exec session",
            model=model,
            in_tokens=in_t,
            out_tokens=out_t,
            cached_tokens=cached_t,
            cost_usd=cost,
            known_price=price_known(model),
            issue_id=issue_id,
        )
    if exit_code != 0:
        raise SolverError(f"muse exec failed: {stderr_text.strip()[-500:]}")
    return stdout_text, stderr_text


def _charge_and_emit(
    run_id: str,
    issue_id: str,
    model_used: str,
    in_tokens: int,
    out_tokens: int,
    cached_tokens: int,
    cost: float | None,
    known: bool,
    ledger: Ledger | None,
    caps: IssueCaps | None,
    traces_dir: Path | None,
) -> None:
    """Record spend and emit ``cap_exceeded`` when the final cost is over cap.

    Unlike :func:`budget.check_and_charge`, an over-cap actual never raises:
    the breach is observed (``cap_exceeded``) and the session result is kept.
    The bit-before part of the cap is :func:`derive_max_steps` plus the
    live streaming kill in :func:`_run_streaming`.
    """
    if ledger is not None:
        ledger.record(
            TokenUsage(
                model=model_used,
                in_tokens=in_tokens,
                out_tokens=out_tokens,
                cached_tokens=cached_tokens,
            ),
            cost,
        )
        spent = ledger.usd_spent
    else:
        spent = cost if cost is not None else 0.0
    if caps is not None and cost is not None and cost > caps.max_usd:
        trace_log.append(
            run_id,
            trace_log.CapExceeded(
                run_id=run_id,
                stage="fixer",
                issue_id=issue_id,
                cap="usd",
                spent=spent,
                limit=caps.max_usd,
                cost_usd=cost,
            ),
            traces_dir,
        )


def _emit_error_model_call(
    run_id: str,
    issue_id: str,
    model_used: str,
    in_tokens: int,
    out_tokens: int,
    cached_tokens: int,
    cost: float | None,
    known: bool,
    traces_dir: Path | None,
) -> None:
    """ModelCall for a session that failed after usage was parsed."""
    trace_log.append(
        run_id,
        trace_log.ModelCall(
            run_id=run_id,
            stage="fixer",
            issue_id=issue_id,
            model=model_used,
            provider="muse-exec",
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost,
            price_known=known,
        ),
        traces_dir,
    )


def _fail_with_usage(
    message: str,
    events: list[dict[str, Any]],
    model: str,
    run_id: str,
    issue_id: str,
    ledger: Ledger | None,
    caps: IssueCaps | None,
    traces_dir: Path | None,
) -> Any:
    """Charge, emit error-path events, and raise :class:`SolverError`."""
    usage_model, in_tokens, out_tokens, cached_tokens = _coerce_run_usage(events)
    model_used = usage_model or model
    cost = cost_usd(model_used, in_tokens, out_tokens, cached_tokens)
    known = price_known(model_used)
    _charge_and_emit(
        run_id, issue_id, model_used, in_tokens, out_tokens, cached_tokens,
        cost, known, ledger, caps, traces_dir,
    )
    _emit_error_model_call(
        run_id, issue_id, model_used, in_tokens, out_tokens, cached_tokens,
        cost, known, traces_dir,
    )
    raise SolverError(
        message,
        model=model_used,
        in_tokens=in_tokens,
        out_tokens=out_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost,
        known_price=known,
        answer_objects=0,
        events_emitted=True,
    )


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
    if ledger is not None and caps is not None:
        remaining = caps.max_usd - ledger.usd_spent
    else:
        remaining = None
    # The USD cap bites before the session: shrink --max-model-steps to what
    # the remaining budget affords, never above the configured step cap.
    steps = derive_max_steps(model, effort, steps, remaining)
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
            cmd, repo, run_id, prompt_file, model, effort, steps, timeout_s, runs_root,
            caps=caps, ledger=ledger, issue_id=issue_id,
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
    usage_model: str | None = None
    answer_objects = 0
    if len(events) == 1 and "payload_type" not in events[0]:
        # Legacy single-object stub payload (no JSONL envelope).
        payload = _coerce_payload(events[0])
        in_tokens, out_tokens, cached_tokens = _coerce_usage(payload)
        model_used = str(payload.get("model", model) or model)
    else:
        answer = _terminal_text(events)
        if not answer.strip():
            _fail_with_usage(
                "muse exec returned invalid JSON (empty terminal text)",
                events, model, run_id, issue_id, ledger, caps, traces_dir,
            )
        try:
            payload, answer_objects = parse_answer(answer)
        except SolverError as exc:
            # parse_answer counted the objects; attach usage before failing.
            usage_model, in_tokens, out_tokens, cached_tokens = _coerce_run_usage(
                events
            )
            model_used = usage_model or model
            cost = cost_usd(model_used, in_tokens, out_tokens, cached_tokens)
            known = price_known(model_used)
            _charge_and_emit(
                run_id, issue_id, model_used, in_tokens, out_tokens, cached_tokens,
                cost, known, ledger, caps, traces_dir,
            )
            _emit_error_model_call(
                run_id, issue_id, model_used, in_tokens, out_tokens, cached_tokens,
                cost, known, traces_dir,
            )
            raise SolverError(
                str(exc),
                model=model_used,
                in_tokens=in_tokens,
                out_tokens=out_tokens,
                cached_tokens=cached_tokens,
                cost_usd=cost,
                known_price=known,
                answer_objects=exc.answer_objects,
                events_emitted=True,
            ) from exc
        usage_model, in_tokens, out_tokens, cached_tokens = _coerce_run_usage(events)
        model_used = usage_model or model
    cost = cost_usd(model_used, in_tokens, out_tokens, cached_tokens)
    known = price_known(model_used)
    _charge_and_emit(
        run_id, issue_id, model_used, in_tokens, out_tokens, cached_tokens,
        cost, known, ledger, caps, traces_dir,
    )

    test_file = _opt_str(payload.get("test_file") or "").strip()
    test_command = _coerce_test_command(payload.get("test_command"), test_file)
    abstain_reason = _opt_str(payload.get("abstain_reason") or "").strip()

    result = SolverResult(
        run_id=run_id,
        issue_id=issue_id,
        test_file=test_file,
        test_command=test_command,
        patch_summary=_opt_str(payload.get("patch_summary") or ""),
        root_cause=_opt_str(payload.get("root_cause") or ""),
        confidence=_confidence(payload.get("confidence", 0.0)),
        model=model_used,
        in_tokens=in_tokens,
        out_tokens=out_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost,
        known_price=known,
        answer_objects=answer_objects,
    )

    def _fail_after_usage(message: str) -> Any:
        _emit_error_model_call(
            run_id, issue_id, model_used, in_tokens, out_tokens, cached_tokens,
            cost, known, traces_dir,
        )
        raise SolverError(
            message,
            model=model_used,
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost,
            known_price=known,
            answer_objects=answer_objects,
            events_emitted=True,
        )

    try:
        new_commits = _new_commits(repo, head_before, runner=runner)
    except SolverError as exc:
        _fail_after_usage(str(exc))
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
        _fail_after_usage("cannot read parent of the test commit")
    result.test_parent = parent.stdout.strip()
    try:
        result.patch_text = _show_patch(repo, result.fix_commit, runner=runner)
    except SolverError as exc:
        _fail_after_usage(str(exc))
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
    "DEFAULT_PER_STEP_USD",
    "EFFORT_MAX_STEPS",
    "MUSE_BIN_ENV",
    "OUTPUT_SCHEMA",
    "PER_STEP_USD",
    "PROMPT_FILENAME",
    "SCHEMA_FILENAME",
    "SolverError",
    "SolverResult",
    "REASONING_EFFORTS",
    "build_command",
    "build_prompt",
    "derive_max_steps",
    "emit_solver_events",
    "parse_answer",
    "per_step_cost_usd",
    "resolve_muse_bin",
    "run_solver",
]
