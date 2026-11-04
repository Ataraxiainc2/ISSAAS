"""Offline tests for the guided scout (Prompt 26, items 5 and 7).

No network, no model calls: GitHub is faked and the proposing model is a
FakeProvider returning canned JSON.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from issa_as import scout as scout_mod
from issa_as import traces as trace_log
from issa_as.providers import Completion
from issa_as.solver import run_solver, solver_child_env
from issa_as.workspace import IssueInfo

NOW = datetime.now(timezone.utc)
REPO = "octo/lib"

TOKEN_KEYS = ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT", "GH_ENTERPRISE_TOKEN")


def make_issue(number: int) -> dict[str, Any]:
    return {
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "repository_full_name": REPO,
        "number": number,
        "title": "Bug: crash on empty input",
        "body": "Steps to reproduce:\n1. Run it\n\n```python\nraise ValueError()\n```",
        "labels": [{"name": "bug"}],
        "user": {"login": "human"},
        "comments": 3,
        "created_at": (NOW - timedelta(days=5)).isoformat(),
    }


def good_repo() -> dict[str, Any]:
    return {
        "archived": False,
        "fork": False,
        "stargazers_count": 120,
        "size": 2048,
        "pushed_at": (NOW - timedelta(days=3)).isoformat(),
    }


class FakeGitHub:
    """In-memory ScoutClient: every query finds one fresh issue."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search_issues(self, query: str) -> list[dict[str, Any]]:
        self.queries.append(query)
        return [make_issue(len(self.queries))]

    def get_repo(self, full_name: str) -> dict[str, Any]:
        assert full_name == REPO
        return good_repo()

    def get_text(self, full_name: str, path: str) -> str | None:
        if (full_name, path) == (REPO, "pyproject.toml"):
            return "[project]\nname = 'lib'\n"
        return None

    def list_dir(self, full_name: str, path: str) -> list[str]:
        return {
            (REPO, "tests"): ["test_lib.py"],
            (REPO, ".github/workflows"): ["ci.yml"],
        }.get((full_name, path), [])

    def linked_open_pr(self, full_name: str, number: int) -> bool:
        return False


def guided_answer(queries: list[dict[str, Any]], rationale: str = "why") -> str:
    return json.dumps({"queries": queries, "rationale": rationale})


class FakeProvider:
    """Canned proposing model (or a raising one, for the fallback test)."""

    name = "fake"
    model = "fake-model"

    def __init__(self, text: str = "", error: BaseException | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, stage, prompt, effort="low", run_id=None, **kwargs):
        self.calls.append({"stage": stage, "prompt": prompt, "effort": effort})
        if self.error is not None:
            raise self.error
        return Completion(
            text=self.text, model=self.model, provider=self.name,
            in_tokens=10, out_tokens=20, cost_usd=0.0, price_known=True,
        )


def valid_proposal(keywords: str = "sql injection") -> dict[str, Any]:
    return {"keywords": keywords, "class": None, "why": "past wins"}


def test_valid_proposal_becomes_query_with_language_qualifier(tmp_path: Path) -> None:
    provider = FakeProvider(guided_answer([valid_proposal()], rationale="past sqli wins"))
    client = FakeGitHub()
    outcome = scout_mod.discover_full(
        client, limit=5, languages=("python",), guided=True,
        provider=provider, proof_root=tmp_path / "proof",
    )
    assert outcome.guided is True
    assert outcome.accepted == [valid_proposal()]
    assert outcome.rejected == []
    assert client.queries[0] == "type:issue state:open sql injection language:python"
    assert outcome.candidates
    assert outcome.candidates[0].matched_query == client.queries[0]


def test_bad_proposals_rejected_with_reasons() -> None:
    provider = FakeProvider(
        guided_answer([
            {"keywords": "repo:octo/lib", "class": None, "why": "sneaky"},
            {"keywords": "x" * 61, "class": None, "why": "long"},
            {"keywords": "drop;table", "class": None, "why": "chars"},
        ])
    )
    accepted, rejected, rationale, _ = scout_mod.propose_guided_queries(provider, [])
    assert accepted == []
    assert len(rejected) == 3
    reasons = [item["reason"] for item in rejected]
    assert "`:`" in reasons[0]
    assert "too long" in reasons[1]
    assert "bad characters" in reasons[2]
    assert rationale == "why"


