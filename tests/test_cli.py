"""Smoke tests for the issa CLI stubs (Milestone 1)."""

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
