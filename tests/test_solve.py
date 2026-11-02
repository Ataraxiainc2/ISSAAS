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
    MUSE_BIN_ENV,
    OUTPUT_SCHEMA,
    REASONING_EFFORTS,
    SolverError,
    SolverResult,
    build_command,
    build_prompt,
    emit_solver_events,
    resolve_muse_bin,
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


@pytest.mark.slow
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
    prompt_file = tmp_path / "workspaces" / "run1" / "solver_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(build_prompt(_issue(), "high"), encoding="utf-8")
    cmd = build_command(
        repo, schema, prompt_file, 60, model="muse-spark-1.3", effort="high",
    )
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
        "--prompt-file", str(prompt_file),
    ]
    assert "--trust-workspace" not in cmd
    # The multi-line prompt travels via --prompt-file, never positionally.
    assert build_prompt(_issue(), "high") not in cmd
    assert set(OUTPUT_SCHEMA["properties"]) == {
        "test_file", "test_command", "patch_summary",
        "root_cause", "confidence", "abstain_reason",
    }


def test_output_schema_is_strict() -> None:
    def check(node: object, path: str) -> None:
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        if node.get("type") == "object" or props is not None:
            assert node.get("additionalProperties") is False, path
            assert set(node.get("required", [])) == set(props or {}), path
        for key, child in (props or {}).items():
            check(child, f"{path}.{key}")
        items = node.get("items")
        if isinstance(items, dict):
            check(items, f"{path}[]")
        elif isinstance(items, list):
            for i, child in enumerate(items):
                check(child, f"{path}[{i}]")
        for keyword in ("anyOf", "oneOf", "allOf", "prefixItems"):
            branches = node.get(keyword)
            if isinstance(branches, list):
                for i, child in enumerate(branches):
                    check(child, f"{path}.{keyword}[{i}]")

    check(OUTPUT_SCHEMA, "OUTPUT_SCHEMA")
    assert set(OUTPUT_SCHEMA["required"]) == {
        "test_file", "test_command", "patch_summary",
        "root_cause", "confidence", "abstain_reason",
    }


def test_solver_null_fields_parse_like_missing(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "run-null" / "repo"
    repo.mkdir(parents=True)
    answer = {
        "test_file": None,
        "test_command": None,
        "patch_summary": None,
        "root_cause": None,
        "confidence": None,
        "abstain_reason": "cannot reproduce",
    }
    stdout = _usage_stdout(answer, [])
    result = run_solver(
        repo, _issue(), run_id="run-null", effort="medium",
        workspaces_root=workspaces, traces_dir=tmp_path / "traces",
        muse_runs_root=tmp_path / "muse-runs",
        runner=_muse_jsonl_runner(stdout, []),
    )
    assert result.abstained is True
    assert result.abstain_reason == "cannot reproduce"
    assert result.test_file == ""
    assert result.patch_summary == ""
    assert result.root_cause == ""
    assert result.confidence == 0.0


def test_solver_effort_levels_reach_reasoning_effort(tmp_path: Path) -> None:
    assert set(REASONING_EFFORTS) == {"minimal", "low", "medium", "high", "xhigh"}
    repo = tmp_path / "workspaces" / "run1" / "repo"
    prompt_file = tmp_path / "p.md"
    for effort in REASONING_EFFORTS:
        cmd = build_command(
            repo, tmp_path / "s.json", prompt_file, EFFORT_MAX_STEPS[effort],
            model="m", effort=effort,
        )
        assert cmd[cmd.index("--reasoning-effort") + 1] == effort
    with pytest.raises(SolverError, match="unknown reasoning effort"):
        build_command(
            repo, tmp_path / "s.json", prompt_file, 30, model="m", effort="ultra",
        )
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
        muse_runs_root=tmp_path / "muse-runs",
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
        muse_runs_root=tmp_path / "muse-runs",
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


@pytest.mark.slow
def test_reverify_on_stored_run(tmp_path: Path) -> None:
    from issa_as.pipeline import reverify_run

    traces_dir = tmp_path / "traces"
    workspaces = tmp_path / "workspaces"
    proof = tmp_path / "proof"

    def solver(repo_path, issue, *, run_id):
        return _solver_success(repo_path, issue, traces_dir, run_id)

    outcome = solve_issue(
        ISSUE_URL,
        model="muse-spark-1.3",
        effort="medium",
        run_id="reverify-test-1",
        workspaces_root=workspaces,
        traces_dir=traces_dir,
        proof_root=proof,
        fetch_fn=lambda url: _issue(),
        clone_fn=_clone_stub,
        venv_fn=lambda repo: None,
        solver_fn=solver,
    )
    assert outcome.verdict == "VERIFIED"
    before = traces.load("reverify-test-1", traces_dir)
    assert sum(1 for e in before if e.type == "verify_verdict") == 1

    result = reverify_run(
        "reverify-test-1",
        workspaces_root=workspaces,
        proof_root=proof,
        traces_dir=traces_dir,
    )
    assert result.verdict == "VERIFIED"
    verdict = json.loads(
        (proof / "reverify-test-1" / "verdict.json").read_text(encoding="utf-8")
    )
    assert verdict["verdict"] == "VERIFIED"
    assert "baseline_failures" in verdict
    after = traces.load("reverify-test-1", traces_dir)
    assert sum(1 for e in after if e.type == "verify_verdict") == 2
    assert traces.verify_chain("reverify-test-1", traces_dir).ok


def test_reverify_rejects_unknown_run(tmp_path: Path) -> None:
    from issa_as.pipeline import reverify_run
    from issa_as.workspace import WorkspaceError

    with pytest.raises(WorkspaceError):
        reverify_run(
            "no-such-run",
            workspaces_root=tmp_path / "workspaces",
            proof_root=tmp_path / "proof",
            traces_dir=tmp_path / "traces",
        )


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


@pytest.mark.slow
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


def test_resolve_muse_bin_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MUSE_BIN_ENV, "/env/muse.cmd")
    assert resolve_muse_bin("/explicit/muse") == "/explicit/muse"


