"""ISSA AS command line: `issa scout | solve | reverify | verify | publish`."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issa",
        description="Issue Scouting, Solving and Attestation, Agentic Solver.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run the pipeline on one issue (Milestone 2).")
    run_p.add_argument("--issue-url", default="", help="GitHub issue URL to attempt.")
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
        help="Ecosystems to include, comma-separated (runner:none always kept).",
    )
    scout_p.add_argument("--run-id", default="", help="Run id (generated when empty).")
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
    found = _scout.discover(live, limit=limit, languages=langs)
    out_dir = _Path("scout")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": rid, "candidates": [c.model_dump() for c in found]}
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        print("issa run: not implemented in Milestone 1 (see Milestone 2 vertical slice).")
        return 0
    if args.command == "replay":
        print(f"issa replay: not implemented in Milestone 1 (run_id={args.run_id}).")
        return 0
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
        print(f"issa scout: {len(found)} candidates in scout/{rid}.json (run_id={rid})")
        return 0
    if args.command == "publish":
        # Dry-run only: never touches the network, even with an approval id.
        print(
            "issa publish dry-run: "
            f"would publish bundle={args.bundle or '<none>'} "
            f"approval_id={args.approval_id or '<none>'}; no network writes performed."
        )
        return 0
    raise AssertionError(f"unknown command {args.command}")  # pragma: no cover
