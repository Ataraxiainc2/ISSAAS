"""ISSA AS command line: `issa run | replay | publish`."""

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

    replay_p = sub.add_parser("replay", help="Replay a committed trace without API keys.")
    replay_p.add_argument("run_id", help="Run id to replay from traces/<run_id>.jsonl.")

    verify_p = sub.add_parser("verify", help="Verify a trace hash chain (Keyless Trace Replay).")
    verify_p.add_argument("run_id", help="Run id to verify in traces/<run_id>.jsonl.")

    pub_p = sub.add_parser("publish", help="Publish a proof bundle (dry-run only).")
    pub_p.add_argument("--bundle", default="", help="Proof bundle path.")
    pub_p.add_argument("--approval-id", default="", help="Human approval id.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        print("issa run: not implemented in Milestone 1 (see Milestone 2 vertical slice).")
        return 0
    if args.command == "replay":
        print(f"issa replay: not implemented in Milestone 1 (run_id={args.run_id}).")
        return 0
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
    if args.command == "publish":
        # Dry-run only: never touches the network, even with an approval id.
        print(
            "issa publish dry-run: "
            f"would publish bundle={args.bundle or '<none>'} "
            f"approval_id={args.approval_id or '<none>'}; no network writes performed."
        )
        return 0
    raise AssertionError(f"unknown command {args.command}")  # pragma: no cover
