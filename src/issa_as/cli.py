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
    run_p.add_argument(
        "--retry-attempted",
        action="store_true",
        help="Re-attempt issues that already have a non-solved proof bundle (never solved ones).",
    )

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
    solve_p.add_argument(
        "--max-usd",
        type=float,
        default=None,
        help="Solver USD cap (default: 2.00 for escalated muse-spark-1.3/high, else 0.60).",
    )
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
    reverify_p.add_argument(
        "--suite-path",
        default="",
        help="Scope the regression suite to this directory (default: unit rule).",
    )
    reverify_p.add_argument(
        "--setup",
        action="store_true",
        help="Reinstall the workspace test dependencies before verifying.",
    )

    bundle_p = sub.add_parser(
        "verify-bundle",
        help="Rebuild a proof bundle from proof/<run_id>/ only (clean-room check).",
    )
    bundle_p.add_argument("run_id", help="Run id to rebuild from proof/<run_id>/.")
    bundle_p.add_argument(
        "--out",
        default="",
        help="Result directory (default workspaces/bundle-<run_id>/result/).",
    )
    bundle_p.add_argument(
        "--clone-url",
        default="",
        help="Override the clone source (tests use a local path).",
    )
    bundle_p.add_argument(
        "--suite-path",
        default="",
        help="Scope the regression suite to this directory (default: unit rule).",
    )
    bundle_p.add_argument(
        "--timeout",
        type=float,
        default=1200.0,
        help="Suite-run timeout in seconds (default 1200; F2P runs keep 120).",
    )

    recover_p = sub.add_parser(
        "recover",
        help="Rebuild a failed run's bundle from its muse-runs log without calling Muse.",
    )
    recover_p.add_argument("run_id", help="Failed run id to recover.")

    pub_p = sub.add_parser("publish", help="Publish a proof bundle (dry-run only).")
    pub_p.add_argument("--bundle", default="", help="Proof bundle path.")
    pub_p.add_argument("--approval-id", default="", help="Human approval id.")

    disc_p = sub.add_parser(
        "disclosure",
        help="Build a private disclosure draft for an existing run (dry-run only).",
    )
    disc_p.add_argument("run_id", help="Run id to build proof/<run_id>/disclosure.md for.")

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

    serve_p = sub.add_parser(
        "serve",
        help="Serve the local operator panel on 127.0.0.1 (offline reads, guarded actions).",
    )
    serve_p.add_argument("--port", type=int, default=8780, help="Local port (default 8780).")

    eval_p = sub.add_parser(
        "eval",
        help="Run deterministic evaluation harnesses (offline, no model calls).",
    )
    eval_sub = eval_p.add_subparsers(dest="eval_command", required=True)
    eval_verifier_p = eval_sub.add_parser(
        "verifier",
        help="Attack the verifier with hostile patch shapes (synthetic or bundle).",
    )
    eval_verifier_p.add_argument(
        "--bundle",
        default="",
        help="Proof bundle run id to attack (default: synthetic project).",
    )
    eval_verifier_p.add_argument(
        "--out",
        default="",
        help="Report path (default evals/results/verifier-<target>.json).",
    )
    eval_verifier_p.add_argument(
        "--clone-url",
        default="",
        help="Clone source for --bundle (tests use a local path).",
    )
    eval_verifier_p.add_argument(
        "--keep",
        action="store_true",
        help="Keep the scratch workspace under workspaces/ (default: removed).",
    )
    eval_runs_p = eval_sub.add_parser(
        "runs",
        help="Metrics from committed proof/, traces/ and triage/.",
    )
    eval_runs_p.add_argument(
        "--out",
        default="",
        help="Report path (default evals/results/runs.json).",
    )
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
                "security_policy": candidate.security_policy.model_dump(),
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
                retry_attempted=args.retry_attempted,
            )
        except (WorkspaceError, ValueError) as exc:
            print(f"issa run: error: {exc}")
            return 2
        return 0 if outcome.verdict in ("VERIFIED", "DRY_RUN") else 1
    if args.command == "replay":
        return run_replay(args.run_id)
    if args.command == "solve":
        from issa_as import pipeline
        from issa_as import select as select_mod
        from issa_as.budget import IssueCaps
        from issa_as.solver import default_max_steps
        from issa_as.workspace import WorkspaceError

        if args.max_usd is not None and args.max_usd < 0:
            print("issa solve: error: --max-usd must be >= 0.")
            return 2
        solve_max_usd = (
            args.max_usd
            if args.max_usd is not None
            else select_mod.recommended_max_usd(args.model, args.effort)
        )
        solve_steps = args.max_steps or default_max_steps(args.model, args.effort)
        try:
            outcome = pipeline.solve_issue(
                args.issue_url,
                model=args.model,
                effort=args.effort,
                max_steps=args.max_steps,
                run_id=args.run_id or None,
                caps=IssueCaps(max_steps=solve_steps, max_usd=solve_max_usd),
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
            result = pipeline.reverify_run(
                args.run_id, setup=args.setup, suite_path=args.suite_path or None
            )
        except (WorkspaceError, ValueError, OSError) as exc:
            print(f"issa reverify: error: {exc}")
            return 2
        print(f"issa reverify: {result.verdict} (run_id={args.run_id})")
        return 0 if result.verdict == "VERIFIED" else 1
    if args.command == "verify-bundle":
        from issa_as import bundle as bundle_mod

        try:
            outcome = bundle_mod.verify_bundle(
                args.run_id,
                out_dir=args.out or None,
                clone_url=args.clone_url or None,
                suite_path=args.suite_path or None,
                timeout_s=args.timeout,
            )
        except Exception as exc:  # noqa: BLE001 — never a traceback-only exit
            print(f"issa verify-bundle: error: {type(exc).__name__}: {exc}")
            return 2
        print(outcome.summary)
        return 0 if outcome.verdict == "VERIFIED" else 1
    if args.command == "recover":
        from issa_as import pipeline
        from issa_as import solver as solver_mod
        from issa_as.workspace import WorkspaceError

        try:
            outcome = pipeline.recover_run(args.run_id)
        except (WorkspaceError, ValueError, OSError, solver_mod.SolverError) as exc:
            print(f"issa recover: error: {exc}")
            return 2
        print(f"issa recover: {outcome.verdict} {outcome.bundle_dir}/ (run_id={args.run_id})")
        return 0
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
    if args.command == "serve":
        from issa_as import serve as serve_mod

        return serve_mod.serve(port=args.port)
    if args.command == "eval":
        from issa_as import evals as evals_mod

        if args.eval_command == "verifier":
            try:
                report = evals_mod.run_verifier_eval(
                    bundle_run_id=args.bundle or None,
                    out=args.out or None,
                    clone_url=args.clone_url or None,
                    keep=args.keep,
                )
            except evals_mod.EvalError as exc:
                print(f"issa eval verifier: error: {exc}")
                return 2
            print(report["summary"])
            return 0 if all(r["ok"] for r in report["attacks"]) else 1
        if args.eval_command == "runs":
            metrics = evals_mod.run_runs_eval(out=args.out or None)
            print(
                f"issa eval runs: runs={metrics['n_runs']} "
                f"verified={metrics['n_verified']} "
                f"rate={metrics['verified_rate']:.1%} "
                f"total_usd={metrics['total_solver_usd']:.6f} "
                f"cost_per_fix={metrics['cost_per_verified_fix_usd']}"
            )
            return 0
        raise AssertionError(  # pragma: no cover - argparse enforces choices
            f"unknown eval command {args.eval_command}"
        )
    if args.command == "publish":
        from pathlib import Path as _BundlePath

        from issa_as import publish as publish_mod

        bundle_path = _BundlePath(args.bundle) if args.bundle else None
        if bundle_path is not None and bundle_path.is_dir():
            try:
                result = publish_mod.publish_bundle(
                    bundle_path, approval_id=args.approval_id or ""
                )
            except (OSError, ValueError) as exc:
                print(f"issa publish: error: {exc}")
                return 2
            if result["private_reporting"]:
                print(
                    "issa publish dry-run: private-reporting repo - refused public "
                    f"PR/issue/comment drafts; wrote {result['written']} "
                    f"approval_id={args.approval_id or '<none>'}; "
                    "no network writes performed."
                )
            else:
                print(
                    "issa publish dry-run: "
                    f"wrote {result['written']} "
                    f"approval_id={args.approval_id or '<none>'}; "
                    "no network writes performed."
                )
            return 0
        # Dry-run only: never touches the network, even with an approval id.
        print(
            "issa publish dry-run: "
            f"would publish bundle={args.bundle or '<none>'} "
            f"approval_id={args.approval_id or '<none>'}; no network writes performed."
        )
        return 0
    if args.command == "disclosure":
        from issa_as import publish as publish_mod

        try:
            out = publish_mod.build_disclosure_for_run(args.run_id)
        except (OSError, ValueError) as exc:
            print(f"issa disclosure: error: {exc}")
            return 2
        print(f"issa disclosure: wrote {out} (run_id={args.run_id})")
        return 0
    raise AssertionError(f"unknown command {args.command}")  # pragma: no cover
