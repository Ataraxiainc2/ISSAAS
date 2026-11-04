"""Autonomous loop for ``issa run`` (Milestone 3c).

One run id, one hash-chained trace across every stage:

scout → triage → critic → select → solve → verify → proof bundle / abstention.

The solve stage reuses :func:`pipeline.solve_issue` with the selection's
``recommended_solver_model`` / ``recommended_effort``. A run-level USD cap
(``--max-usd``) is checked before every paid stage through
:class:`CappedProvider` (triage/critic) and an explicit gate (solver); a
breach emits ``cap_blocked`` and ends the run in abstention. ``--dry-run``
runs only the free scout stage, then prints the planned paid stages with
estimated tokens/USD without calling any provider. No GitHub writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from issa_as import briefs as briefs_mod
from issa_as import critic as critic_mod
from issa_as import pipeline as pipe_mod
from issa_as import scout as scout_mod
from issa_as import select as select_mod
from issa_as import traces as trace_log
from issa_as import triage as triage_mod
from issa_as import workspace as ws_mod
from issa_as.budget import (
    CapBlockedError,
    IssueCaps,
    Ledger,
    TokenUsage,
    cost_usd,
)
from issa_as.providers import ChatProvider, max_tokens_for_batch
from issa_as.solver import SolverResult, default_max_steps
from issa_as.workspace import IssueInfo

DEFAULT_LANGUAGES = ("python",)
DEFAULT_LIMIT = 30
DEFAULT_MAX_USD = 0.50


@dataclass
class RunOutcome:
    """Outcome of one autonomous ``issa run`` loop."""

    run_id: str
    verdict: str
    reason: str = ""
    selection: select_mod.Selection | None = None
    solve: pipe_mod.SolveOutcome | None = None


def make_run_id() -> str:
    """Filesystem-safe run id for one autonomous run."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-run"


def _config_hash(languages: tuple[str, ...], limit: int, max_usd: float) -> str:
    import hashlib

    raw = f"run|{','.join(languages)}|{limit}|{max_usd}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _gate_usd(
    ledger: Ledger,
    est_cost: float | None,
    max_usd: float,
    run_id: str,
    stage: str,
    issue_id: str | None,
    traces_dir: Path | None,
) -> None:
    """Block a paid stage that would exceed the run USD cap.

    Emits ``cap_blocked`` (when ``run_id`` is given) and raises
    :class:`CapBlockedError` before anything is charged. Unknown prices
    (``None``) count as 0.0 for cap purposes, matching :mod:`budget`.
    """
    est = est_cost if est_cost is not None else 0.0
    if ledger.usd_spent + est > max_usd:
        if run_id:
            trace_log.append(
                run_id,
                trace_log.CapBlocked(
                    run_id=run_id,
                    stage=stage,
                    issue_id=issue_id,
                    cap="usd",
                    spent=ledger.usd_spent,
                    limit=max_usd,
                ),
                traces_dir,
            )
        raise CapBlockedError("usd", ledger.usd_spent, max_usd)


