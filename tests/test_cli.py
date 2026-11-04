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


def test_run_rejects_bad_limits(capsys) -> None:
    assert main(["run", "--limit", "0"]) == 2
    assert "error" in capsys.readouterr().out
    assert main(["run", "--max-usd", "-1"]) == 2
    assert "error" in capsys.readouterr().out


def test_replay_refuses_missing_run(capsys, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["replay", "r1"]) == 1
    assert "refusing to replay" in capsys.readouterr().out


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


def _write_scout_report(path, run_id: str = "s1") -> None:
    import json as _json

    report = {
        "run_id": run_id,
        "rate_limited": False,
        "candidates": [
            {
                "issue_url": "https://github.com/o/r/issues/1",
                "repo": "o/r",
                "number": 1,
                "title": "Crash on empty input",
                "labels": ["bug"],
                "security_class": None,
                "runner": "python",
                "stars": 120,
                "has_snippet": True,
                "signals": {"has_repro_steps": 1.0},
                "score": 0.7,
            }
        ],
    }
    scout_dir = path / "scout"
    scout_dir.mkdir(parents=True, exist_ok=True)
    (scout_dir / f"{run_id}.json").write_text(_json.dumps(report), encoding="utf-8")


def test_triage_dry_run_prints_request_without_network(
    tmp_path, monkeypatch, capsys
) -> None:
    import httpx

    from issa_as.cli import run_triage

    def no_client(*args, **kwargs):
        raise AssertionError("dry-run must not construct a network client")

    monkeypatch.setattr(httpx, "Client", no_client)
    monkeypatch.chdir(tmp_path)
    _write_scout_report(tmp_path)
    assert run_triage("s1", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "model=" in out and "est_in_tokens=" in out and "est_usd=" in out
    assert "candidates=1" in out


def test_triage_missing_report_is_an_error(tmp_path, monkeypatch, capsys) -> None:
    from issa_as.cli import run_triage

    monkeypatch.chdir(tmp_path)
    assert run_triage("nope", dry_run=True) == 2
    assert "no such scout report" in capsys.readouterr().out


def test_triage_full_run_picks_with_stub_provider(tmp_path, monkeypatch, capsys) -> None:
    import json as _json

    from issa_as.cli import run_triage
    from issa_as.providers import Completion

    class StageStub:
        name = "stub"
        model = "stub-model"

        def complete(self, *, stage, prompt, effort="low", run_id=None, **kwargs):
            if stage == "triage":
                text = _json.dumps({"issues": [{
                    "issue_url": "https://github.com/o/r/issues/1",
                    "solvability": 0.8, "difficulty": "low",
                    "is_real_bug": True, "security_relevant": False,
                    "rationale": "Clear repro.",
                }]})
            else:
                text = _json.dumps({"issues": [{
                    "issue_url": "https://github.com/o/r/issues/1",
                    "solvability_v2": 0.75, "rationale_v2": "Agreed.",
                }]})
            return Completion(text=text, model=self.model, provider=self.name)

    monkeypatch.chdir(tmp_path)
    _write_scout_report(tmp_path)
    assert run_triage("s1", provider=StageStub()) == 0
    out = capsys.readouterr().out
    assert "picked https://github.com/o/r/issues/1" in out
    assert "effort=low" in out
    saved = _json.loads((tmp_path / "triage" / "triage-s1.json").read_text())
    assert saved["scout_run_id"] == "s1"
    assert saved["selection"]["picked"]["issue_url"].endswith("/issues/1")
    assert saved["scored"][0]["critic_flag"] == "ok"


def test_run_retry_attempted_flag_defaults_off_and_parses() -> None:
    assert build_parser().parse_args(["run"]).retry_attempted is False
    args = build_parser().parse_args(["run", "--retry-attempted"])
    assert args.retry_attempted is True
