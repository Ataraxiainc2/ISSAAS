"""Local web control panel server: ``issa serve [--port 8780]``.

Serves the static operator panel in ``web/`` (no build step, no framework,
no CDN) plus JSON APIs over the local ``scout/``, ``triage/``, ``traces/``
and ``proof/`` directories. Stdlib only (``http.server`` + ``threading``).

Security model (see ``docs/ARCHITECTURE.md``):

- Binds ``127.0.0.1`` only; never ``0.0.0.0``.
- Rejects requests whose ``Host`` is not localhost (DNS-rebinding guard)
  and requests carrying a non-localhost ``Origin``/``Referer`` (CSRF guard).
- Actions are POST only; reads are GET only.
- Run ids are validated with :func:`workspace.validate_run_id`.
- Untrusted issue/model text is served as JSON and inserted with
  ``textContent`` only (see ``web/app.js``); never executed.
- Never serves ``.env`` values or any secret: responses are built only
  from trace/scout/triage/proof files, and error paths never echo the
  environment.
- Paid actions (triage, solve, full loop) require a confirm token from
  their estimate request; the token binds the exact parameters, is
  single-use, expires after :data:`TOKEN_TTL_S`, and is checked here on
  the server, not only in the page.
- One background worker runs actions one at a time; a busy server
  answers 409. Progress streams via job-log polling
  (``GET /api/jobs/<job_id>``).
"""

from __future__ import annotations

import contextlib
import io
import json
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8780
BUDGET_TOTAL_USD = 150.0
TOKEN_TTL_S = 600.0
MAX_BODY_BYTES = 1_000_000

# Hostnames treated as "this machine" by the Host/Origin guards.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Actions that spend model budget and therefore need a confirm token.
_PAID_ACTIONS = frozenset({"triage", "solve", "run", "brief"})

# Rejection when a paid action (including a guided scout) arrives without the
# confirm token minted for its exact estimate parameters.
_TOKEN_ERROR = "paid action needs a valid confirm token from its estimate request"


def _is_local_host(value: str | None) -> bool:
    """Whether a Host/Origin/Referer host part names this machine."""
    if not value:
        return False
    host = value.strip().lower()
    if ":" in host and not host.startswith("["):
        host = host.rsplit(":", 1)[0]
    host = host.strip("[]")
    return host in _LOCAL_HOSTS


def _origin_host(origin: str | None) -> str | None:
    """Host part of an Origin/Referer header value (None when absent)."""
    if not origin:
        return None
    try:
        return urllib.parse.urlparse(origin).hostname
    except ValueError:
        return None


def check_request_local(headers: Any) -> bool:
    """DNS-rebinding/CSRF guard: Host must be local, Origin/Referer too.

    ``headers`` is any mapping with ``get`` (email.message.Message works).
    A present-but-foreign ``Host`` is rejected; a foreign ``Origin`` or
    ``Referer`` is rejected. Missing ``Host`` (HTTP/1.0) is accepted, but
    a present foreign Origin is still rejected.
    """
    get = headers.get
    host = get("Host")
    if host is not None and not _is_local_host(host):
        return False
    for name in ("Origin", "Referer"):
        origin_host = _origin_host(get(name))
        if origin_host is not None and not _is_local_host(origin_host):
            return False
    return True


@dataclass
class Job:
    """One background action: status, captured log and result."""

    job_id: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    status: str = "queued"  # queued -> running -> done | error
    log: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str = ""


@dataclass
class _Token:
    action: str
    params_key: str
    expires_at: float


def _fresh_run_id(prefix: str) -> str:
    """Timestamped run id for panel jobs (mirrors the CLI generators)."""
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}"


def _params_key(params: dict[str, Any]) -> str:
    """Canonical key binding a confirm token to exact action parameters."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def _parse_guided(value: Any, default: bool = True) -> bool:
    """Lenient guided flag: missing means ``default`` (on)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off", ""):
        return False
    return default


def _read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# Fields surfaced from ``proof/<run_id>/linux-verify.json`` (keyless Linux
# CI rebuild) on the panel. ``reason`` is included when the file records it
# so the detail view can explain the Linux verdict.
_LINUX_VERIFY_FIELDS = (
    "verdict",
    "f2p_pass",
    "regressions",
    "suite_scope",
    "ci_run",
    "platform",
    "date",
)


def _linux_verify_for_run(root: Path, run_id: str) -> dict[str, Any] | None:
    """The linux-verify.json record for one run, or None when absent."""
    path = root / "proof" / run_id / "linux-verify.json"
    if not path.is_file():
        return None
    try:
        doc = _read_json_file(path)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    record = {key: doc.get(key) for key in _LINUX_VERIFY_FIELDS}
    if isinstance(doc.get("reason"), str) and doc["reason"].strip():
        record["reason"] = doc["reason"]
    return record