class CappedProvider:
    """A :class:`ChatProvider` wrapper enforcing one run-level USD cap.

    Pre-checks an estimated cost before delegating to the inner provider
    (emitting ``cap_blocked`` and raising instead of calling), then records
    the actual completion cost on the shared ledger. The ledger/caps passed
    into :meth:`complete` by callers are ignored: the run ledger is the
    single source of truth, so inner providers must not double-charge.
    """

    def __init__(self, inner: ChatProvider, *, ledger: Ledger, max_usd: float) -> None:
        self.inner = inner
        self.ledger = ledger
        self.max_usd = max_usd

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    def complete(
        self,
        *,
        stage: str,
        prompt: str,
        effort: str = "low",
        run_id: str | None = None,
        issue_id: str | None = None,
        ledger: Ledger | None = None,
        caps: IssueCaps | None = None,
        traces_dir: Path | None = None,
        max_tokens: int | None = None,
    ):
        _ = (ledger, caps)
        est_in = max(len(prompt) // 4, 1)
        est_out = max_tokens if max_tokens is not None else 512
        _gate_usd(
            self.ledger,
            cost_usd(self.inner.model, est_in, est_out),
            self.max_usd,
            run_id or "",
            stage,
            issue_id,
            traces_dir,
        )
        completion = self.inner.complete(
            stage=stage,
            prompt=prompt,
            effort=effort,
            run_id=run_id,
            issue_id=issue_id,
            traces_dir=traces_dir,
            max_tokens=max_tokens,
        )
        self.ledger.record(
            TokenUsage(
                model=completion.model,
                in_tokens=completion.in_tokens,
                out_tokens=completion.out_tokens,
                cached_tokens=completion.cached_tokens,
            ),
            completion.cost_usd,
        )
        return completion


def _parse_languages(languages: str) -> tuple[str, ...]:
    langs = tuple(p.strip() for p in languages.split(",") if p.strip())
    return langs or DEFAULT_LANGUAGES


def _plan_and_print(
    rid: str,
    candidates: list[dict[str, Any]],
    provider: ChatProvider,
    max_usd: float,
) -> None:
    """Print the planned paid stages with estimated tokens/USD (no calls)."""
    info = triage_mod.dry_run_info(candidates, provider)
    brief_info = briefs_mod.dry_run_info(candidates, provider)
    brief_usd = (
        f"{brief_info['est_cost_usd']:.6f}"
        if brief_info["est_cost_usd"] is not None
        else "unknown"
    )
    est_usd = (
        f"{info['est_cost_usd']:.6f}" if info["est_cost_usd"] is not None else "unknown"
    )
    top_k = min(len(candidates), critic_mod.CRITIC_TOP_K)
    critic_out = max_tokens_for_batch(top_k) if top_k else 0
    critic_in = info["est_in_tokens"]
    critic_usd = cost_usd(provider.model, critic_in, critic_out)
    critic_usd_s = f"{critic_usd:.6f}" if critic_usd is not None else "unknown"
    print(f"issa run dry-run: run_id={rid} candidates={len(candidates)}")
    print(
        f"  brief: model={brief_info['model']} provider={brief_info['provider']} "
        f"est_in_tokens={brief_info['est_in_tokens']} "
        f"est_out_tokens={brief_info['est_out_tokens']} est_usd={brief_usd}"
    )
    print(f"  scout: {len(candidates)} candidates (no model cost)")
    print(
        f"  triage: model={info['model']} provider={info['provider']} "
        f"est_in_tokens={info['est_in_tokens']} "
        f"est_out_tokens={info['est_out_tokens']} est_usd={est_usd}"
    )
    print(
        f"  critic: top_k={top_k} model={provider.model} "
        f"est_in_tokens~{critic_in} est_out_tokens={critic_out} "
        f"est_usd~{critic_usd_s}"
    )
    print("  select: deterministic (no model cost)")
    print("  solve: model/effort routed after selection (not run in dry-run)")
    print(f"  run cap: max_usd={max_usd}")


def _abstain_outcome(
    rid: str,
    reason: str,
    selection: select_mod.Selection | None,
    proof_root: Path | str,
    traces_dir: Path | None,
) -> RunOutcome:
    bundle_dir = Path(proof_root) / rid
    bundle_dir.mkdir(parents=True, exist_ok=True)
    pipe_mod._abstain(rid, f"run:{rid}", None, bundle_dir, traces_dir, reason)
    print(f"issa run: ABSTAIN {reason} (run_id={rid})")
    return RunOutcome(run_id=rid, verdict="ABSTAIN", reason=reason, selection=selection)


def run(
    *,
    languages: str = "python",
    limit: int = 30,
    max_usd: float = DEFAULT_MAX_USD,
    dry_run: bool = False,
    retry_attempted: bool = False,
    guided: bool = False,
    run_id: str = "",
    client: Any | None = None,
    provider: ChatProvider | None = None,
    workspaces_root: Path | str = ws_mod.DEFAULT_WORKSPACE_ROOT,
    traces_dir: Path | None = None,
    proof_root: Path | str = pipe_mod.DEFAULT_PROOF_ROOT,
    proof_dir: Path | str = select_mod.DEFAULT_PROOF_DIR,
    fetch_fn: Callable[[str], IssueInfo] | None = None,
    clone_fn: Callable[[str, Path], Path] | None = None,
    venv_fn: Callable[[Path], Any] | None = None,
    solver_fn: Callable[..., SolverResult] | None = None,
) -> RunOutcome:
    """Run the full autonomous loop under one run id and one trace.

    ``client`` is the scout GitHub client (fake in tests); ``provider`` is
    the triage/critic model provider (fake in tests; never called on
    ``dry_run``). ``fetch_fn``/``clone_fn``/``venv_fn``/``solver_fn`` are
    passed through to :func:`pipeline.solve_issue` for offline tests. With
    ``retry_attempted`` set, issues whose bundles never solved are tried again.
    """
    langs = _parse_languages(languages)
    if limit <= 0:
        raise ValueError(f"--limit must be positive, got {limit!r}")
    if max_usd < 0:
        raise ValueError(f"--max-usd must be >= 0, got {max_usd!r}")
    rid = run_id or make_run_id()
    ws_mod.validate_run_id(rid)
    ledger = Ledger(issue_id=rid)

    trace_log.append(
        rid,
        trace_log.RunStarted(
            run_id=rid,
            config_hash=_config_hash(langs, limit, max_usd),
            model_routes={},
            caps=IssueCaps(max_usd=max_usd).model_dump(),
        ),
        traces_dir,
    )

    live = client if client is not None else scout_mod.GitHubClient()
    if guided and not dry_run:
        outcome = scout_mod.discover_full(
            live, limit=limit, languages=langs, guided=True,
            provider=provider, run_id=rid, traces_dir=traces_dir,
        )
        live_outcome = (outcome.candidates, outcome.rate_limited)
    else:
        live_outcome = scout_mod.discover_limited(live, limit=limit, languages=langs)
    found, rate_limited = live_outcome

    for candidate in found:
        trace_log.append(
            rid,
            trace_log.IssueDiscovered(
                run_id=rid,
                issue_url=candidate.issue_url,
                repo=candidate.repo,
                labels=list(candidate.labels),
                has_snippet=candidate.has_snippet,
                security_policy=candidate.security_policy.model_dump(),
                matched_query=candidate.matched_query,
            ),
            traces_dir,
        )

    inner = provider if provider is not None else triage_mod.default_provider()

    if dry_run:
        _plan_and_print(rid, [c.model_dump() for c in found], inner, max_usd)
        return RunOutcome(run_id=rid, verdict="DRY_RUN", reason="dry-run plan only")

    capped = CappedProvider(inner, ledger=ledger, max_usd=max_usd)
    # Plain-language briefs right after scouting: a failed brief call is
    # recorded and never blocks the loop.
    briefs_mod.brief_candidates(
        [c.model_dump() for c in found],
        capped,
        scout_run_id=rid,
        run_id=rid,
        traces_dir=traces_dir,
        scout_dir=Path("scout"),
    )
    try:
        scored = triage_mod.score(
            [c.model_dump() for c in found], capped, run_id=rid, traces_dir=traces_dir
        )
        verdicts = critic_mod.rescore(scored, capped, run_id=rid, traces_dir=traces_dir)
    except CapBlockedError as exc:
        return _abstain_outcome(
            rid, f"run cap blocked ({exc.cap}) before solve triage", None,
            proof_root, traces_dir,
        )
    merged = critic_mod.merge(scored, verdicts)
    selection = select_mod.pick(
        merged, proof_dir=proof_dir, run_id=rid, traces_dir=traces_dir,
        retry_attempted=retry_attempted,
    )
    if selection.picked is None:
        detail = "; ".join(s.reason for s in selection.skipped[:3])
        reason = "no selectable issue (all skipped)" + (f": {detail}" if detail else "")
        if rate_limited:
            reason += " [scout rate-limited]"
        return _abstain_outcome(rid, reason, selection, proof_root, traces_dir)

    picked = selection.picked
    print(
        f"issa run: picked {picked.issue_url} "
        f"(model={picked.recommended_solver_model} "
        f"effort={picked.recommended_effort})"
    )
    try:
        _gate_usd(ledger, 0.0, max_usd, rid, "solver", picked.issue_url, traces_dir)
    except CapBlockedError as exc:
        return _abstain_outcome(
            rid, f"run cap blocked ({exc.cap}) before solver", selection,
            proof_root, traces_dir,
        )
    steps = default_max_steps(
        picked.recommended_solver_model, picked.recommended_effort
    )
    # Escalated picks (strong model + high effort) raise the solver
    # allowance to their recommended_max_usd (2.00): the 59-step/$1.23
    # ansible-runner#1548 fix does not fit a smaller cap. The run-level
    # --max-usd still gates triage/critic and the pre-solver check above.
    solver_max_usd = max(
        max_usd,
        select_mod.recommended_max_usd(
            picked.recommended_solver_model, picked.recommended_effort
        ),
    )
    try:
        outcome = pipe_mod.solve_issue(
            picked.issue_url,
            model=picked.recommended_solver_model,
            effort=picked.recommended_effort,
            max_steps=steps,
            run_id=rid,
            workspaces_root=workspaces_root,
            traces_dir=traces_dir,
            proof_root=proof_root,
            fetch_fn=fetch_fn,
            clone_fn=clone_fn,
            venv_fn=venv_fn,
            solver_fn=solver_fn,
            ledger=ledger,
            caps=IssueCaps(max_steps=steps, max_usd=solver_max_usd),
        )
    except CapBlockedError as exc:
        return _abstain_outcome(
            rid, f"run cap blocked ({exc.cap}) during solver", selection,
            proof_root, traces_dir,
        )
    print(f"issa run: {outcome.verdict} {outcome.bundle_dir}/ (run_id={rid})")
    return RunOutcome(
        run_id=rid,
        verdict=outcome.verdict,
        reason=f"solved via {picked.issue_url}",
        selection=selection,
        solve=outcome,
    )


__all__ = [
    "CappedProvider",
    "RunOutcome",
    "make_run_id",
    "run",
]
