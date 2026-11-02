"""Offline end-to-end tests for `issa solve` (Milestone 2 vertical slice).

No network, no model calls: the GitHub API is faked, the `muse exec`
session is a stub that writes a test commit + fix commit into a tmp repo,
and verification runs for real (local pytest in the tmp repo).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from issa_as import traces
from issa_as.budget import SPARK_OPEN_MODEL, cost_usd
from issa_as.cli import main
from issa_as.pipeline import solve_issue
from issa_as.providers import FakeProvider
from issa_as.solver import (
    EFFORT_MAX_STEPS,
    OUTPUT_SCHEMA,
    REASONING_EFFORTS,
    SolverError,
    SolverResult,
    build_command,
    build_prompt,
    emit_solver_events,
    run_solver,
)
from issa_as.workspace import (
    IssueInfo,
    VenvInfo,
    WorkspaceError,
    fetch_issue,
    parse_issue_url,
    repo_dir,
    resolve_under,
    setup_venv,
    validate_run_id,
)

ISSUE_URL = "https://github.com/o/r/issues/1"

CALC = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.email=t@t.t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _issue() -> IssueInfo:
    return IssueInfo(
        issue_url=ISSUE_URL,
        owner="o",
        repo="r",
        repo_full="o/r",
        number=1,
        title="add returns wrong result",
        body="```python\nadd(1, 2)  # -1, expected 3\n```",
        labels=["bug"],
        clone_url="https://github.com/o/r.git",
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
        run_id=run_id,
        issue_id=f"{issue.repo_full}#{issue.number}",
        test_file="test_calc.py",
        test_command=[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                      "test_calc.py"],
        test_commit=test_commit,
        fix_commit=fix_commit,
        test_parent=_git(repo, "rev-parse", f"{test_commit}^"),
        patch_text=_git(repo, "diff", f"{fix_commit}^", fix_commit, "--"),
        patch_summary="correct add to sum its arguments",
        root_cause="minus operator instead of plus",
        confidence=0.9,
        model="muse-spark-1.3",
        in_tokens=100,
        out_tokens=50,
        cost_usd=cost_usd("muse-spark-1.3", 100, 50),
        known_price=True,
    )
    emit_solver_events(run_id, result.issue_id, result, "medium", traces_dir)
    return result


def _solver_abstain(repo_path: Path | str, issue: IssueInfo, traces_dir: Path, run_id: str):
    _ = (repo_path, issue)
    result = SolverResult(
        run_id=run_id,
        issue_id="o/r#1",
        abstained=True,
        abstain_reason="cannot reproduce: no failing test found",
        model="muse-spark-1.3",
        in_tokens=10,
        out_tokens=5,
        cost_usd=cost_usd("muse-spark-1.3", 10, 5),
        known_price=True,
    )
    emit_solver_events(run_id, result.issue_id, result, "medium", traces_dir)
    return result


def test_solve_verified_end_to_end(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    workspaces = tmp_path / "workspaces"
    proof = tmp_path / "proof"

    def solver(repo_path, issue, *, run_id):
        return _solver_success(repo_path, issue, traces_dir, run_id)

    outcome = solve_issue(
        ISSUE_URL,
        model="muse-spark-1.3",
        effort="medium",
        run_id="solve-test-1",
        workspaces_root=workspaces,
        traces_dir=traces_dir,
        proof_root=proof,
        fetch_fn=lambda url: _issue(),
        clone_fn=_clone_stub,
        venv_fn=lambda repo: None,
        solver_fn=solver,
    )
    assert outcome.verdict == "VERIFIED"

    bundle = proof / "solve-test-1"
    assert (bundle / "verdict.json").is_file()
    assert (bundle / "fix.patch").is_file()
    assert (bundle / "test" / "test_calc.py").read_text(encoding="utf-8") == TEST
    assert "a + b" in (bundle / "fix.patch").read_text(encoding="utf-8")
    assert (bundle / "verify.log").is_file()
    verdict = json.loads((bundle / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "VERIFIED"
    assert verdict["test_commit"] == outcome.solver.test_commit
    assert verdict["fix_commit"] == outcome.solver.fix_commit
    cost = json.loads((bundle / "cost.json").read_text(encoding="utf-8"))
    assert cost["cost_usd"] == pytest.approx(cost_usd("muse-spark-1.3", 100, 50))
    assert cost["price_known"] is True

    events = traces.load("solve-test-1", traces_dir)
    types = [e.type for e in events]
    for expected in ("run_started", "issue_discovered", "fix_attempt", "model_call",
                     "verify_verdict", "proof_bundle"):
        assert expected in types, types
    model_call = next(e for e in events if e.type == "model_call")
    assert model_call.stage == "fixer"
    assert model_call.cost_usd == pytest.approx(cost_usd("muse-spark-1.3", 100, 50))
    assert traces.verify_chain("solve-test-1", traces_dir).ok


def test_solve_abstains_honestly(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"

    def solver(repo_path, issue, *, run_id):
        return _solver_abstain(repo_path, issue, traces_dir, run_id)

    outcome = solve_issue(
        ISSUE_URL,
        run_id="solve-test-ab",
        workspaces_root=tmp_path / "workspaces",
        traces_dir=traces_dir,
        proof_root=tmp_path / "proof",
        fetch_fn=lambda url: _issue(),
        clone_fn=_clone_stub,
        venv_fn=lambda repo: None,
        solver_fn=solver,
    )
    assert outcome.verdict == "ABSTAIN"
    bundle = tmp_path / "proof" / "solve-test-ab"
    note = (bundle / "abstention.md").read_text(encoding="utf-8")
    assert "cannot reproduce" in note

    events = traces.load("solve-test-ab", traces_dir)
    types = [e.type for e in events]
    assert "abstained" in types
    assert "proof_bundle" not in types
    assert "fix_attempt" not in types  # no patch, no fix attempt
    assert "model_call" in types
    assert traces.verify_chain("solve-test-ab", traces_dir).ok


def test_fetch_issue_uses_read_only_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={"title": "t", "body": "b ```code```",
                  "labels": [{"name": "bug"}, {"name": "x"}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    issue = fetch_issue(ISSUE_URL, client=client)
    assert seen["url"] == "https://api.github.com/repos/o/r/issues/1"
    assert seen["auth"] == ""  # no token, no auth header
    assert (issue.title, issue.repo_full, issue.number) == ("t", "o/r", 1)
    assert issue.labels == ["bug", "x"]
    assert issue.clone_url == "https://github.com/o/r.git"


def test_fetch_issue_sends_token_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"title": "t", "body": "", "labels": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetch_issue(ISSUE_URL, client=client)
    assert seen["auth"] == "Bearer fake-token"


def test_fetch_issue_rejects_bad_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(WorkspaceError, match="404"):
        fetch_issue(ISSUE_URL, client=client)


def test_bad_urls_and_paths_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        parse_issue_url("https://github.com/o/r/pull/1")
    with pytest.raises(WorkspaceError):
        validate_run_id("../evil")
    with pytest.raises(WorkspaceError):
        validate_run_id("")
    with pytest.raises(WorkspaceError):
        repo_dir("run", "..", tmp_path / "ws")
    with pytest.raises(WorkspaceError):
        resolve_under(tmp_path / "ws", "..", "evil")


def test_solver_command_matches_architecture(tmp_path: Path) -> None:
    repo = tmp_path / "workspaces" / "run1" / "repo"
    schema = tmp_path / "schema.json"
    prompt = build_prompt(_issue(), "high")
    cmd = build_command(repo, schema, prompt, 60, model="muse-spark-1.3", effort="high")
    assert cmd == [
        "muse", "exec",
        "--workspace", str(repo),
        "--model", "muse-spark-1.3",
        "--reasoning-effort", "high",
        "--approval-mode", "never",
        "--disable-web-tools",
        "--max-model-steps", "60",
        "--output-schema", str(schema),
        "--json",
        prompt,
    ]
    assert "--trust-workspace" not in cmd
    assert set(OUTPUT_SCHEMA["properties"]) == {
        "test_file", "test_command", "patch_summary",
        "root_cause", "confidence", "abstain_reason",
    }


def test_solver_effort_levels_reach_reasoning_effort(tmp_path: Path) -> None:
    assert set(REASONING_EFFORTS) == {"minimal", "low", "medium", "high", "xhigh"}
    repo = tmp_path / "workspaces" / "run1" / "repo"
    for effort in REASONING_EFFORTS:
        cmd = build_command(
            repo, tmp_path / "s.json", "p", EFFORT_MAX_STEPS[effort],
            model="m", effort=effort,
        )
        assert cmd[cmd.index("--reasoning-effort") + 1] == effort
    with pytest.raises(SolverError, match="unknown reasoning effort"):
        build_command(repo, tmp_path / "s.json", "p", 30, model="m", effort="ultra")
    with pytest.raises(SolverError, match="unknown effort"):
        run_solver(
            tmp_path / "workspaces" / "run1" / "repo", _issue(),
            run_id="bad-effort", effort="ultra",
            workspaces_root=tmp_path / "workspaces",
        )


def _muse_jsonl_runner(stdout: str, seen: list) -> object:
    def fake(cmd, **kwargs):
        args = list(cmd)
        if args and args[0] == "muse":
            seen.append(args)
            return subprocess.CompletedProcess(cmd, 0, stdout, "")
        if args[:2] == ["git", "rev-list"]:
            return subprocess.CompletedProcess(cmd, 0, "testc1\nfixc2\n", "")
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, "base0\n", "")
        if args[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(cmd, 0, "diff --git patch\n", "")
        raise AssertionError(f"unexpected command: {cmd}")

    return fake


def _usage_stdout(answer: dict, models: list[dict]) -> str:
    return (
        json.dumps({"payload_type": "run.output.delta", "payload": {"text": "working"}})
        + "\n"
        + json.dumps({"payload_type": "run.usage", "payload": {"models": models}})
        + "\n"
        + json.dumps(
            {"payload_type": "run.terminal.completed", "payload": {"text": json.dumps(answer)}}
        )
        + "\n"
    )


_SOLVER_ANSWER = {
    "test_file": "test_calc.py",
    "test_command": "pytest -q test_calc.py",
    "patch_summary": "correct add",
    "root_cause": "minus instead of plus",
    "confidence": 0.9,
}


def test_solver_prices_from_run_usage_event(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "run-u" / "repo"
    repo.mkdir(parents=True)
    stdout = _usage_stdout(
        _SOLVER_ANSWER,
        [{"model": "muse-spark-1.3-contributor", "input_tokens": 1000,
          "cached_tokens": 200, "output_tokens": 50, "calls": 1}],
    )
    seen: list = []
    result = run_solver(
        repo, _issue(), run_id="run-u", model="muse-spark-1.3", effort="medium",
        workspaces_root=workspaces, traces_dir=tmp_path / "traces",
        runner=_muse_jsonl_runner(stdout, seen),
    )
    # The usage event overrides the requested model for pricing.
    assert result.model == "muse-spark-1.3-contributor"
    assert (result.in_tokens, result.out_tokens, result.cached_tokens) == (1000, 50, 200)
    assert result.cost_usd == pytest.approx(cost_usd(
        "muse-spark-1.3-contributor", 1000, 50, 200))
    assert result.known_price is True
    assert result.test_commit == "testc1" and result.fix_commit == "fixc2"
    assert seen and "--trust-workspace" not in seen[0]
    assert seen[0][seen[0].index("--model") + 1] == "muse-spark-1.3"


def test_solver_falls_back_to_requested_model_without_usage(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "run-f" / "repo"
    repo.mkdir(parents=True)
    stdout = _usage_stdout(_SOLVER_ANSWER, [])
    result = run_solver(
        repo, _issue(), run_id="run-f", model="muse-spark-1.3", effort="low",
        workspaces_root=workspaces, traces_dir=tmp_path / "traces",
        runner=_muse_jsonl_runner(stdout, []),
    )
    assert result.model == "muse-spark-1.3"
    assert (result.in_tokens, result.out_tokens, result.cached_tokens) == (0, 0, 0)
    assert result.cost_usd == pytest.approx(cost_usd("muse-spark-1.3", 0, 0, 0))
    assert result.known_price is True


def test_unknown_spark_open_model_call_is_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SPARK_OPEN_PRICE_IN_PER_1M", raising=False)
    monkeypatch.delenv("SPARK_OPEN_PRICE_OUT_PER_1M", raising=False)
    provider = FakeProvider(model=SPARK_OPEN_MODEL, text="x", in_tokens=100, out_tokens=50)
    completion = provider.complete(
        stage="triage", prompt="x", run_id="r-null", traces_dir=tmp_path
    )
    assert completion.cost_usd is None
    assert completion.price_known is False
    (event,) = traces.load("r-null", tmp_path)
    assert event.type == "model_call"
    assert event.cost_usd is None  # never 0.0
    assert event.price_known is False


def test_solve_rejects_bad_url(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["solve", "not-a-url"]) == 2
    assert "error" in capsys.readouterr().out


def test_setup_venv_installs_use_sanitized_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "super-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "run1" / "repo"
    repo.mkdir(parents=True)
    (repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    seen: list = []

    def fake_run(cmd, **kwargs):
        seen.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    info = setup_venv(repo, workspaces_root=workspaces, uv_bin="uv", runner=fake_run)
    assert info.created is True
    assert len(seen) == 3  # venv + requirements.txt + editable install
    base = workspaces.resolve()
    for cmd, kwargs in seen:
        assert kwargs.get("timeout") == 600.0
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert "MODEL_API_KEY" not in env and "GITHUB_TOKEN" not in env
        assert str(Path(env["TMP"]).resolve()).startswith(str(base))
        assert not Path(env["TMP"]).exists()  # venv tmpdir is cleaned up
    assert "$ uv pip install -r requirements.txt" in info.log
    assert "$ uv pip install -e ." in info.log


def test_install_log_lands_in_proof_bundle(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    workspaces = tmp_path / "workspaces"
    proof = tmp_path / "proof"

    def solver(repo_path, issue, *, run_id):
        return _solver_success(repo_path, issue, traces_dir, run_id)

    outcome = solve_issue(
        ISSUE_URL,
        run_id="solve-install-log",
        workspaces_root=workspaces,
        traces_dir=traces_dir,
        proof_root=proof,
        fetch_fn=lambda url: _issue(),
        clone_fn=_clone_stub,
        venv_fn=lambda repo: VenvInfo(python=repo / ".venv", created=True,
                                      log="$ uv pip install -e .\nok\n"),
        solver_fn=solver,
    )
    assert outcome.verdict == "VERIFIED"
    install_log = proof / "solve-install-log" / "install.log"
    assert install_log.is_file()
    assert "$ uv pip install -e ." in install_log.read_text(encoding="utf-8")