def _read_reconciliation(root: Path) -> dict[str, dict[str, Any]]:
    """Corrected per-run costs from ``evals/results/cost-reconciliation.json``.

    The ledger (``model_call`` events) omits Muse reasoning tokens, so this
    optional file records the corrected figure next to the logged one. A
    missing or malformed file is ignored: every run then shows its ledger
    cost with no correction fields set.
    """
    path = root / "evals" / "results" / "cost-reconciliation.json"
    try:
        doc = _read_json_file(path)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    runs = doc.get("runs")
    if not isinstance(runs, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for run_id, entry in runs.items():
        if not isinstance(entry, dict):
            continue
        cost = entry.get("cost_usd")
        if not isinstance(cost, (int, float)):
            continue
        logged = entry.get("logged_usd")
        basis = entry.get("basis")
        out[str(run_id)] = {
            "cost_usd": round(float(cost), 6),
            "logged_usd": (
                round(float(logged), 6)
                if isinstance(logged, (int, float))
                else None
            ),
            "basis": str(basis) if isinstance(basis, str) else "",
        }
    return out


def _display_cost(
    ledger_usd: float | None, adj: dict[str, Any] | None
) -> dict[str, Any]:
    """Ledger cost plus the optional reconciled (reasoning-inclusive) figure."""
    if adj is None:
        return {
            "cost_usd": ledger_usd,
            "cost_logged_usd": None,
            "cost_basis": "",
            "cost_corrected": False,
        }
    logged = adj["logged_usd"] if adj["logged_usd"] is not None else ledger_usd
    return {
        "cost_usd": adj["cost_usd"],
        "cost_logged_usd": logged,
        "cost_basis": adj["basis"],
        "cost_corrected": True,
    }


def _run_kind(run_id: str) -> str:
    """Panel grouping for a run id: scout, triage, video, or solve."""
    if run_id.startswith("scout-"):
        return "scout"
    if run_id.startswith("triage-"):
        return "triage"
    if run_id.startswith("video-"):
        return "video"
    return "solve"


def _best_verdict(local_verdict: str, linux: dict[str, Any] | None, kind: str) -> str:
    """Best-known verdict: the Linux verdict wins, then the kind label.

    Scout/triage/video runs are cataloguing or media work, not fixes,
    so they show ``SCOUT``/``TRIAGE``/``VIDEO`` instead of reading like an
    unfinished fix (``PENDING``). Anything else falls back to the local
    verdict.
    """
    if linux is not None and str(linux.get("verdict", "")).strip():
        return str(linux["verdict"])
    if kind == "scout":
        return "SCOUT"
    if kind == "triage":
        return "TRIAGE"
    if kind == "video":
        return "VIDEO"
    return local_verdict


def _verdict_for_run(root: Path, run_id: str, events: list[Any]) -> str:
    """Local verdict: proof bundle wins, then trace, else PENDING."""
    verdict_path = root / "proof" / run_id / "verdict.json"
    if verdict_path.is_file():
        try:
            doc = _read_json_file(verdict_path)
            if isinstance(doc, dict) and str(doc.get("verdict", "")).strip():
                return str(doc["verdict"])
        except (OSError, ValueError):
            pass
    verdict = ""
    for event in events:
        event_type = event.type if hasattr(event, "type") else event.get("type", "")
        if event_type == "verify_verdict":
            value = event.verdict if hasattr(event, "verdict") else event.get("verdict", "")
            verdict = str(value or verdict or "ERROR")
        elif event_type == "abstained":
            verdict = "ABSTAIN"
        elif event_type == "error":
            verdict = "ERROR"
    return verdict or "PENDING"


def _cost_for_events(events: list[Any]) -> float | None:
    """Total model spend across ``model_call`` events (None prices skipped)."""
    total = 0.0
    seen = False
    for event in events:
        event_type = event.type if hasattr(event, "type") else event.get("type", "")
        if event_type != "model_call":
            continue
        cost = event.cost_usd if hasattr(event, "cost_usd") else event.get("cost_usd")
        if isinstance(cost, (int, float)):
            total += float(cost)
            seen = True
    return round(total, 6) if seen else 0.0


def summarize_runs(root: Path) -> dict[str, Any]:
    """Every run with verdict, cost and hash-chain status, plus total spend."""
    from issa_as import traces as trace_log

    run_ids: set[str] = set()
    traces_dir = root / "traces"
    if traces_dir.is_dir():
        for path in sorted(traces_dir.glob("*.jsonl")):
            if path.stem:
                run_ids.add(path.stem)
    proof_dir = root / "proof"
    if proof_dir.is_dir():
        for path in sorted(proof_dir.iterdir()):
            if path.is_dir() and path.name:
                run_ids.add(path.name)
    recon = _read_reconciliation(root)
    runs: list[dict[str, Any]] = []
    ledgers: list[float] = []
    for run_id in sorted(run_ids):
        try:
            events = trace_log.load(run_id, traces_dir)
        except (OSError, ValueError):
            events = []
        try:
            chain_ok: bool | None = trace_log.verify_chain(run_id, traces_dir).ok
        except (OSError, ValueError):
            chain_ok = None if not events else False
        if not events and not (proof_dir / run_id).is_dir():
            continue
        local_verdict = _verdict_for_run(root, run_id, events)
        linux = _linux_verify_for_run(root, run_id)
        kind = _run_kind(run_id)
        ledger = _cost_for_events(events)
        ledgers.append(float(ledger or 0.0))
        cost = _display_cost(ledger, recon.get(run_id))
        runs.append(
            {
                "run_id": run_id,
                "verdict": _best_verdict(local_verdict, linux, kind),
                "local_verdict": local_verdict,
                "linux": linux,
                "kind": kind,
                "cost_usd": cost["cost_usd"],
                "cost_logged_usd": cost["cost_logged_usd"],
                "cost_basis": cost["cost_basis"],
                "cost_corrected": cost["cost_corrected"],
                "chain_ok": chain_ok,
                "n_events": len(events),
            }
        )
    total = round(sum(ledgers), 6)
    return {
        "budget_total_usd": BUDGET_TOTAL_USD,
        "total_spend_usd": total,
        "remaining_usd": round(BUDGET_TOTAL_USD - total, 6),
        "n_runs": len(runs),
        "runs": runs,
    }


def _proof_verdict(root: Path, run_id: str) -> dict[str, Any] | None:
    """The ``proof/<run_id>/verdict.json`` record, or None when absent."""
    path = root / "proof" / run_id / "verdict.json"
    if not path.is_file():
        return None
    try:
        doc = _read_json_file(path)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _local_summary(
    root: Path, run_id: str, local_verdict: str, reason: Any
) -> str:
    """One muted line describing the local (Windows) outcome.

    Prefers the first content line of ``proof/<run_id>/error.md`` and falls
    back to the first line of the local failure reason.
    """
    first = ""
    try:
        text = (root / "proof" / run_id / "error.md").read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            first = stripped
            break
    if not first and isinstance(reason, str) and reason.strip():
        first = reason.strip().splitlines()[0][:300]
    label = f"Local run (Windows): {local_verdict or 'UNKNOWN'}"
    return f"{label} — {first}" if first else label


def _best_f2p(
    linux: dict[str, Any] | None,
    verify: dict[str, Any] | None,
    test_path: str,
) -> dict[str, Any]:
    """Best-known fail-to-pass evidence: the Linux record wins when it passes.

    When ``linux.f2p_pass`` is true the strip shows FAIL on base → PASS with
    fix backed by the Linux CI record; otherwise it shows the local result.
    """
    if isinstance(linux, dict) and linux.get("f2p_pass") is True:
        return {
            "source": "linux",
            "f2p_pass": True,
            "regressions": linux.get("regressions"),
            "test_path": test_path,
            "suite_scope": linux.get("suite_scope") or "",
            "ci_run": linux.get("ci_run") or "",
            "reason": linux.get("reason") or "",
        }
    if isinstance(verify, dict) and isinstance(verify.get("f2p_pass"), bool):
        return {
            "source": "local",
            "f2p_pass": verify.get("f2p_pass"),
            "regressions": verify.get("regressions"),
            "test_path": test_path,
            "suite_scope": "",
            "ci_run": "",
            "reason": verify.get("reason") or "",
        }
    return {
        "source": "none",
        "f2p_pass": None,
        "regressions": None,
        "test_path": test_path,
        "suite_scope": "",
        "ci_run": "",
        "reason": "",
    }


def run_detail(root: Path, run_id: str) -> dict[str, Any] | None:
    """Trace timeline, decision, work/verification and result for one run."""
    from issa_as import traces as trace_log
    from issa_as.workspace import validate_run_id

    try:
        validate_run_id(run_id)
    except Exception:
        return None
    traces_dir = root / "traces"
    if not (traces_dir / f"{run_id}.jsonl").is_file():
        return None
    try:
        events = trace_log.load(run_id, traces_dir)
        chain = trace_log.verify_chain(run_id, traces_dir)
    except (OSError, ValueError):
        return None
    timeline = [e.model_dump(mode="json") for e in events]
    selected = next(
        (e for e in timeline if e.get("type") == "selected"), None
    )
    skipped = [e for e in timeline if e.get("type") == "skipped"]
    calls = [e for e in timeline if e.get("type") == "model_call"]
    repro = next((e for e in timeline if e.get("type") == "repro_result"), None)
    fix = next((e for e in timeline if e.get("type") == "fix_attempt"), None)
    verify = next((e for e in timeline if e.get("type") == "verify_verdict"), None)
    proof: dict[str, Any] | None = None
    bundle_dir = root / "proof" / run_id
    if bundle_dir.is_dir():
        proof = {"files": sorted(p.name for p in bundle_dir.iterdir())}
        for name in ("verdict.json", "cost.json"):
            path = bundle_dir / name
            if path.is_file():
                try:
                    proof[name.replace(".json", "")] = _read_json_file(path)
                except (OSError, ValueError):
                    proof[name.replace(".json", "")] = {"error": "unreadable"}
        patch_path = bundle_dir / "fix.patch"
        if patch_path.is_file():
            try:
                proof["fix_patch"] = patch_path.read_text(encoding="utf-8")
            except OSError:
                proof["fix_patch"] = ""
    local_verdict = _verdict_for_run(root, run_id, events)
    linux = _linux_verify_for_run(root, run_id)
    kind = _run_kind(run_id)
    verdict_doc = proof.get("verdict") if isinstance(proof, dict) else None
    if not isinstance(verdict_doc, dict):
        verdict_doc = None
    if repro is None and verdict_doc is not None:
        bundle_test = verdict_doc.get("test_file") or verdict_doc.get("test_path")
        if bundle_test:
            repro = {"type": "repro_result", "test_path": bundle_test}
    if verify is None and verdict_doc is not None:
        if (
            verdict_doc.get("f2p_pass") is not None
            or verdict_doc.get("regressions") is not None
        ):
            verify = {
                "type": "verify_verdict",
                "verdict": verdict_doc.get("verdict"),
                "reason": verdict_doc.get("reason"),
                "f2p_pass": verdict_doc.get("f2p_pass"),
                "regressions": verdict_doc.get("regressions"),
            }
    if fix is None and verdict_doc is not None:
        bundle_hash = verdict_doc.get("patch_hash") or verdict_doc.get("fix_commit")
        if bundle_hash or verdict_doc.get("patch_summary") or verdict_doc.get("effort"):
            fix = {
                "type": "fix_attempt",
                "effort": verdict_doc.get("effort"),
                "patch_hash": bundle_hash,
                "explanation": verdict_doc.get("patch_summary"),
            }
    issue_url = ""
    for entry in timeline:
        if entry.get("issue_url"):
            issue_url = str(entry["issue_url"])
            break
    if not issue_url and verdict_doc is not None and verdict_doc.get("issue_url"):
        issue_url = str(verdict_doc["issue_url"])
    best_test_path = ""
    if isinstance(repro, dict) and repro.get("test_path"):
        best_test_path = str(repro["test_path"])
    elif verdict_doc is not None and (
        verdict_doc.get("test_file") or verdict_doc.get("test_path")
    ):
        best_test_path = str(
            verdict_doc.get("test_file") or verdict_doc.get("test_path")
        )
    local_reason = ""
    if isinstance(verify, dict) and verify.get("reason"):
        local_reason = str(verify["reason"])
    elif verdict_doc is not None and verdict_doc.get("reason"):
        local_reason = str(verdict_doc["reason"])
    ledger = _cost_for_events(events)
    cost = _display_cost(ledger, _read_reconciliation(root).get(run_id))
    return {
        "run_id": run_id,
        "verdict": _best_verdict(local_verdict, linux, kind),
        "local_verdict": local_verdict,
        "linux": linux,
        "kind": kind,
        "chain": {"ok": chain.ok, "bad_line": chain.bad_line, "reason": chain.reason},
        "cost_usd": cost["cost_usd"],
        "cost_logged_usd": cost["cost_logged_usd"],
        "cost_basis": cost["cost_basis"],
        "cost_corrected": cost["cost_corrected"],
        "issue_url": issue_url,
        "f2p": _best_f2p(linux, verify, best_test_path),
        "local_summary": _local_summary(root, run_id, local_verdict, local_reason),
        "timeline": timeline,
        "decision": {"selected": selected, "skipped": skipped},
        "work": {
            "repro_result": repro,
            "fix_attempt": fix,
            "verify_verdict": verify,
            "model_calls": calls,
        },
        "proof": proof,
    }


def _read_briefs_doc(path: Path) -> dict[str, Any] | None:
    """The validated briefs map of one ``*.briefs.json`` file, or None."""
    if not path.is_file():
        return None
    try:
        doc = _read_json_file(path)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    briefs = doc.get("briefs", {})
    return briefs if isinstance(briefs, dict) else None


def _briefs_for(root: Path, scout_run_id: str) -> dict[str, Any] | None:
    """The briefs map of a scout report, or None when no briefs exist."""
    return _read_briefs_doc(root / "scout" / f"{scout_run_id}.briefs.json")


def _find_brief(root: Path, issue_url: str) -> dict[str, Any] | None:
    """First briefs-file entry for an issue URL across all scout briefs."""
    scout_dir = root / "scout"
    if not issue_url or not scout_dir.is_dir():
        return None
    for path in sorted(scout_dir.glob("*.briefs.json")):
        briefs = _read_briefs_doc(path)
        if briefs and isinstance(briefs.get(issue_url), dict):
            entry = briefs[issue_url]
            return {
                "title": entry.get("title", {}),
                "problem": entry.get("problem", {}),
                "fix_idea": entry.get("fix_idea", {}),
                "source_language": entry.get("source_language", "unknown"),
            }
    return None


# Display names for the live "Now working on" card.
_LIVE_STAGES = {
    "run": "scouting",
    "scout": "scouting",
    "brief": "briefing",
    "triage": "triaging",
    "critic": "triaging",
    "select": "selecting",
    "workspace": "solving",
    "reproduce": "solving",
    "fixer": "solving",
    "verifier": "verifying",
    "attester": "verifying",
    "publish": "verifying",
}


def live_status(root: Path, run_id: str) -> dict[str, Any] | None:
    """Current stage, selected issue (+brief) and verdict for a live run.

    Derived from the run trace only: the stage is the latest
    non-accounting event, the selection comes from the ``selected``
    event, and the brief is looked up across all scout briefs files.
    Returns None when the run has no trace (unknown run).
    """
    from issa_as import traces as trace_log
    from issa_as.workspace import validate_run_id

    try:
        validate_run_id(run_id)
    except Exception:
        return None
    traces_dir = root / "traces"
    if not (traces_dir / f"{run_id}.jsonl").is_file():
        return None
    try:
        events = trace_log.load(run_id, traces_dir)
    except (OSError, ValueError):
        return None
    if not events:
        return None
    stage = "scouting"
    for event in reversed(events):
        dump = event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
        if dump.get("type") == "model_call":
            continue
        stage = _LIVE_STAGES.get(str(dump.get("stage", "")), "scouting")
        break
    issue_url = ""
    model = ""
    effort = ""
    verdict = ""
    for event in events:
        dump = event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
        event_type = dump.get("type", "")
        if event_type == "selected":
            issue_url = str(dump.get("issue_id", "") or issue_url)
            model = str(dump.get("model", "") or model)
            effort = str(dump.get("effort", "") or effort)
        elif event_type == "verify_verdict":
            verdict = str(dump.get("verdict", "") or verdict or "ERROR")
        elif event_type == "abstained":
            verdict = "ABSTAIN"
        elif event_type == "error":
            verdict = "ERROR"
    return {
        "run_id": run_id,
        "stage": stage,
        "issue_url": issue_url,
        "model": model,
        "effort": effort,
        "brief": _find_brief(root, issue_url),
        "verdict": verdict,
        "n_events": len(events),
    }


def list_reports(root: Path, dirname: str, suffix: str) -> list[dict[str, Any]]:
    """Summaries of ``scout/*.json`` or ``triage/*.json`` reports."""
    directory = root / dirname
    reports: list[dict[str, Any]] = []
    if not directory.is_dir():
        return reports
    for path in sorted(directory.glob(f"*{suffix}")):
        try:
            doc = _read_json_file(path)
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        entry: dict[str, Any] = {"run_id": path.stem}
        if dirname == "scout":
            candidates = doc.get("candidates", [])
            entry["n_candidates"] = len(candidates) if isinstance(candidates, list) else 0
            entry["rate_limited"] = bool(doc.get("rate_limited", False))
        else:
            scored = doc.get("scored", [])
            entry["scout_run_id"] = doc.get("scout_run_id", "")
            entry["n_scored"] = len(scored) if isinstance(scored, list) else 0
            selection = doc.get("selection", {}) or {}
            picked = (selection.get("picked") or {}) if isinstance(selection, dict) else {}
            url = picked.get("issue_url", "") if isinstance(picked, dict) else ""
            entry["picked_issue_url"] = url
            skipped = selection.get("skipped", []) if isinstance(selection, dict) else []
            entry["n_skipped"] = len(skipped)
        reports.append(entry)
    return reports


def triage_detail(root: Path, run_id: str) -> dict[str, Any] | None:
    """Scored candidates joined with critic verdicts and model provenance.

    The deterministic score (``signals_score``), the Spark Open solvability
    numbers, the critic adjustment (``solvability_v2``/``delta``/``flag``)
    and the final score come from ``triage/<run_id>.json``; the model and
    provider behind each number, plus reasoning excerpts, come from the
    same run id's trace events.
    """
    from issa_as import traces as trace_log
    from issa_as.workspace import validate_run_id

    try:
        validate_run_id(run_id)
    except Exception:
        return None
    path = root / "triage" / f"{run_id}.json"
    if not path.is_file():
        return None
    try:
        doc = _read_json_file(path)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    try:
        events = trace_log.load(run_id, root / "traces")
    except (OSError, ValueError):
        events = []
    triage_marks: dict[str, dict[str, Any]] = {}
    critic_marks: dict[str, dict[str, Any]] = {}
    call_by_stage: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        dump = event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
        event_type = dump.get("type", "")
        issue_id = str(dump.get("issue_id", "") or "")
        if event_type == "triage_scored" and issue_id:
            triage_marks[issue_id] = {
                "model": dump.get("model", ""),
                "provider": dump.get("provider", ""),
            }
        elif event_type == "critic_rescored" and issue_id:
            critic_marks[issue_id] = {
                "model": dump.get("model", ""),
                "provider": dump.get("provider", ""),
            }
        elif event_type == "model_call":
            call_by_stage.setdefault(str(dump.get("stage", "")), []).append(dump)
    triage_call = (call_by_stage.get("triage") or [{}])[0]
    critic_call = (call_by_stage.get("critic") or [{}])[0]
    scored = doc.get("scored", []) if isinstance(doc.get("scored"), list) else []
    verdicts = {
        str(v.get("issue_url", "")): v
        for v in (doc.get("critic", []) or [])
        if isinstance(v, dict)
    }
    joined: list[dict[str, Any]] = []
    for item in scored:
        if not isinstance(item, dict):
            continue
        url = str(item.get("issue_url", ""))
        critic = verdicts.get(url, {})
        joined.append(
            {
                **item,
                "critic": critic,
                "triage_model": triage_marks.get(url, {}).get(
                    "model", triage_call.get("model", "")
                ),
                "triage_provider": triage_marks.get(url, {}).get(
                    "provider", triage_call.get("provider", "")
                ),
                "critic_model": critic_marks.get(url, {}).get(
                    "model", critic_call.get("model", "")
                ),
                "critic_provider": critic_marks.get(url, {}).get(
                    "provider", critic_call.get("provider", "")
                ),
                "triage_reasoning_excerpt": triage_call.get("reasoning_excerpt", ""),
                "critic_reasoning_excerpt": critic_call.get("reasoning_excerpt", ""),
            }
        )
    selection = doc.get("selection") if isinstance(doc.get("selection"), dict) else {}
    picked = (selection or {}).get("picked")
    if isinstance(picked, dict) and not picked.get("recommended_max_usd"):
        from issa_as import select as select_mod

        picked["recommended_max_usd"] = select_mod.recommended_max_usd(
            str(picked.get("recommended_solver_model", "")),
            str(picked.get("recommended_effort", "")),
        )
    return {
        "run_id": run_id,
        "scout_run_id": doc.get("scout_run_id", ""),
        "briefs": _briefs_for(root, str(doc.get("scout_run_id", "") or "")) or {},
        "scored": joined,
        "selection": selection,
        "triage_tokens": {
            "in_tokens": triage_call.get("in_tokens", 0),
            "out_tokens": triage_call.get("out_tokens", 0),
            "reasoning_tokens": triage_call.get("reasoning_tokens", 0),
            "cost_usd": triage_call.get("cost_usd"),
        },
        "critic_tokens": {
            "in_tokens": critic_call.get("in_tokens", 0),
            "out_tokens": critic_call.get("out_tokens", 0),
            "reasoning_tokens": critic_call.get("reasoning_tokens", 0),
            "cost_usd": critic_call.get("cost_usd"),
        },
    }


def _round_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    return None


def estimate_triage(root: Path, scout_run_id: str) -> dict[str, Any]:
    """Offline triage + critic cost estimate for one scout report."""
    from issa_as import critic as critic_mod
    from issa_as import triage as triage_mod
    from issa_as.budget import cost_usd
    from issa_as.providers import max_tokens_for_batch
    from issa_as.workspace import validate_run_id

    validate_run_id(scout_run_id)
    path = root / "scout" / f"{scout_run_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no such scout report: {scout_run_id}")
    doc = _read_json_file(path)
    candidates = doc.get("candidates", []) if isinstance(doc, dict) else []
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"scout report has no candidates: {scout_run_id}")
    info = triage_mod.dry_run_info(candidates, None)
    top_k = min(len(candidates), critic_mod.CRITIC_TOP_K)
    critic_in = info["est_in_tokens"]
    critic_out = max_tokens_for_batch(top_k) if top_k else 0
    critic_usd = cost_usd(info["model"], critic_in, critic_out)
    return {
        "scout_run_id": scout_run_id,
        "model": info["model"],
        "provider": info["provider"],
        "n_candidates": info["n_candidates"],
        "triage": {
            "est_in_tokens": info["est_in_tokens"],
            "est_out_tokens": info["est_out_tokens"],
            "est_cost_usd": _round_or_none(info["est_cost_usd"]),
            "price_known": info["price_known"],
        },
        "critic": {
            "top_k": top_k,
            "est_in_tokens": critic_in,
            "est_out_tokens": critic_out,
            "est_cost_usd": _round_or_none(critic_usd),
        },
    }


