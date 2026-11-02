"""Smoke tests for the issa CLI stubs (Milestone 1)."""

import json

from issa_as import traces
from issa_as.cli import build_parser, main


def test_help_lists_subcommands(capsys) -> None:
    try:
        build_parser().parse_args(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "run" in out and "replay" in out and "publish" in out


def test_publish_is_dry_run_only(capsys) -> None:
    assert main(["publish", "--bundle", "b.json", "--approval-id", "a1"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "no network writes" in out


def test_run_and_replay_stubs(capsys) -> None:
    assert main(["run", "--issue-url", "https://example.invalid/i/1"]) == 0
    assert "not implemented" in capsys.readouterr().out
    assert main(["replay", "r1"]) == 0
    assert "not implemented" in capsys.readouterr().out


def test_verify_reports_ok_and_broken(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    traces.append("vc", {"type": "run_started", "stage": "run"}, None)
    traces.append("vc", {"type": "selected", "stage": "select"}, None)
    assert main(["verify", "vc"]) == 0
    assert "ok" in capsys.readouterr().out

    path = tmp_path / "traces" / "vc.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["stage"] = "tampered"
    lines[1] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main(["verify", "vc"]) == 1
    assert "BROKEN" in capsys.readouterr().out


def test_help_lists_verify(capsys) -> None:
    try:
        build_parser().parse_args(["verify", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "verify" in capsys.readouterr().out