def test_resolve_muse_bin_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MUSE_BIN_ENV, "/env/muse.cmd")
    assert resolve_muse_bin() == "/env/muse.cmd"


def test_resolve_muse_bin_falls_back_to_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MUSE_BIN_ENV, raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: "C:/muse/muse.cmd" if name == "muse" else None
    )
    assert resolve_muse_bin() == "C:/muse/muse.cmd"


def test_resolve_muse_bin_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MUSE_BIN_ENV, raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(SolverError, match="muse binary not found"):
        resolve_muse_bin()


def test_run_solver_writes_prompt_file_and_run_files(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "run-p" / "repo"
    repo.mkdir(parents=True)
    runs = tmp_path / "muse-runs"
    stdout = _usage_stdout(
        _SOLVER_ANSWER,
        [{"model": "muse-spark-1.3", "input_tokens": 10,
          "output_tokens": 5, "calls": 1}],
    )
    seen: list = []
    run_solver(
        repo, _issue(), run_id="run-p", model="muse-spark-1.3", effort="medium",
        workspaces_root=workspaces, traces_dir=tmp_path / "traces",
        muse_runs_root=runs, runner=_muse_jsonl_runner(stdout, seen),
    )
    # Prompt lives in workspaces/<run_id>/ and travels via --prompt-file.
    prompt_file = workspaces / "run-p" / "solver_prompt.md"
    assert prompt_file.is_file()
    assert "add returns wrong result" in prompt_file.read_text(encoding="utf-8")
    assert seen and seen[0][seen[0].index("--prompt-file") + 1] == str(prompt_file)
    # Streamed JSONL log plus the console meta record.
    lines = (runs / "solver-run-p.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert all(json.loads(line).get("payload_type") for line in lines)
    meta = json.loads((runs / "solver-run-p.meta.json").read_text(encoding="utf-8"))
    assert meta["run"] == "solver-run-p"
    assert meta["prompt_file"].endswith("solver_prompt.md")
    assert meta["model"] == "muse-spark-1.3"
    assert meta["reasoning_effort"] == "medium"
    assert meta["max_model_steps"] == EFFORT_MAX_STEPS["medium"]
    assert meta["disable_web_tools"] is True
    assert "--prompt-file" in meta["command"]
    assert meta["started"] and meta["finished"]
    assert meta["exit_code"] == 0


def _boom_runner(exc: BaseException):
    def fake(cmd, **kwargs):
        args = list(cmd)
        if args and args[0] == "muse":
            raise exc
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, "base0\n", "")
        raise AssertionError(f"unexpected command: {cmd}")

    return fake


def test_run_solver_start_failure_raises(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "run-e" / "repo"
    repo.mkdir(parents=True)
    with pytest.raises(SolverError, match="muse exec failed to start"):
        run_solver(
            repo, _issue(), run_id="run-e",
            workspaces_root=workspaces, traces_dir=tmp_path / "traces",
            muse_runs_root=tmp_path / "muse-runs",
            runner=_boom_runner(OSError(2, "The system cannot find the file specified")),
        )


def test_run_solver_timeout_raises(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = workspaces / "run-t" / "repo"
    repo.mkdir(parents=True)
    with pytest.raises(SolverError, match="timed out"):
        run_solver(
            repo, _issue(), run_id="run-t", timeout_s=5.0,
            workspaces_root=workspaces, traces_dir=tmp_path / "traces",
            muse_runs_root=tmp_path / "muse-runs",
            runner=_boom_runner(subprocess.TimeoutExpired("muse", 5.0)),
        )


def test_pipeline_solver_crash_is_error_not_abstain(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"

    def boom(repo_path, issue, *, run_id):
        raise RuntimeError("muse exec failed to start: [WinError 2]")

    outcome = solve_issue(
        ISSUE_URL,
        run_id="solve-err",
        workspaces_root=tmp_path / "workspaces",
        traces_dir=traces_dir,
        proof_root=tmp_path / "proof",
        fetch_fn=lambda url: _issue(),
        clone_fn=_clone_stub,
        venv_fn=lambda repo: None,
        solver_fn=boom,
    )
    assert outcome.verdict == "ERROR"
    bundle = tmp_path / "proof" / "solve-err"
    assert "WinError 2" in (bundle / "error.md").read_text(encoding="utf-8")
    assert not (bundle / "abstention.md").exists()

    events = traces.load("solve-err", traces_dir)
    types = [e.type for e in events]
    assert "error" in types
    assert "abstained" not in types
    assert "workspace_ready" in types  # clone + install traced before the crash
    assert traces.verify_chain("solve-err", traces_dir).ok


@pytest.mark.slow
def test_pipeline_run_started_caps_and_workspace_ready(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    workspaces = tmp_path / "workspaces"
    proof = tmp_path / "proof"

    def solver(repo_path, issue, *, run_id):
        return _solver_success(repo_path, issue, traces_dir, run_id)

    outcome = solve_issue(
        ISSUE_URL,
        run_id="solve-caps",
        effort="medium",
        workspaces_root=workspaces,
        traces_dir=traces_dir,
        proof_root=proof,
        fetch_fn=lambda url: _issue(),
        clone_fn=_clone_stub,
        venv_fn=lambda repo: VenvInfo(
            python=repo / ".venv", created=True,
            log="$ uv pip install -r requirements.txt\n(exit 0)\nok\n",
        ),
        solver_fn=solver,
    )
    assert outcome.verdict == "VERIFIED"

    events = traces.load("solve-caps", traces_dir)
    started = next(e for e in events if e.type == "run_started")
    # Effort default (medium -> 30 steps), not the IssueCaps() default of 10.
    assert started.caps["max_steps"] == EFFORT_MAX_STEPS["medium"]
    ready = next(e for e in events if e.type == "workspace_ready")
    assert ready.repo == "o/r"
    # SHA at clone time: the solver's test commit sits on top of it.
    assert ready.repo_sha == outcome.solver.test_parent
    assert ready.repo_sha == _git(
        workspaces / "solve-caps" / "r", "rev-parse", "HEAD~2"
    )
    assert ready.install_exit_codes == [0]
    assert ready.venv_created is True

    # An explicit --max-steps overrides the effort default in run_started.
    def solver2(repo_path, issue, *, run_id):
        return _solver_success(repo_path, issue, traces_dir, run_id)

    outcome2 = solve_issue(
        ISSUE_URL,
        run_id="solve-caps2",
        effort="medium",
        max_steps=7,
        workspaces_root=workspaces,
        traces_dir=traces_dir,
        proof_root=proof,
        fetch_fn=lambda url: _issue(),
        clone_fn=_clone_stub,
        venv_fn=lambda repo: None,
        solver_fn=solver2,
    )
    assert outcome2.verdict == "VERIFIED"
    events2 = traces.load("solve-caps2", traces_dir)
    started2 = next(e for e in events2 if e.type == "run_started")
    assert started2.caps["max_steps"] == 7
    assert traces.verify_chain("solve-caps2", traces_dir).ok