def estimate_brief(root: Path, scout_run_id: str) -> dict[str, Any]:
    """Offline plain-language briefs cost estimate for one scout report."""
    from issa_as import briefs as briefs_mod
    from issa_as.workspace import validate_run_id

    validate_run_id(scout_run_id)
    path = root / "scout" / f"{scout_run_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no such scout report: {scout_run_id}")
    doc = _read_json_file(path)
    candidates = doc.get("candidates", []) if isinstance(doc, dict) else []
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"scout report has no candidates: {scout_run_id}")
    info = briefs_mod.dry_run_info(candidates, None)
    return {
        "scout_run_id": scout_run_id,
        "model": info["model"],
        "provider": info["provider"],
        "n_candidates": info["n_candidates"],
        "brief": {
            "est_in_tokens": info["est_in_tokens"],
            "est_out_tokens": info["est_out_tokens"],
            "est_cost_usd": _round_or_none(info["est_cost_usd"]),
            "price_known": info["price_known"],
        },
    }


def estimate_solve(
    issue_url: str,
    model: str,
    effort: str,
    max_steps: int | None,
) -> dict[str, Any]:
    """Offline solver cost estimate from the per-step table."""
    from issa_as import select as select_mod
    from issa_as.solver import SolverError, default_max_steps, per_step_cost_usd
    from issa_as.workspace import parse_issue_url

    parse_issue_url(issue_url)
    if max_steps is not None and max_steps <= 0:
        raise ValueError(f"max_steps must be positive, got {max_steps!r}")
    try:
        steps = (
            int(max_steps)
            if max_steps is not None
            else default_max_steps(model, effort)
        )
    except SolverError as exc:
        raise ValueError(str(exc)) from exc
    per_step = per_step_cost_usd(model, effort)
    return {
        "issue_url": issue_url,
        "model": model,
        "effort": effort,
        "steps": steps,
        "est_cost_usd": round(steps * per_step, 6),
        "recommended_max_usd": select_mod.recommended_max_usd(model, effort),
    }


