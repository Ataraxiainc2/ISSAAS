"""SECURITY.md respect: private-reporting repos get a private draft (Prompt 18).

Offline tests only: all GitHub traffic goes through in-memory fakes, all
bundles live under tmp_path. No network, no model calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from issa_as import publish as publish_mod
from issa_as import scout as scout_mod
from issa_as import select as select_mod
from issa_as import triage as triage_mod
from issa_as.cli import main

NOW = datetime.now(timezone.utc)

ANSIBLE_POLICY = (
    "Security Policy\n"
    "Vulnerability reports MUST be submitted by email to security@ansible.com "
    "and MUST NOT be reported through public channels such as GitHub issues, "
    "pull requests or discussions.\n"
    "Please include: title, description, affected versions, reproduction steps, "
    "impact and suggested fix.\n"
)

ADVISORY_POLICY = (
    "Security Policy\n"
    "Please report vulnerabilities using GitHub Security Advisories. "
    "Do not open public issues for security bugs.\n"
)

PERMISSIVE_POLICY = (
    "Security Policy\n"
    "Report bugs via GitHub issues. Pull requests welcome!\n"
)


# --- policy parse -----------------------------------------------------------


def test_parse_ansible_style_policy_is_private() -> None:
    policy = scout_mod.parse_security_policy(ANSIBLE_POLICY, "ansible/ansible-runner")
    assert policy.private_reporting is True
    assert policy.contact == "security@ansible.com"


def test_parse_advisory_only_policy_points_at_advisories() -> None:
    policy = scout_mod.parse_security_policy(ADVISORY_POLICY, "o/r")
    assert policy.private_reporting is True
    assert policy.contact == "https://github.com/o/r/security/advisories/new"


def test_parse_no_policy_is_not_private() -> None:
    for text in (None, "", "   \n  "):
        policy = scout_mod.parse_security_policy(text, "o/r")
        assert policy.private_reporting is False
        assert policy.contact == ""
        assert policy.source == ""


def test_parse_permissive_policy_is_not_private() -> None:
    policy = scout_mod.parse_security_policy(PERMISSIVE_POLICY, "o/r")
    assert policy.private_reporting is False


# --- scout fetch ------------------------------------------------------------


class FakeGitHub:
    """Minimal ScoutClient: canned texts, everything else passing."""

    def __init__(self, texts: dict[tuple[str, str], str | None]) -> None:
        self.texts = texts
        self.fetched: list[str] = []

    def search_issues(self, query: str) -> list[dict[str, Any]]:
        return []

    def get_repo(self, full_name: str) -> dict[str, Any]:
        return {
            "archived": False,
            "fork": False,
            "stargazers_count": 120,
            "size": 2048,
            "pushed_at": (NOW - timedelta(days=3)).isoformat(),
        }

    def get_text(self, full_name: str, path: str) -> str | None:
        self.fetched.append(path)
        return self.texts.get((full_name, path))

    def list_dir(self, full_name: str, path: str) -> list[str]:
        if path == "tests":
            return ["test_lib.py"]
        return []

    def linked_open_pr(self, full_name: str, number: int) -> bool:
        return False


def _issue(repo: str = "o/r", number: int = 1) -> dict[str, Any]:
    return {
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "repository_full_name": repo,
        "number": number,
        "title": "Crash on empty input",
        "body": "Steps to reproduce:\n1. Run it\n\n```python\nraise ValueError()\n```",
        "labels": [{"name": "bug"}],
        "user": {"login": "human"},
        "comments": 3,
        "created_at": (NOW - timedelta(days=5)).isoformat(),
    }


def test_fetch_uses_first_found_path() -> None:
    client = FakeGitHub({("o/r", ".github/SECURITY.md"): ADVISORY_POLICY})
    policy = scout_mod.fetch_security_policy(client, "o/r")
    assert policy.source == ".github/SECURITY.md"
    assert policy.private_reporting is True
    # Stops at the first hit: docs/SECURITY.md is never requested.
    assert "docs/SECURITY.md" not in client.fetched


def test_fetch_without_policy_is_not_private() -> None:
    assert scout_mod.fetch_security_policy(FakeGitHub({}), "o/r").private_reporting is False


def test_evaluate_issue_stores_policy_on_candidate() -> None:
    client = FakeGitHub(
        {
            ("o/r", "pyproject.toml"): "[project]\nname='lib'\n",
            ("o/r", "SECURITY.md"): ANSIBLE_POLICY,
        }
    )
    candidate = scout_mod.evaluate_issue(client, _issue(), None)
    assert candidate is not None
    assert candidate.security_policy.private_reporting is True
    assert candidate.security_policy.contact == "security@ansible.com"
    assert candidate.security_policy.source == "SECURITY.md"


# --- selection keeps the issue, says why ------------------------------------


def _scored(url: str, policy: dict[str, Any] | None) -> triage_mod.TriageResult:
    return triage_mod.TriageResult(
        issue_url=url,
        repo="o/r",
        number=1,
        title="Bug",
        runner="python",
        signals_score=0.7,
        solvability=0.7,
        difficulty="medium",
        is_real_bug=True,
        rationale="r",
        final_score=0.7,
        security_policy=policy,
    )


def test_pick_keeps_private_repo_and_names_disclosure(tmp_path) -> None:
    scored = [
        _scored(
            "https://github.com/o/r/issues/1",
            {"private_reporting": True, "contact": "security@o.com", "source": "SECURITY.md"},
        )
    ]
    selection = select_mod.pick(scored, proof_dir=tmp_path / "proof")
    assert selection.picked is not None
    assert selection.picked.issue_url.endswith("/issues/1")
    assert "private disclosure required" in selection.picked.reason
    assert selection.skipped == []


def test_pick_reason_without_policy_has_no_disclosure_note(tmp_path) -> None:
    selection = select_mod.pick(
        [_scored("https://github.com/o/r/issues/1", None)],
        proof_dir=tmp_path / "proof",
    )
    assert selection.picked is not None
    assert "private disclosure" not in selection.picked.reason


# --- publish: private repos never get public drafts --------------------------


def _write_bundle(
    root: Path,
    run_id: str = "20260101-o-r-1",
    issue_url: str = "https://github.com/o/r/issues/1",
) -> Path:
    bundle = root / "proof" / run_id
    bundle.mkdir(parents=True)
    (bundle / "verdict.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "issue_url": issue_url,
                "issue_id": "o/r#1",
                "verdict": "VERIFIED",
                "test_file": "tests/test_bug.py",
                "test_command": ["python", "-m", "pytest", "tests/test_bug.py"],
                "patch_summary": "Guard against empty input.",
                "model": "muse-spark-1.3-contributor",
                "f2p_pass": True,
            }
        ),
        encoding="utf-8",
    )
    (bundle / "issue.json").write_text(
        json.dumps(
            {
                "issue_url": issue_url,
                "repo_full": "o/r",
                "title": "Crash on empty input",
                "body": "Steps to reproduce: pass empty input.",
            }
        ),
        encoding="utf-8",
    )
    (bundle / "fix.patch").write_text(
        "diff --git a/lib.py b/lib.py\n+if not value:\n+    raise ValueError('empty')\n",
        encoding="utf-8",
    )
    return bundle


def test_publish_private_repo_writes_disclosure_not_public_draft(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = _write_bundle(tmp_path)
    result = publish_mod.publish_bundle(bundle, policy_text=ANSIBLE_POLICY)
    assert result["private_reporting"] is True
    assert result["refused_public"] is True
    assert (bundle / "disclosure.md").is_file()
    leftovers = [
        p.name
        for p in bundle.iterdir()
        if p.suffix == ".md" and p.name != "disclosure.md"
    ]
    assert leftovers == [], f"public drafts must never be written: {leftovers}"


def test_disclosure_content_fields_and_ai_line(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = _write_bundle(tmp_path)
    publish_mod.publish_bundle(bundle, policy_text=ANSIBLE_POLICY)
    text = (bundle / "disclosure.md").read_text(encoding="utf-8")
    assert publish_mod.DRAFT_HEADER in text
    assert "ISSA AS never sends it" in text
    assert "security@ansible.com" in text
    assert "Crash on empty input" in text
    assert "o/r" in text
    assert "Affected versions" in text
    assert "tests/test_bug.py" in text  # reproduction steps = regression test
    assert "fix.patch" in text  # suggested fix
    assert "https://github.com/o/r/issues/1" in text
    assert "Fail-to-Pass" in text
    assert publish_mod.AI_DISCLOSURE_LINE in text


def test_publish_public_repo_writes_pr_draft_only(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = _write_bundle(tmp_path)
    result = publish_mod.publish_bundle(bundle, policy_text=PERMISSIVE_POLICY)
    assert result["private_reporting"] is False
    assert (bundle / "pr_draft.md").is_file()
    assert not (bundle / "disclosure.md").exists()
    assert publish_mod.AI_DISCLOSURE_LINE in (bundle / "pr_draft.md").read_text(
        encoding="utf-8"
    )


def test_publish_reads_policy_from_workspace_repo(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = _write_bundle(tmp_path)
    repo_dir = tmp_path / "workspaces" / bundle.name / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "SECURITY.md").write_text(ANSIBLE_POLICY, encoding="utf-8")
    result = publish_mod.publish_bundle(bundle)
    assert result["private_reporting"] is True
    assert (bundle / "disclosure.md").is_file()


# --- issa disclosure ---------------------------------------------------------


def test_disclosure_command_builds_from_workspace_policy(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = _write_bundle(tmp_path)
    repo_dir = tmp_path / "workspaces" / bundle.name / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "SECURITY.md").write_text(ANSIBLE_POLICY, encoding="utf-8")
    assert main(["disclosure", bundle.name]) == 0
    assert "disclosure.md" in capsys.readouterr().out
    text = (bundle / "disclosure.md").read_text(encoding="utf-8")
    assert "security@ansible.com" in text
    assert publish_mod.AI_DISCLOSURE_LINE in text


class OneShotClient:
    """Fake GitHub contents client: counts requests, serves one policy."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests = 0

    def get_text(self, full_name: str, path: str) -> str | None:
        self.requests += 1
        return self.text


def test_disclosure_without_workspace_uses_one_github_request(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = _write_bundle(tmp_path)
    client = OneShotClient(ANSIBLE_POLICY)
    out = publish_mod.build_disclosure_for_run(bundle.name, client=client)
    assert Path(out).resolve() == (bundle / "disclosure.md").resolve()
    assert client.requests == 1
    assert "security@ansible.com" in out.read_text(encoding="utf-8")


def test_publish_cli_reports_private_refusal(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = _write_bundle(tmp_path)
    (tmp_path / "workspaces" / bundle.name / "repo").mkdir(parents=True)
    (tmp_path / "workspaces" / bundle.name / "repo" / "SECURITY.md").write_text(
        ANSIBLE_POLICY, encoding="utf-8"
    )
    assert main(["publish", "--bundle", str(bundle)]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out and "no network writes" in out
    assert "refused public" in out
    assert (bundle / "disclosure.md").is_file()
