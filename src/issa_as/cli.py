"""ISSA AS command line: `issa scout | solve | reverify | verify | publish`."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issa",
        description="Issue Scouting, Solving and Attestation, Agentic Solver.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser(
        "run", help="Run the autonomous loop: scout, triage, select, solve, verify."
    )
    run_p.add_argument(
        "--languages",
        default="python",
        help="Ecosystems to include, comma-separated (default: python).",
    )
    run_p.add_argument("--limit", type=int, default=30, help="Max candidates (default 30).")
    run_p.add_argument(
        "--max-usd",
        type=float,
        default=0.50,
        help="Run-level USD cap, checked before every paid stage (default 0.50).",
    )
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Scout only, then print the planned stages with estimated tokens/USD.",
    )
    run_p.add_argument("--run-id", default="", help="Run id for traces/<run_id>.jsonl.")

    solve_p = sub.add_parser("solve", help="Solve one GitHub issue end to end (Milestone 2).")
    solve_p.add_argument("issue_url", help="GitHub issue URL, e.g. https://github.com/o/r/issues/1.")
    solve_p.add_argument(
        "--model",
        default="muse-spark-1.3-contributor",
        help="Solver model (cheap default; muse-spark-1.3 for the strong run).",
    )
    solve_p.add_argument(
        "--effort",
        default="medium",
        choices=["minimal", "low", "medium", "high", "xhigh"],
        help="Solver effort (passed through as the Muse --reasoning-effort).",
    )
    solve_p.add_argument("--max-steps", type=int, default=None, help="Max muse exec steps.")
    solve_p.add_argument("--run-id", default="", help="Run id (generated when empty).")

    replay_p = sub.add_parser("replay", help="Replay a committed trace without API keys.")
    replay_p.add_argument("run_id", help="Run id to replay from traces/<run_id>.jsonl.")

    verify_p = sub.add_parser("verify", help="Verify a trace hash chain (Keyless Trace Replay).")
    verify_p.add_argument("run_id", help="Run id to verify in traces/<run_id>.jsonl.")

    reverify_p = sub.add_parser(
        "reverify",
        help="Re-run verification for a stored run without calling Muse.",
    )
    reverify_p.add_argument("run_id", help="Run id to re-verify from proof/<run_id>/verdict.json.")

    pub_p = sub.add_parser("publish", help="Publish a proof bundle (dry-run only).")
    pub_p.add_argument("--bundle", default="", help="Proof bundle path.")
    pub_p.add_argument("--approval-id", default="", help="Human approval id.")

    scout_p = sub.add_parser("scout", help="Discover candidate issues (security first).")
    scout_p.add_argument("--limit", type=int, default=30, help="Max candidates (default 30).")
    scout_p.add_argument(
        "--languages",
        default="python,node",
        help=(
            "Ecosystems to include, comma-separated; each adds a "
            "language: qualifier to the GitHub queries (runner:none always kept)."
        ),
    )
    scout_p.add_argument("--run-id", default="", help="Run id (generated when empty).")

    triage_p = sub.add_parser(
        "triage", help="Score scout candidates and pick one to attempt (Milestone 3b)."
    )
    triage_p.add_argument("scout_run_id", help="Scout run id to triage (scout/<id>.json).")
    triage_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the exact request (model, token estimate, estimated USD) "
        "without calling the API.",
    )
    triage_p.add_argument("--run-id", default="", help="Triage run id (generated when empty).")
    return parser


def run_scout(
    limit: int = 30,
    languages: str = "python,node",
    run_id: str = "",
    client: object | None = None,
) -> tuple[str, list]:
    """Run discovery, write ``scout/<run_id>.json`` and trace ``issue_discovered`` events."""
    import json as _json
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    from issa_as import scout as _scout
    from issa_as import traces as _traces

    langs = tuple(p.strip() for p in languages.split(",") if p.strip())
    if not langs:
        langs = ("python", "node")
    rid = run_id or f"scout-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    live = _scout.GitHubClient() if client is None else client
    found, rate_limited = _scout.discover_limited(live, limit=limit, languages=langs)
    out_dir = _Path("scout")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": rid,
        "candidates": [c.model_dump() for c in found],
        "rate_limited": rate_limited,
    }
    (out_dir / f"{rid}.json").write_text(_json.dumps(payload, indent=2), encoding="utf-8")
    _traces.append(
        rid,
        {"type": "run_started", "stage": "run", "config_hash": f"scout:{limit}:{','.join(langs)}"},
        None,
    )
    for candidate in found:
        _traces.append(
            rid,
            {
                "type": "issue_discovered",
                "stage": "scout",
                "issue_id": f"{candidate.repo}#{candidate.number}",
                "issue_url": candidate.issue_url,
                "repo": candidate.repo,
                "labels": candidate.labels,
                "has_snippet": candidate.has_snippet,
                "security_class": candidate.security_class,
                "runner": candidate.runner,
                "signals": candidate.signals,
                "score": candidate.score,
            },
            None,
        )
    return rid, found


def run_triage(
    scout_run_id: str,
    dry_run: bool = False,
    run_id: str = "",
    provider: object | None = None,
) -> int:
    """Score scout candidates (triage + critic) and pick one to attempt.

    With ``dry_run`` prints the exact request (model, token estimate,
    estimated USD) without touching the network. Otherwise runs the full
    pipeline, writes ``triage/<run_id>.json`` and traces every stage.
    """
    import json as _json
    from pathlib import Path as _Path

    from issa_as import critic as _critic
    from issa_as import select as _select
    from issa_as import triage as _triage

    report_path = _Path("scout") / f"{scout_run_id}.json"
    if not report_path.is_file():
        print(f"issa triage: error: no such scout report: {report_path}")
        return 2
    try:
        doc = _json.loads(report_path.read_text(encoding="utf-8"))
    except ValueError:
        print(f"issa triage: error: corrupt scout report: {report_path}")
        return 2
    candidates = doc.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        print(f"issa triage: error: scout report has no candidates: {report_path}")
        return 2
    if dry_run:
        info = _triage.dry_run_info(candidates, provider)  # type: ignore[arg-type]
        est_usd = (
            f"{info['est_cost_usd']:.6f}" if info["est_cost_usd"] is not None else "unknown"
        )
        print(
            "issa triage dry-run: "
            f"model={info['model']} provider={info['provider']} "
            f"candidates={info['n_candidates']} "
            f"est_in_tokens={info['est_in_tokens']} "
            f"est_out_tokens={info['est_out_tokens']} est_usd={est_usd}"
        )
        return 0
    rid = run_id or f"triage-{scout_run_id}"
    scored = _triage.score(candidates, provider, run_id=rid)  # type: ignore[arg-type]
    verdicts = _critic.rescore(scored, provider, run_id=rid)  # type: ignore[arg-type]
    merged = _critic.merge(scored, verdicts)
    selection = _select.pick(merged, run_id=rid)
    out_dir = _Path("triage")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": rid,
        "scout_run_id": scout_run_id,
        "scored": [s.model_dump() for s in merged],
        "critic": [v.model_dump() for v in verdicts],
        "selection": selection.model_dump(),
    }
    (out_dir / f"{rid}.json").write_text(_json.dumps(payload, indent=2), encoding="utf-8")
    if selection.picked is not None:
        print(
            f"issa triage: picked {selection.picked.issue_url} "
            f"(effort={selection.picked.effort}) in triage/{rid}.json "
            f"(run_id={rid})"
        )
    else:
        print(f"issa triage: no pick (all skipped) in triage/{rid}.json (run_id={rid})")
    return 0


def _replay_line(event: object) -> str:
    """One human-readable timeline row for a trace event."""
    from datetime import datetime, timezone

    get = lambda name, default="": getattr(event, name, default)  # noqa: E731
    ts = get("ts", None)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        when = ts.astimezone(timezone.utc).strftime("%H:%M:%S")
    else:
        when = str(ts or "??:??:??")
    parts = [f"{when}", f"{get('stage', '?')}", f"{get('type', '?')}"]
    keys: list[str] = []
    issue = get("issue_id", "") or ""
    if issue:
        keys.append(f"issue={issue}")
    for name in (
        "solvability",
        "solvability_v2",
        "delta",
        "flag",
        "final_score",
        "model",
        "effort",
        "verdict",
        "f2p_pass",
        "regressions",
        "test_path",
        "test_commit",
        "failed_on_base",
        "cap",
        "spent",
        "limit",
        "provider",
        "request_id",
        "reasoning_tokens",
        "difficulty",
        "est_cost_usd",
    ):
        value = get(name, None)
        if value is None or value == "" or value is False or value == 0.0:
            continue
        if isinstance(value, float):
            keys.append(f"{name}={value:.4g}")
        else:
            keys.append(f"{name}={value}")
    cost = get("cost_usd", None)
    if cost is not None:
        keys.append(f"cost_usd={cost:.6f}" if isinstance(cost, float) else f"cost_usd={cost}")
    for name in ("reason", "rationale", "failure_reason", "model_rationale"):
        text = str(get(name, "") or "").strip().replace("\n", " ")
        if text:
            keys.append(f"{name}={text[:160]}")
            break
    excerpt = str(get("reasoning_excerpt", "") or "").replace("\n", " ").strip()
    if excerpt:
        keys.append(f"reasoning={excerpt[:300]}")
    return " | ".join(parts + keys)


def run_replay(run_id: str, traces_dir: object = None) -> int:
    """Keyless Trace Replay: verify the hash chain, then print a timeline.

    Refuses to replay a broken chain (non-zero exit, no timeline). Reads
    only the local trace file: no network, no API keys.
    """
    from pathlib import Path as _Path

    from issa_as import traces as trace_log

    tdir = _Path(traces_dir) if traces_dir is not None else None
    report = trace_log.verify_chain(run_id, tdir)
    if not report.ok:
        print(
            f"issa replay: refusing to replay run_id={run_id}: chain broken "
            f"(line={report.bad_line}, reason={report.reason})."
        )
        return 1
    events = trace_log.load(run_id, tdir)
    print(f"issa replay: run_id={run_id} events={len(events)} (chain ok)")
    for event in events:
        print(_replay_line(event))
    return 0


def main(argv: list[str] | None = None) -> int:
    from pathlib import Path as _EnvPath

    from issa_as.dotenv import load_dotenv

    load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env")
    args = build_parser().parse_args(argv)
    if args.command == "run":
        from issa_as import run as run_mod
        from issa_as.workspace import WorkspaceError

        if args.limit <= 0:
            print("issa run: error: --limit must be positive.")
            return 2
        if args.max_usd < 0:
            print("issa run: error: --max-usd must be >= 0.")
            return 2
        try:
            outcome = run_mod.run(
                languages=args.languages,
                limit=args.limit,
                max_usd=args.max_usd,
                dry_run=args.dry_run,
                run_id=args.run_id or "",
            )
        except (WorkspaceError, ValueError) as exc:
            print(f"issa run: error: {exc}")
            return 2
        return 0 if outcome.verdict in ("VERIFIED", "DRY_RUN") else 1
    if args.command == "replay":
        return run_replay(args.run_id)
    if args.command == "solve":
        from issa_as import pipeline
        from issa_as.workspace import WorkspaceError

        try:
            outcome = pipeline.solve_issue(
                args.issue_url,
                model=args.model,
                effort=args.effort,
                max_steps=args.max_steps,
                run_id=args.run_id or None,
            )
        except (WorkspaceError, ValueError) as exc:
            print(f"issa solve: error: {exc}")
            return 2
        print(f"issa solve: {outcome.verdict} {outcome.bundle_dir}/ (run_id={outcome.run_id})")
        return 0 if outcome.verdict == "VERIFIED" else 1
    if args.command == "verify":
        from issa_as import traces as trace_log

        report = trace_log.verify_chain(args.run_id)
        if report.ok:
            print(f"issa verify: chain ok for run_id={args.run_id}.")
            return 0
        print(
            f"issa verify: CHAIN BROKEN for run_id={args.run_id} "
            f"(line={report.bad_line}, reason={report.reason})."
        )
        return 1
    if args.command == "reverify":
        from issa_as import pipeline
        from issa_as.workspace import WorkspaceError

        try:
            result = pipeline.reverify_run(args.run_id)
        except (WorkspaceError, ValueError, OSError) as exc:
            print(f"issa reverify: error: {exc}")
            return 2
        print(f"issa reverify: {result.verdict} (run_id={args.run_id})")
        return 0 if result.verdict == "VERIFIED" else 1
    if args.command == "scout":
        if args.limit <= 0:
            print("issa scout: error: --limit must be positive.")
            return 2
        rid, found = run_scout(limit=args.limit, languages=args.languages, run_id=args.run_id)
        import json as _json
        from pathlib import Path as _ReportPath

        try:
            report = _json.loads(
                (_ReportPath("scout") / f"{rid}.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            report = {}
        note = " (rate limited — partial results)" if report.get("rate_limited") else ""
        print(f"issa scout: {len(found)} candidates in scout/{rid}.json (run_id={rid}){note}")
        return 0
    if args.command == "triage":
        return run_triage(
            args.scout_run_id, dry_run=args.dry_run, run_id=args.run_id
        )
    if args.command == "publish":
        # Dry-run only: never touches the network, even with an approval id.
        print(
            "issa publish dry-run: "
            f"would publish bundle={args.bundle or '<none>'} "
            f"approval_id={args.approval_id or '<none>'}; no network writes performed."
        )
        return 0
    raise AssertionError(f"unknown command {args.command}")  # pragma: no cover