def estimate_run(
    languages: str, limit: int, max_usd: float, guided: bool = True
) -> dict[str, Any]:
    """Offline full-loop estimate: guided proposal + triage/critic + solver allowance.

    With ``guided`` (the default) the reply adds the guided query-proposal
    step as one extra Spark Open call in ``est_calls`` with its price in
    ``guided_est_usd``.
    """
    from issa_as import critic as critic_mod
    from issa_as import select as select_mod
    from issa_as import triage as triage_mod
    from issa_as.budget import cost_usd
    from issa_as.providers import max_tokens_for_batch

    if limit <= 0:
        raise ValueError("--limit must be positive")
    if max_usd < 0:
        raise ValueError("--max-usd must be >= 0")
    fake = [
        {
            "issue_url": f"https://github.com/o/r/issues/{n}",
            "title": "Bug",
            "labels": ["bug"],
            "security_class": None,
            "runner": "python",
            "stars": 100,
            "signals_score": 0.5,
            "signals": {},
            "body_excerpt": "Steps to reproduce: run it.",
        }
        for n in range(1, limit + 1)
    ]
    info = triage_mod.dry_run_info(fake, None)
    top_k = min(limit, critic_mod.CRITIC_TOP_K)
    critic_out = max_tokens_for_batch(top_k)
    critic_usd = cost_usd(info["model"], info["est_in_tokens"], critic_out)
    triage_usd = info["est_cost_usd"] or 0.0
    from issa_as import briefs as briefs_mod

    brief_usd = briefs_mod.dry_run_info(fake, None)["est_cost_usd"] or 0.0
    guided_usd = None
    if guided:
        from issa_as import scout as scout_mod

        prompt = scout_mod.build_guided_prompt([])
        guided_usd = cost_usd(
            info["model"], max(len(prompt) // 4, 1), scout_mod.GUIDED_MAX_TOKENS
        )
    return {
        "languages": languages,
        "limit": limit,
        "max_usd": max_usd,
        "guided": guided,
        "triage_est_usd": _round_or_none(info["est_cost_usd"]),
        "critic_est_usd": _round_or_none(critic_usd),
        "guided_est_usd": _round_or_none(guided_usd),
        "brief_est_usd": _round_or_none(brief_usd),
        "est_calls": 3 + (1 if guided else 0),
        "solver_allowance_usd": max(max_usd, select_mod.ESCALATED_MAX_USD),
        "note": (
            "Synthetic estimate for planning only: triage/critic scale with "
            f"candidate count; the solver allowance rises to the escalated "
            f"recommendation ({select_mod.ESCALATED_MAX_USD:.2f}) when the pick routes "
            "to muse-spark-1.3/high. "
            f"~{(triage_usd + brief_usd + (critic_usd or 0.0) + (guided_usd or 0.0)):.4f}"
            " USD before solve"
            + (" (guided query proposal included)." if guided else ".")
        ),
    }


def estimate_scout(
    limit: int, languages: str, guided: bool = True
) -> dict[str, Any]:
    """Offline scout estimate: GitHub search is free, guided adds one call.

    With ``guided`` (the default) the reply prices one Spark Open
    query-proposal call (``scout`` stage) in ``guided_est_usd`` and counts
    it in ``est_calls``. An unguided scout makes no model call at all.
    """
    from issa_as import scout as scout_mod
    from issa_as import triage as triage_mod
    from issa_as.budget import cost_usd

    if limit <= 0:
        raise ValueError("--limit must be positive")
    guided_usd = None
    model = ""
    if guided:
        model = triage_mod.dry_run_info([], None)["model"]
        prompt = scout_mod.build_guided_prompt([])
        guided_usd = cost_usd(
            model, max(len(prompt) // 4, 1), scout_mod.GUIDED_MAX_TOKENS
        )
    return {
        "limit": limit,
        "languages": languages,
        "guided": guided,
        "model": model,
        "guided_est_usd": _round_or_none(guided_usd),
        "est_calls": 1 if guided else 0,
        "note": (
            "GitHub search is free; a guided scout adds one Spark Open call "
            "to propose the searches."
            if guided
            else "Unguided scout: fixed queries only, no model call."
        ),
    }


def _run_scout(server: PanelServer, params: dict[str, Any]) -> dict[str, Any]:
    import os as _os

    from issa_as import cli as cli_mod

    _os.chdir(server.root)
    rid = str(params.get("run_id", "")) or _fresh_run_id("scout")
    _set_job_run_id(server, rid)
    _, found = cli_mod.run_scout(
        limit=int(params.get("limit", 30)),
        languages=str(params.get("languages", "python,node")),
        guided=_parse_guided(params.get("guided"), True),
        run_id=rid,
    )
    return {"run_id": rid, "n_candidates": len(found)}


def _run_triage(server: PanelServer, params: dict[str, Any]) -> dict[str, Any]:
    import os as _os

    from issa_as import cli as cli_mod

    rid = str(params.get("run_id", "")) or _fresh_run_id("triage")
    _set_job_run_id(server, rid)
    _os.chdir(server.root)
    code = cli_mod.run_triage(
        str(params["scout_run_id"]), run_id=rid
    )
    if code != 0:
        raise RuntimeError(f"triage exited with code {code}")
    return {"scout_run_id": str(params["scout_run_id"]), "run_id": rid}


def _run_brief(server: PanelServer, params: dict[str, Any]) -> dict[str, Any]:
    import os as _os

    from issa_as import cli as cli_mod

    rid = str(params.get("run_id", "")) or _fresh_run_id("brief")
    _set_job_run_id(server, rid)
    _os.chdir(server.root)
    code = cli_mod.run_brief(
        str(params["scout_run_id"]), run_id=rid
    )
    if code != 0:
        raise RuntimeError(f"brief exited with code {code}")
    return {"scout_run_id": str(params["scout_run_id"]), "run_id": rid}


def _run_solve(server: PanelServer, params: dict[str, Any]) -> dict[str, Any]:
    from issa_as import pipeline as pipe_mod
    from issa_as import select as select_mod
    from issa_as.budget import IssueCaps
    from issa_as.solver import default_max_steps

    model = str(params.get("model", "muse-spark-1.3-contributor"))
    if params.get("run_id"):
        _set_job_run_id(server, str(params["run_id"]))
    effort = str(params.get("effort", "medium"))
    max_steps = params.get("max_steps")
    steps = (
        int(max_steps)
        if max_steps is not None
        else default_max_steps(model, effort)
    )
    max_usd = params.get("max_usd")
    allowance = (
        float(max_usd)
        if max_usd is not None
        else select_mod.recommended_max_usd(model, effort)
    )
    outcome = pipe_mod.solve_issue(
        str(params["issue_url"]),
        model=model,
        effort=effort,
        max_steps=int(max_steps) if max_steps is not None else None,
        run_id=str(params["run_id"]) if params.get("run_id") else None,
        workspaces_root=server.root / "workspaces",
        traces_dir=server.root / "traces",
        proof_root=server.root / "proof",
        caps=IssueCaps(max_steps=steps, max_usd=allowance),
    )
    return {
        "run_id": outcome.run_id,
        "verdict": outcome.verdict,
        "bundle": str(outcome.bundle_dir),
    }


def _run_recover(server: PanelServer, params: dict[str, Any]) -> dict[str, Any]:
    from issa_as import pipeline as pipe_mod

    outcome = pipe_mod.recover_run(
        str(params["run_id"]),
        workspaces_root=server.root / "workspaces",
        proof_root=server.root / "proof",
        traces_dir=server.root / "traces",
    )
    return {"run_id": outcome.run_id, "verdict": outcome.verdict}


def _run_reverify(server: PanelServer, params: dict[str, Any]) -> dict[str, Any]:
    from issa_as import pipeline as pipe_mod

    result = pipe_mod.reverify_run(
        str(params["run_id"]),
        workspaces_root=server.root / "workspaces",
        proof_root=server.root / "proof",
        traces_dir=server.root / "traces",
    )
    return {"run_id": str(params["run_id"]), "verdict": result.verdict}


def _run_full(server: PanelServer, params: dict[str, Any]) -> dict[str, Any]:
    from issa_as import run as run_mod

    rid = str(params.get("run_id", "")) or _fresh_run_id("run")
    _set_job_run_id(server, rid)
    outcome = run_mod.run(
        languages=str(params.get("languages", "python")),
        limit=int(params.get("limit", 30)),
        max_usd=float(params.get("max_usd", 0.50)),
        guided=_parse_guided(params.get("guided"), True),
        run_id=rid,
        workspaces_root=server.root / "workspaces",
        traces_dir=server.root / "traces",
        proof_root=server.root / "proof",
    )
    return {"run_id": outcome.run_id, "verdict": outcome.verdict, "reason": outcome.reason}


DEFAULT_RUNNERS: dict[str, Callable[[Any, dict[str, Any]], dict[str, Any]]] = {
    "scout": _run_scout,
    "triage": _run_triage,
    "solve": _run_solve,
    "recover": _run_recover,
    "reverify": _run_reverify,
    "run": _run_full,
    "brief": _run_brief,
}


class PanelServer(ThreadingHTTPServer):
    """HTTP server holding panel state: jobs, tokens and runners."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        root: Path,
        bind_and_activate: bool = True,
    ) -> None:
        self.root = root
        self.jobs: dict[str, Job] = {}
        self.tokens: dict[str, _Token] = {}
        self.runners: dict[str, Callable[[Any, dict[str, Any]], dict[str, Any]]] = dict(
            DEFAULT_RUNNERS
        )
        self.job_lock = threading.Lock()
        self.current_job_id: str | None = None
        super().__init__(address, PanelHandler, bind_and_activate=bind_and_activate)

    def mint_token(self, action: str, params: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(24)
        self.tokens[token] = _Token(
            action=action,
            params_key=_params_key(params),
            expires_at=time.time() + TOKEN_TTL_S,
        )
        return token

    def consume_token(self, action: str, params: dict[str, Any], token: str) -> bool:
        """Single-use, expiring, parameter-bound confirm token check."""
        record = self.tokens.pop(token, None)
        if record is None:
            return False
        if time.time() > record.expires_at:
            return False
        return record.action == action and record.params_key == _params_key(params)

    def start_job(self, kind: str, params: dict[str, Any]) -> Job | None:
        """Start a background job, or None when the worker is busy."""
        with self.job_lock:
            if self.current_job_id is not None:
                return None
            job = Job(job_id=secrets.token_hex(8), kind=kind, params=params)
            self.jobs[job.job_id] = job
            self.current_job_id = job.job_id
        thread = threading.Thread(
            target=self._execute, args=(job,), daemon=True, name=f"issa-{kind}"
        )
        thread.start()
        return job

    def _execute(self, job: Job) -> None:
        job.status = "running"
        runner = self.runners.get(job.kind)
        buffer = io.StringIO()
        try:
            if runner is None:
                raise RuntimeError(f"unknown action: {job.kind}")
            with contextlib.redirect_stdout(buffer):
                job.result = runner(self, dict(job.params))
                if isinstance(job.result, dict):
                    rid = job.result.get("run_id")
                    if isinstance(rid, str) and rid and not job.run_id:
                        job.run_id = rid
            job.status = "done"
        except Exception as exc:  # job failure is data, never a crash
            job.status = "error"
            job.error = str(exc)[:2000]
        finally:
            tail = buffer.getvalue()
            if tail:
                job.log.append(tail[-20000:])
            with self.job_lock:
                if self.current_job_id == job.job_id:
                    self.current_job_id = None


_STATIC_FILES = {
    "/": ("web/index.html", "text/html; charset=utf-8"),
    "/index.html": ("web/index.html", "text/html; charset=utf-8"),
    "/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("web/styles.css", "text/css; charset=utf-8"),
    "/logo.svg": ("web/logo.svg", "image/svg+xml; charset=utf-8"),
}


def _set_job_run_id(server: PanelServer, run_id: str) -> None:
    """Record the trace run id on the currently executing job (best effort)."""
    with server.job_lock:
        current = server.current_job_id
        job = server.jobs.get(current) if current else None
        if job is not None and not job.run_id:
            job.run_id = run_id


def _job_dump(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "status": job.status,
        "run_id": job.run_id,
        "log": "\n".join(job.log),
        "result": job.result,
        "error": job.error,
    }


class PanelHandler(BaseHTTPRequestHandler):
    """Routes: static panel, JSON reads (GET) and guarded actions (POST)."""

    server_version = "ISSAPanel/1.0"

    @property
    def panel(self) -> PanelServer:
        server = self.server
        assert isinstance(server, PanelServer)
        return server

    def log_message(self, *args: Any) -> None:
        pass

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        if not check_request_local(self.headers):
            self._send_json(403, {"error": "forbidden: localhost only"})
            return False
        return True

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            return {}
        try:
            raw = self.rfile.read(length)
        except (OSError, ValueError):
            return None
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return doc if isinstance(doc, dict) else None

    def _validated_run_id(self, raw: str) -> str | None:
        from issa_as.workspace import WorkspaceError, validate_run_id

        try:
            return validate_run_id(urllib.parse.unquote(raw))
        except WorkspaceError:
            return None

    # -- GET -----------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._guard():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path in _STATIC_FILES:
            self._serve_static(path)
            return
        parts = [p for p in path.split("/") if p]
        try:
            if parts == ["api", "overview"]:
                self._send_json(200, summarize_runs(self.panel.root))
            elif parts == ["api", "status"]:
                self._send_json(
                    200,
                    {"busy": self.panel.current_job_id is not None,
                     "current_job_id": self.panel.current_job_id},
                )
            elif parts == ["api", "runs"]:
                self._send_json(200, summarize_runs(self.panel.root))
            elif len(parts) == 3 and parts[:2] == ["api", "runs"]:
                run_id = self._validated_run_id(parts[2])
                if run_id is None:
                    self._send_json(400, {"error": "invalid run id"})
                    return
                detail = run_detail(self.panel.root, run_id)
                if detail is None:
                    self._send_json(404, {"error": "run not found"})
                    return
                self._send_json(200, detail)
            elif parts == ["api", "scout"]:
                self._send_json(200, {"reports": list_reports(self.panel.root, "scout", ".json")})
            elif len(parts) == 3 and parts[:2] == ["api", "scout"]:
                run_id = self._validated_run_id(parts[2])
                if run_id is None:
                    self._send_json(400, {"error": "invalid run id"})
                    return
                report_path = self.panel.root / "scout" / f"{run_id}.json"
                if not report_path.is_file():
                    self._send_json(404, {"error": "scout report not found"})
                    return
                try:
                    doc = _read_json_file(report_path)
                    if isinstance(doc, dict):
                        doc["briefs"] = _briefs_for(self.panel.root, run_id) or {}
                    self._send_json(200, doc)
                except (OSError, ValueError):
                    self._send_json(500, {"error": "scout report unreadable"})
            elif parts == ["api", "triage"]:
                self._send_json(200, {"reports": list_reports(self.panel.root, "triage", ".json")})
            elif len(parts) == 3 and parts[:2] == ["api", "triage"]:
                run_id = self._validated_run_id(parts[2])
                if run_id is None:
                    self._send_json(400, {"error": "invalid run id"})
                    return
                detail = triage_detail(self.panel.root, run_id)
                if detail is None:
                    self._send_json(404, {"error": "triage report not found"})
                    return
                self._send_json(200, detail)
            elif len(parts) == 3 and parts[:2] == ["api", "briefs"]:
                run_id = self._validated_run_id(parts[2])
                if run_id is None:
                    self._send_json(400, {"error": "invalid run id"})
                    return
                briefs = _briefs_for(self.panel.root, run_id)
                if briefs is None:
                    self._send_json(404, {"error": "briefs not found"})
                    return
                self._send_json(200, {"scout_run_id": run_id, "briefs": briefs})
            elif len(parts) == 3 and parts[:2] == ["api", "live"]:
                run_id = self._validated_run_id(parts[2])
                if run_id is None:
                    self._send_json(400, {"error": "invalid run id"})
                    return
                live = live_status(self.panel.root, run_id)
                if live is None:
                    self._send_json(404, {"error": "run not found"})
                    return
                self._send_json(200, live)
            elif len(parts) == 3 and parts[:2] == ["api", "proof"]:
                run_id = self._validated_run_id(parts[2])
                if run_id is None:
                    self._send_json(400, {"error": "invalid run id"})
                    return
                bundle = self.panel.root / "proof" / run_id
                if not bundle.is_dir():
                    self._send_json(404, {"error": "proof bundle not found"})
                    return
                payload: dict[str, Any] = {
                    "run_id": run_id,
                    "files": sorted(p.name for p in bundle.iterdir()),
                }
                for name in ("verdict.json", "cost.json"):
                    target = bundle / name
                    if target.is_file():
                        try:
                            payload[name.replace(".json", "")] = _read_json_file(target)
                        except (OSError, ValueError):
                            payload[name.replace(".json", "")] = {"error": "unreadable"}
                patch = bundle / "fix.patch"
                if patch.is_file():
                    try:
                        payload["fix_patch"] = patch.read_text(encoding="utf-8")
                    except OSError:
                        payload["fix_patch"] = ""
                self._send_json(200, payload)
            elif len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                job = self.panel.jobs.get(parts[2])
                if job is None:
                    self._send_json(404, {"error": "job not found"})
                    return
                self._send_json(200, _job_dump(job))
            else:
                self._send_json(404, {"error": "not found"})
        except (OSError, ValueError) as exc:
            self._send_json(500, {"error": f"read failed: {exc}"})

    def _serve_static(self, path: str) -> None:
        name, content_type = _STATIC_FILES[path]
        target = self.panel.root / name
        try:
            body = target.read_bytes()
        except OSError:
            self._send_json(404, {"error": "panel file not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- POST -----------------------------------------------------------------

    def do_POST(self) -> None:
        if not self._guard():
            return
        path = urllib.parse.urlsplit(self.path).path
        parts = [p for p in path.split("/") if p]
        if len(parts) == 3 and parts[:2] == ["api", "estimate"]:
            self._handle_estimate(parts[2])
            return
        if len(parts) == 3 and parts[:2] == ["api", "actions"]:
            self._handle_action(parts[2])
            return
        if parts[:1] == ["api"]:
            self._send_json(404, {"error": "not found"})
            return
        self._send_json(405, {"error": "method not allowed: use GET for reads"})

    def do_PUT(self) -> None:
        self._guard_json_method()

    def do_DELETE(self) -> None:
        self._guard_json_method()

    def do_PATCH(self) -> None:
        self._guard_json_method()

    def _guard_json_method(self) -> None:
        if not self._guard():
            return
        self._send_json(405, {"error": "method not allowed: reads are GET, actions are POST"})

    def _handle_estimate(self, kind: str) -> None:
        from issa_as.workspace import WorkspaceError

        body = self._read_json_body()
        if body is None:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        try:
            if kind == "brief":
                scout_run_id = str(body.get("scout_run_id", ""))
                estimate = estimate_brief(self.panel.root, scout_run_id)
                params = {"scout_run_id": scout_run_id}
                if body.get("run_id"):
                    params["run_id"] = str(body["run_id"])
                token = self.panel.mint_token("brief", params)
                self._send_json(200, {**estimate, "confirm_token": token})
            elif kind == "triage":
                scout_run_id = str(body.get("scout_run_id", ""))
                estimate = estimate_triage(self.panel.root, scout_run_id)
                params = {"scout_run_id": scout_run_id}
                if body.get("run_id"):
                    params["run_id"] = str(body["run_id"])
                token = self.panel.mint_token("triage", params)
                self._send_json(200, {**estimate, "confirm_token": token})
            elif kind == "solve":
                issue_url = str(body.get("issue_url", ""))
                model = str(body.get("model", "muse-spark-1.3-contributor"))
                effort = str(body.get("effort", "medium"))
                max_steps = body.get("max_steps")
                estimate = estimate_solve(
                    issue_url, model, effort,
                    int(max_steps) if max_steps is not None else None,
                )
                params = {"issue_url": issue_url, "model": model, "effort": effort}
                if max_steps is not None:
                    params["max_steps"] = int(max_steps)
                if body.get("max_usd") is not None:
                    params["max_usd"] = float(body["max_usd"])
                if body.get("run_id"):
                    params["run_id"] = str(body["run_id"])
                token = self.panel.mint_token("solve", params)
                self._send_json(200, {**estimate, "confirm_token": token})
            elif kind == "run":
                languages = str(body.get("languages", "python"))
                limit = int(body.get("limit", 30))
                max_usd = float(body.get("max_usd", 0.50))
                guided = _parse_guided(body.get("guided"), True)
                estimate = estimate_run(languages, limit, max_usd, guided)
                params = {
                    "languages": languages,
                    "limit": limit,
                    "max_usd": max_usd,
                    "guided": guided,
                }
                if body.get("run_id"):
                    params["run_id"] = str(body["run_id"])
                token = self.panel.mint_token("run", params)
                self._send_json(200, {**estimate, "confirm_token": token})
            else:
                if kind == "scout":
                    try:
                        scout_limit = int(body.get("limit", 30))
                        scout_languages = str(body.get("languages", "python,node"))
                    except (TypeError, ValueError):
                        raise ValueError("invalid scout parameters")
                    scout_guided = _parse_guided(body.get("guided"), True)
                    estimate = estimate_scout(scout_limit, scout_languages, scout_guided)
                    params = {
                        "limit": scout_limit,
                        "languages": scout_languages,
                        "guided": scout_guided,
                    }
                    if body.get("run_id"):
                        params["run_id"] = str(body["run_id"])
                    reply = dict(estimate)
                    if scout_guided:
                        reply["confirm_token"] = self.panel.mint_token("scout", params)
                    self._send_json(200, reply)
                else:
                    self._send_json(404, {"error": "unknown estimate"})
        except FileNotFoundError as exc:
            self._send_json(404, {"error": str(exc)[:300]})
        except (ValueError, OSError, WorkspaceError) as exc:
            self._send_json(400, {"error": str(exc)[:300]})

    def _handle_action(self, kind: str) -> None:
        body = self._read_json_body()
        if body is None:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        if kind in _PAID_ACTIONS:
            params = {k: body[k] for k in body if k != "confirm_token"}
            if params.get("run_id") and self._validated_run_id(str(params["run_id"])) is None:
                self._send_json(400, {"error": "invalid run id"})
                return
            token = str(body.get("confirm_token", ""))
            if not token or not self.panel.consume_token(kind, params, token):
                self._send_json(
                    403,
                    {"error": _TOKEN_ERROR},
                )
                return
        elif kind in ("scout", "recover", "reverify"):
            params = dict(body)
            if kind in ("recover", "reverify"):
                run_id = self._validated_run_id(str(body.get("run_id", "")))
                if run_id is None:
                    self._send_json(400, {"error": "invalid run id"})
                    return
                params["run_id"] = run_id
            if kind == "scout":
                try:
                    params["limit"] = int(body.get("limit", 30))
                    params["languages"] = str(body.get("languages", "python,node"))
                    params["guided"] = _parse_guided(body.get("guided"), True)
                    params.pop("confirm_token", None)
                    if body.get("run_id"):
                        params["run_id"] = str(body["run_id"])
                    if params["guided"]:
                        scout_token = str(body.get("confirm_token", ""))
                        if not scout_token or not self.panel.consume_token(
                            kind, params, scout_token
                        ):
                            self._send_json(
                                403,
                                {"error": _TOKEN_ERROR},
                            )
                            return
                except (TypeError, ValueError):
                    self._send_json(400, {"error": "invalid scout parameters"})
                    return
                if params["limit"] <= 0:
                    self._send_json(400, {"error": "limit must be positive"})
                    return
        else:
            self._send_json(404, {"error": "unknown action"})
            return
        job = self.panel.start_job(kind, params)
        if job is None:
            self._send_json(
                409,
                {"error": "worker busy", "current_job_id": self.panel.current_job_id},
            )
            return
        self._send_json(200, {"job_id": job.job_id, "kind": kind, "status": job.status})


__all__ = [
    "BUDGET_TOTAL_USD",
    "BIND_HOST",
    "DEFAULT_PORT",
    "TOKEN_TTL_S",
    "Job",
    "PanelServer",
    "check_request_local",
    "estimate_brief",
    "estimate_run",
    "estimate_scout",
    "estimate_solve",
    "estimate_triage",
    "live_status",
    "run_detail",
    "serve",
    "summarize_runs",
    "triage_detail",
]


def serve(port: int = DEFAULT_PORT, root: Path | str | None = None) -> int:
    """Serve the local control panel on 127.0.0.1 until interrupted."""
    base = Path(root) if root is not None else Path.cwd()
    try:
        server = PanelServer((BIND_HOST, port), base)
    except OSError as exc:
        print(f"issa serve: error: cannot bind {BIND_HOST}:{port}: {exc}")
        return 2
    actual_port = server.server_address[1]
    print(f"issa serve: panel at http://127.0.0.1:{actual_port}/ (localhost only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
