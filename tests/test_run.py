"""Offline tests for `issa run`, `issa replay` and the `repro_result` event.

No network, no model calls: GitHub is faked, triage/critic use a
stage-dispatched stub provider, and the `muse exec` session is a stub that
writes a test commit + fix commit into a tmp repo.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from issa_as import traces
from issa_as.budget import cost_usd
from issa_as.cli import main, run_replay
from issa_as.pipeline import solve_issue
from issa_as.providers import Completion
from issa_as.run import run as autonomous_run
from issa_as.solver import SolverResult, emit_solver_events
from issa_as.workspace import IssueInfo

NOW = datetime.now(timezone.utc)
ISSUE_URL = "https://github.com/o/r/issues/1"

CALC = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def make_issue(number: int = 1) -> dict[str, Any]:
    return {
        "html_url": f"https://github.com/o/r/issues/{number}",
        "repository_full_name": "o/r",
        "number": number,
        "title": "Bug: crash on empty input",
        "body": "Steps to reproduce:\n1. Run it\n\n```python\nraise ValueError()\n```",
        "labels": [{"name": "bug"}],
        "user": {"login": "human"},
        "comments": 3,
        "created_at": (NOW - timedelta(days=5)).isoformat(),
    }


class FakeGitHub:
    """Minimal ScoutClient: one python repo with a test suite."""

    def __init__(self, issues: list[dict[str, Any]] | None = None) -> None:
        self.issues = issues if issues is not None else [make_issue()]
        self.queries: list[str] = []

    def search_issues(self, query: str) -> list[dict[str, Any]]:
        self.queries.append(query)
        return list(self.issues)

    def get_repo(self, full_name: str) -> dict[str, Any]:
        assert full_name == "o/r"
        return {
            "archived": False,
            "fork": False,
            "stargazers_count": 120,
            "size": 2048,
            "pushed_at": (NOW - timedelta(days=3)).isoformat(),
        }

    def get_text(self, full_name: str, path: str) -> str | None:
        if path == "pyproject.toml":
            return "[project]\nname = 'r'\n"
        return None

    def list_dir(self, full_name: str, path: str) -> list[str]:
        if path == "tests":
            return ["test_calc.py"]
        if path == ".github/workflows":
            return ["ci.yml"]
        return []

    def linked_open_pr(self, full_name: str, number: int) -> bool:
        return False


class StageStub:
    """Stage-dispatched stub provider with priced model names."""

    name = "stub"
    model = "muse-spark-1.3-contributor"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, stage, prompt, effort="low", run_id=None, **kwargs):
        self.calls.append({"stage": stage, "prompt": prompt, "effort": effort})
        if stage == "triage":
            text = json.dumps({"issues": [{
                "issue_url": ISSUE_URL,
                "solvability": 0.8, "difficulty": "low",
                "is_real_bug": True, "security_relevant": False,
                "rationale": "Clear repro with snippet; small repo.",
            }]})
        else:
            text = json.dumps({"issues": [{
                "issue_url": ISSUE_URL,
                "solvability_v2": 0.75, "rationale_v2": "Agreed, repro present.",
            }]})
        return Completion(
            text=text, model=self.model, provider=self.name,
            in_tokens=100, out_tokens=50,
            cost_usd=cost_usd(self.model, 100, 50), price_known=True,
        )


class ExplodingProvider(StageStub):
    def complete(self, **kwargs):
        raise AssertionError("paid provider must not be called")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.email=t@t.t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=repo, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _issue() -> IssueInfo:
    return IssueInfo(
        issue_url=ISSUE_URL, owner="o", repo="r", repo_full="o/r", number=1,
        title="add returns wrong result",
        body="```python\nadd(1, 2)  # -1, expected 3\n```",
        labels=["bug"], clone_url="https://github.com/o/r.git",
    )


def _clone_stub(clone_url: str, dest: Path) -> Path:
    assert clone_url == "https://github.com/o/r.git"
    dest.mkdir(parents=True)
    (dest / "calc.py").write_text(CALC, encoding="utf-8")
    _git(dest, "init", "-q")
    _git(dest, "add", ".")
    _git(dest, "commit", "-qm", "base")
    return dest


def _solver_success(repo_path: Path | str, issue: IssueInfo, traces_dir: Path, run_id: str):
    repo = Path(repo_path)
    (repo / "test_calc.py").write_text(TEST, encoding="utf-8")
    _git(repo, "add", "test_calc.py")
    _git(repo, "commit", "-qm", "test: add regression test for add")
    test_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "calc.py").write_text(GOOD, encoding="utf-8")
    _git(repo, "add", "calc.py")
    _git(repo, "commit", "-qm", "fix: correct add")
    fix_commit = _git(repo, "rev-parse", "HEAD")
    result = SolverResult(
        run_id=run_id, issue_id=f"{issue.repo_full}#{issue.number}",
        test_file="test_calc.py",
        test_command=[sys.executable, "-m", "pytest", "-q",
                      "-p", "no:cacheprovider", "test_calc.py"],
        test_commit=test_commit, fix_commit=fix_commit,
        test_parent=_git(repo, "rev-parse", f"{test_commit}^"),
        patch_text=_git(repo, "diff", f"{fix_commit}^", fix_commit, "--"),
        patch_summary="correct add to sum its arguments",
        root_cause="minus operator instead of plus",
        confidence=0.9, model="muse-spark-1.3",
        in_tokens=100, out_tokens=50,
        cost_usd=cost_usd("muse-spark-1.3", 100, 50), known_price=True,
    )
    emit_solver_events(run_id, result.issue_id, result, "medium", traces_dir)
    return result


def _seams(traces_dir: Path):
    def solver(repo_path, issue, *, run_id):
        return _solver_success(repo_path, issue, traces_dir, run_id)

    return {
        "fetch_fn": lambda url: _issue(),
        "clone_fn": _clone_stub,
        "venv_fn": lambda repo: None,
        "solver_fn": solver,
    }


def _run_dirs(tmp_path: Path) -> dict[str, Path]:
    return {
        "workspaces_root": tmp_path / "workspaces",
        "traces_dir": tmp_path / "traces",
        "proof_root": tmp_path / "proof",
        "scout_dir": tmp_path / "scout",
    }


@pytest.mark.slow
def test_run_end_to_end_verified(tmp_path: Path) -> None:
    dirs = _run_dirs(tmp_path)
    provider = StageStub()
    outcome = autonomous_run(
        languages="python", limit=10, max_usd=0.50, run_id="run-e2e",
        client=FakeGitHub(), provider=provider, proof_dir=dirs["proof_root"],
        **dirs, **_seams(dirs["traces_dir"]),
    )
    assert outcome.verdict == "VERIFIED"
    assert outcome.selection is not None
    assert outcome.selection.picked is not None
    assert outcome.selection.picked.issue_url == ISSUE_URL
    # Solver runs on the selection's routed model/effort.
    assert outcome.solve is not None
    assert outcome.solve.solver is not None
    assert outcome.solve.solver.model == outcome.selection.picked.recommended_solver_model

    events = traces.load("run-e2e", dirs["traces_dir"])
    types = [e.type for e in events]
    for expected in (
        "run_started", "issue_discovered", "triage_scored", "critic_rescored",
        "selected", "fix_attempt", "model_call", "repro_result",
        "verify_verdict", "proof_bundle",
    ):
        assert expected in types, types
    assert traces.verify_chain("run-e2e", dirs["traces_dir"]).ok

    selected = next(e for e in events if e.type == "selected")
    # Why this issue and why this model, in human-readable form.
    assert "hybrid score" in selected.reason
    assert selected.model_rationale
    assert "Model:" in selected.reason
    assert selected.model == outcome.selection.picked.recommended_solver_model
    assert selected.effort == outcome.selection.picked.recommended_effort

    repro = next(e for e in events if e.type == "repro_result")
    assert repro.test_path == "test_calc.py"
    assert repro.test_commit == outcome.solve.solver.test_commit
    assert repro.failed_on_base is True
    assert repro.failure_reason


def test_run_cap_blocked_before_first_paid_call(tmp_path: Path) -> None:
    dirs = _run_dirs(tmp_path)
    provider = StageStub()
    outcome = autonomous_run(
        languages="python", limit=10, max_usd=1e-9, run_id="run-capped",
        client=FakeGitHub(), provider=provider, proof_dir=dirs["proof_root"],
        **dirs, **_seams(dirs["traces_dir"]),
    )
    assert outcome.verdict == "ABSTAIN"
    assert provider.calls == []  # zero paid calls
    events = traces.load("run-capped", dirs["traces_dir"])
    types = [e.type for e in events]
    assert "cap_blocked" in types
    assert "model_call" not in types
    assert "selected" not in types
    assert traces.verify_chain("run-capped", dirs["traces_dir"]).ok


def test_run_dry_run_makes_zero_paid_calls(tmp_path: Path, capsys) -> None:
    dirs = _run_dirs(tmp_path)
    provider = ExplodingProvider()
    outcome = autonomous_run(
        languages="python", limit=10, max_usd=0.50, dry_run=True,
        run_id="run-dry", client=FakeGitHub(), provider=provider,
        proof_dir=dirs["proof_root"], **dirs,
    )
    assert outcome.verdict == "DRY_RUN"
    out = capsys.readouterr().out
    assert "triage" in out and "est_in_tokens=" in out and "est_usd=" in out
    assert "critic" in out and "solve" in out
    events = traces.load("run-dry", dirs["traces_dir"])
    assert events  # scout trace is still written
    assert [e for e in events if e.type == "model_call"] == []


@pytest.mark.slow
def test_replay_prints_readable_timeline(tmp_path: Path, capsys) -> None:
    dirs = _run_dirs(tmp_path)
    outcome = autonomous_run(
        languages="python", limit=10, max_usd=0.50, run_id="run-replay",
        client=FakeGitHub(), provider=StageStub(), proof_dir=dirs["proof_root"],
        **dirs, **_seams(dirs["traces_dir"]),
    )
    assert outcome.verdict == "VERIFIED"
    assert run_replay("run-replay", dirs["traces_dir"]) == 0
    out = capsys.readouterr().out
    assert "chain ok" in out
    for token in ("triage_scored", "critic_rescored", "selected",
                  "repro_result", "verify_verdict", "VERIFIED"):
        assert token in out, token
    # Reasoning excerpts are capped at 300 chars per timeline row.
    for line in out.splitlines():
        if "reasoning=" in line:
            assert len(line.split("reasoning=", 1)[1]) <= 300


def test_replay_refuses_tampered_chain(tmp_path: Path, capsys) -> None:
    dirs = _run_dirs(tmp_path)
    autonomous_run(
        languages="python", limit=10, max_usd=0.50, run_id="run-tamper",
        client=FakeGitHub(), provider=StageStub(), proof_dir=dirs["proof_root"],
        **dirs, **_seams(dirs["traces_dir"]),
    )
    path = dirs["traces_dir"] / "run-tamper.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 3
    tampered = json.loads(lines[2])
    tampered["stage"] = "tampered"
    lines[2] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    capsys.readouterr()
    assert run_replay("run-tamper", dirs["traces_dir"]) == 1
    out = capsys.readouterr().out
    assert "refusing to replay" in out
    assert "triage_scored" not in out  # no timeline after refusal


def test_replay_via_cli_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    traces.append("rr1", {"type": "run_started", "stage": "run"}, None)
    assert main(["replay", "rr1"]) == 0
    assert "chain ok" in capsys.readouterr().out
    assert main(["replay", "missing-run"]) == 1
    assert "refusing to replay" in capsys.readouterr().out


def test_repro_result_emitted_by_solve(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    outcome = solve_issue(
        ISSUE_URL, run_id="solve-repro", traces_dir=traces_dir,
        workspaces_root=tmp_path / "workspaces", proof_root=tmp_path / "proof",
        **_seams(traces_dir),
    )
    assert outcome.verdict == "VERIFIED"
    events = traces.load("solve-repro", traces_dir)
    (repro,) = [e for e in events if e.type == "repro_result"]
    assert repro.test_path == "test_calc.py"
    assert repro.test_commit == outcome.solver.test_commit
    assert repro.failed_on_base is True
    assert "fail" in repro.failure_reason.lower()


def _write_proof_verdict(proof_root: Path, name: str, url: str, verdict: str) -> None:
    bundle = proof_root / name
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "verdict.json").write_text(
        json.dumps({"run_id": name, "issue_url": url, "verdict": verdict}),
        encoding="utf-8")


def test_run_skips_attempted_issue_unless_retry(tmp_path: Path) -> None:
    dirs = _run_dirs(tmp_path)
    _write_proof_verdict(dirs["proof_root"], "old-run", ISSUE_URL, "ERROR")
    outcome = autonomous_run(
        languages="python", limit=10, max_usd=0.50, run_id="run-skip",
        client=FakeGitHub(), provider=StageStub(), proof_dir=dirs["proof_root"],
        **dirs, **_seams(dirs["traces_dir"]),
    )
    assert outcome.verdict == "ABSTAIN"
    assert outcome.selection is not None and outcome.selection.picked is None
    reasons = [s.reason for s in outcome.selection.skipped]
    assert any("already attempted (ERROR, run old-run)" in r for r in reasons)

    outcome = autonomous_run(
        languages="python", limit=10, max_usd=0.50, run_id="run-retry",
        client=FakeGitHub(), provider=StageStub(), proof_dir=dirs["proof_root"],
        retry_attempted=True, **dirs, **_seams(dirs["traces_dir"]),
    )
    assert outcome.verdict == "VERIFIED"


def test_run_never_retries_solved_issue(tmp_path: Path) -> None:
    dirs = _run_dirs(tmp_path)
    _write_proof_verdict(dirs["proof_root"], "old-ok", ISSUE_URL, "VERIFIED")
    outcome = autonomous_run(
        languages="python", limit=10, max_usd=0.50, run_id="run-solved",
        client=FakeGitHub(), provider=StageStub(), proof_dir=dirs["proof_root"],
        retry_attempted=True, **dirs, **_seams(dirs["traces_dir"]),
    )
    assert outcome.verdict == "ABSTAIN"
    assert outcome.selection is not None and outcome.selection.picked is None
    reasons = [s.reason for s in outcome.selection.skipped]
    assert any("already solved" in r for r in reasons)