def test_more_than_five_proposals_cut_to_five() -> None:
    proposals = [
        {"keywords": f"heap rescue {n}", "class": None, "why": "w"}
        for n in range(7)
    ]
    provider = FakeProvider(guided_answer(proposals))
    accepted, rejected, _, _ = scout_mod.propose_guided_queries(provider, [])
    assert len(accepted) == 5
    assert len(rejected) == 2
    assert all(item["reason"] == "cut: more than 5 proposals" for item in rejected)


def test_guided_run_falls_back_to_fixed_queries_when_model_fails(tmp_path: Path) -> None:
    provider = FakeProvider(error=RuntimeError("boom"))
    client = FakeGitHub()
    outcome = scout_mod.discover_full(
        client, limit=3, languages=("python",), guided=True,
        provider=provider, proof_root=tmp_path / "proof",
    )
    assert outcome.accepted == [] and outcome.rejected == []
    assert "boom" in outcome.rationale and "using fixed queries" in outcome.rationale
    assert outcome.candidates
    assert client.queries
    assert all(query.startswith("type:issue state:open") for query in client.queries)


def test_trace_event_exists_and_contains_no_token(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-123")
    provider = FakeProvider(guided_answer([valid_proposal()]))
    traces_dir = tmp_path / "traces"
    outcome = scout_mod.discover_full(
        FakeGitHub(), limit=2, languages=("python",), guided=True,
        provider=provider, proof_root=tmp_path / "proof",
        run_id="guided-trace", traces_dir=traces_dir,
    )
    assert outcome.github_authenticated is True
    events = trace_log.load("guided-trace", traces_dir)
    proposed = [e for e in events if e.type == "scout_queries_proposed"]
    assert len(proposed) == 1
    assert "secret-token-123" not in json.dumps(proposed[0].model_dump(), default=str)


def test_solver_child_env_strips_github_tokens(monkeypatch: Any) -> None:
    for key in TOKEN_KEYS:
        monkeypatch.setenv(key, "secret-" + key)
    monkeypatch.setenv("KEEP_ME", "yes")
    env = solver_child_env()
    for key in TOKEN_KEYS:
        assert key not in env
    assert env["KEEP_ME"] == "yes"


def test_solver_child_env_base_override() -> None:
    env = solver_child_env({"GITHUB_TOKEN": "x", "GH_TOKEN": "y", "A": "b"})
    assert "GITHUB_TOKEN" not in env and "GH_TOKEN" not in env
    assert env["A"] == "b"


def _solver_answer_stdout() -> str:
    answer = {
        "test_file": "test_calc.py",
        "test_command": "pytest -q test_calc.py",
        "patch_summary": "correct add",
        "root_cause": "minus instead of plus",
        "confidence": 0.9,
    }
    return json.dumps(
        {"payload_type": "run.terminal.completed", "payload": {"text": json.dumps(answer)}}
    ) + "\n"


def test_run_solver_passes_sanitized_env_to_runner(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-xyz")
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "secret-enterprise")
    monkeypatch.setenv("KEEP_ME", "yes")
    seen_envs: list[Any] = []

    def fake(cmd, **kwargs):
        args = list(cmd)
        if args and args[0] == "muse":
            seen_envs.append(kwargs.get("env"))
            return subprocess.CompletedProcess(cmd, 0, _solver_answer_stdout(), "")
        if args[:2] == ["git", "rev-list"]:
            return subprocess.CompletedProcess(cmd, 0, "testc1\nfixc2\n", "")
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, "base0\n", "")
        if args[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(cmd, 0, "diff --git patch\n", "")
        raise AssertionError(f"unexpected command: {cmd}")

    workspaces = tmp_path / "workspaces"
    repo = workspaces / "run-env" / "repo"
    repo.mkdir(parents=True)
    issue = IssueInfo(
        issue_url="https://github.com/o/r/issues/1",
        owner="o", repo="r", repo_full="o/r", number=1,
        title="add returns wrong result",
        body="```python\nadd(1, 2)  # -1, expected 3\n```",
        labels=["bug"],
        clone_url="https://github.com/o/r.git",
    )
    run_solver(
        repo, issue, run_id="run-env",
        workspaces_root=workspaces, traces_dir=tmp_path / "traces",
        muse_runs_root=tmp_path / "muse-runs", runner=fake,
    )
    assert len(seen_envs) == 1
    env = seen_envs[0]
    for key in TOKEN_KEYS:
        assert key not in env
    assert env["KEEP_ME"] == "yes"
