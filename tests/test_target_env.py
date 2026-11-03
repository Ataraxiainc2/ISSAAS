"""Target test environments: dependency discovery and reverify --setup.

Offline: temp project fixtures with installation stubbed via a fake runner.
No network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from issa_as.workspace import VenvInfo, setup_venv


def _stub_install_runner(seen: list):
    def fake(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    return fake


def _stub_repo(workspaces: Path, name: str = "run1") -> Path:
    repo = workspaces / name / "repo"
    repo.mkdir(parents=True)
    return repo


def test_setup_venv_installs_pyproject_test_extra(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'x'\n[project.optional-dependencies]\ntest = ['pytest>=8']\n",
        encoding="utf-8",
    )
    seen: list = []
    info = setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    assert info.created is True
    editable = next(i for i, c in enumerate(seen) if c[-2:] == ["-e", "."])
    extra = next(i for i, c in enumerate(seen) if c[-1] == ".[test]")
    assert editable < extra  # editable install runs first
    assert not any("-r" in c for c in seen)
    assert len(seen) == 3  # venv + editable + extra (extra already mentions pytest)


def test_setup_venv_installs_dependency_group(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'x'\n[dependency-groups]\ndev = ['pytest']\n",
        encoding="utf-8",
    )
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    assert any(c[-1] == ".[dev]" for c in seen)


def test_setup_venv_installs_every_requirements_pattern(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "requirements-test.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "test").mkdir()
    (repo / "test" / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    req_cmds = [c for c in seen if "-r" in c]
    assert len(req_cmds) == 2
    assert req_cmds[0][-1].endswith("requirements-test.txt")
    assert req_cmds[1][-1].endswith("requirements.txt")


def test_setup_venv_installs_test_subdir_requirements(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "test").mkdir()
    (repo / "test" / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    req_cmds = [c for c in seen if "-r" in c]
    assert len(req_cmds) == 1
    assert req_cmds[0][-1].endswith("requirements.txt")


def test_setup_venv_installs_tox_test_requirements(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "test").mkdir()
    (repo / "test" / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "tox.ini").write_text(
        "[testenv]\ndeps =\n    -r {toxinidir}/test/requirements.txt\n",
        encoding="utf-8",
    )
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    req_cmds = [c for c in seen if "-r" in c]
    assert len(req_cmds) == 1
    assert req_cmds[0][-1].endswith("requirements.txt")


def test_setup_venv_skips_url_and_vcs_tox_deps(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "tox.ini").write_text(
        "[testenv]\ndeps =\n"
        "    pytest\n"
        "    https://example.com/packages/pkg-1.0.zip\n"
        "    git+https://github.com/o/r.git\n",
        encoding="utf-8",
    )
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    assert any(c[-1] == "pytest" for c in seen)
    for cmd in seen:
        assert not any("example.com" in p or p.startswith("git+") for p in cmd)


def test_setup_venv_ensures_pytest_when_missing(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "requirements-test.txt").write_text("requests\n", encoding="utf-8")
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    assert any("-r" in c for c in seen)
    assert any(c[-1] == "pytest" for c in seen)


def test_setup_venv_installs_pytest_when_no_test_deps(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    assert any(c[-1] == "pytest" for c in seen)


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


def _stored_run(tmp_path: Path, run_id: str) -> None:
    """Minimal solve bundle + workspace repo that re-verifies VERIFIED."""
    import sys

    from issa_as.pipeline import solve_issue
    from issa_as.solver import SolverResult
    from issa_as.workspace import IssueInfo

    calc = "def add(a, b):\n    return a - b\n"
    good = "def add(a, b):\n    return a + b\n"
    test = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    issue = IssueInfo(
        issue_url="https://github.com/o/r/issues/1", owner="o", repo="r",
        repo_full="o/r", number=1, title="t", body="b",
        clone_url="https://github.com/o/r.git",
    )

    def clone_stub(clone_url: str, dest: Path) -> Path:
        dest.mkdir(parents=True)
        (dest / "calc.py").write_text(calc, encoding="utf-8")
        _git(dest, "init", "-q")
        _git(dest, "add", ".")
        _git(dest, "commit", "-qm", "base")
        return dest

    def solver(repo_path, issue_arg, *, run_id):
        repo = Path(repo_path)
        (repo / "test_calc.py").write_text(test, encoding="utf-8")
        _git(repo, "add", "test_calc.py")
        _git(repo, "commit", "-qm", "test")
        test_commit = _git(repo, "rev-parse", "HEAD")
        (repo / "calc.py").write_text(good, encoding="utf-8")
        _git(repo, "add", "calc.py")
        _git(repo, "commit", "-qm", "fix")
        fix_commit = _git(repo, "rev-parse", "HEAD")
        return SolverResult(
            run_id=run_id, issue_id="o/r#1", test_file="test_calc.py",
            test_command=[sys.executable, "-m", "pytest", "-q",
                          "-p", "no:cacheprovider", "test_calc.py"],
            test_commit=test_commit, fix_commit=fix_commit,
            test_parent=_git(repo, "rev-parse", f"{test_commit}^"),
            patch_text=_git(repo, "diff", f"{fix_commit}^", fix_commit, "--"),
            model="m",
        )

    outcome = solve_issue(
        issue.issue_url, run_id=run_id,
        workspaces_root=tmp_path / "workspaces",
        traces_dir=tmp_path / "traces",
        proof_root=tmp_path / "proof",
        fetch_fn=lambda url: issue,
        clone_fn=clone_stub,
        venv_fn=lambda repo: None,
        solver_fn=solver,
    )
    assert outcome.verdict == "VERIFIED"


def test_reverify_setup_runs_setup_before_verify(
    tmp_path: Path, monkeypatch
) -> None:
    import issa_as.workspace as workspace_mod
    from issa_as.pipeline import reverify_run

    run_id = "setup-test-1"
    _stored_run(tmp_path, run_id)
    calls: list = []

    def fake_setup(repo_path, *, workspaces_root=None, **kwargs):
        calls.append(Path(repo_path))
        return VenvInfo(python=Path(repo_path) / ".venv", created=True,
                        log="$ uv pip install pytest\n(exit 0)\nok\n")

    monkeypatch.setattr(workspace_mod, "setup_venv", fake_setup)
    result = reverify_run(
        run_id,
        workspaces_root=tmp_path / "workspaces",
        proof_root=tmp_path / "proof",
        traces_dir=tmp_path / "traces",
        setup=True,
    )
    assert result.verdict == "VERIFIED"
    assert len(calls) == 1
    install_log = tmp_path / "proof" / run_id / "install.log"
    assert "$ uv pip install pytest" in install_log.read_text(encoding="utf-8")


def test_reverify_without_setup_skips_setup(
    tmp_path: Path, monkeypatch
) -> None:
    import issa_as.workspace as workspace_mod
    from issa_as.pipeline import reverify_run

    run_id = "setup-test-2"
    _stored_run(tmp_path, run_id)

    def boom(repo_path, **kwargs):
        raise AssertionError("setup_venv must not run without --setup")

    monkeypatch.setattr(workspace_mod, "setup_venv", boom)
    result = reverify_run(
        run_id,
        workspaces_root=tmp_path / "workspaces",
        proof_root=tmp_path / "proof",
        traces_dir=tmp_path / "traces",
    )
    assert result.verdict == "VERIFIED"


def test_reverify_setup_cli_flag_reaches_pipeline(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import issa_as.pipeline as pipeline_mod
    from issa_as.cli import main
    from issa_as.verifier import VerifyResult

    seen: dict = {}

    def fake(run_id, *, setup=False, **kwargs):
        seen["run_id"] = run_id
        seen["setup"] = setup
        return VerifyResult("VERIFIED", "ok", "")

    monkeypatch.setattr(pipeline_mod, "reverify_run", fake)
    assert main(["reverify", "r1", "--setup"]) == 0
    assert seen == {"run_id": "r1", "setup": True}
    assert "VERIFIED" in capsys.readouterr().out
    assert main(["reverify", "r1"]) == 0
    assert seen == {"run_id": "r1", "setup": False}


def test_reverify_bundle_verdict_json_round_trip(tmp_path: Path) -> None:
    from issa_as.pipeline import reverify_run

    run_id = "setup-test-3"
    _stored_run(tmp_path, run_id)
    result = reverify_run(
        run_id,
        workspaces_root=tmp_path / "workspaces",
        proof_root=tmp_path / "proof",
        traces_dir=tmp_path / "traces",
    )
    assert result.verdict == "VERIFIED"
    doc = json.loads(
        (tmp_path / "proof" / run_id / "verdict.json").read_text(encoding="utf-8")
    )
    assert doc["verdict"] == "VERIFIED"


def _recording_runner(seen: list):
    def fake(cmd, **kwargs):
        seen.append((list(cmd), dict(kwargs)))
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    return fake


def test_setup_venv_reuses_existing_venv_without_prompt(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / ".venv").mkdir()  # pre-existing venv must be reused, never replaced
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_recording_runner(seen)
    )
    venv_cmd = seen[0][0]
    assert venv_cmd[:3] == ["uv", "venv", ".venv"]
    assert "--allow-existing" in venv_cmd
    assert seen, "expected at least the venv command"
    for _, kwargs in seen:
        assert kwargs.get("stdin") is subprocess.DEVNULL


def test_install_sources_names_each_install_step() -> None:
    from issa_as.pipeline import _install_sources

    log = (
        "$ uv venv .venv --allow-existing\n(exit 0)\n"
        "$ uv pip install -e .\n(exit 0)\n"
        "$ uv pip install -r requirements-test.txt\n(exit 0)\n"
    )
    assert _install_sources(log) == ["the repo package", "requirements-test.txt"]


def test_reverify_setup_failure_writes_install_log(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import pytest as _pytest

    import issa_as.workspace as workspace_mod
    from issa_as.pipeline import reverify_run
    from issa_as.workspace import WorkspaceError

    run_id = "setup-fail-1"
    _stored_run(tmp_path, run_id)

    def boom(repo_path, **kwargs):
        raise WorkspaceError("uv venv failed (see log)", log="partial setup output\n")

    monkeypatch.setattr(workspace_mod, "setup_venv", boom)
    with _pytest.raises(WorkspaceError, match="setup failed"):
        reverify_run(
            run_id,
            workspaces_root=tmp_path / "workspaces",
            proof_root=tmp_path / "proof",
            traces_dir=tmp_path / "traces",
            setup=True,
        )
    install_log = tmp_path / "proof" / run_id / "install.log"
    content = install_log.read_text(encoding="utf-8")
    assert "--- reverify --setup ---" in content
    assert "partial setup output" in content
    assert "setup failed" in content
    assert "creating venv" in capsys.readouterr().err


def test_setup_venv_installs_all_sources_together(tmp_path: Path) -> None:
    """Every discovered source is installed, not only the first one."""
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'x'\n"
        "[project.optional-dependencies]\n"
        "test = ['pytest>=8']\ntesting = ['pytest>=8']\n"
        "[dependency-groups]\ndev = ['pytest']\n",
        encoding="utf-8",
    )
    (repo / "requirements-test.txt").write_text("requests\n", encoding="utf-8")
    (repo / "test").mkdir()
    (repo / "test" / "requirements.txt").write_text("requests\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("pytest>=8\n", encoding="utf-8")
    (repo / "more").mkdir()
    (repo / "more" / "reqs.txt").write_text("requests\n", encoding="utf-8")
    (repo / "tox.ini").write_text(
        "[testenv]\ndeps =\n"
        "    -r {toxinidir}/more/reqs.txt\n"
        "    pytest-asyncio\n"
        "    https://example.com/packages/pkg-1.0.zip\n"
        "    git+https://github.com/o/r.git\n",
        encoding="utf-8",
    )
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    tails = [c[-1] for c in seen]
    for extra in (".[test]", ".[testing]", ".[dev]"):
        assert extra in tails
    installed_files = [c[-1] for c in seen if "-r" in c]
    assert len(installed_files) == 4
    assert installed_files[0].endswith("requirements-test.txt")
    assert installed_files[1].replace("\\", "/").endswith("test/requirements.txt")
    assert installed_files[2].endswith("requirements.txt")
    assert installed_files[3].endswith("reqs.txt")
    assert any(c[-1] == "pytest-asyncio" for c in seen)
    for cmd in seen:
        assert not any("example.com" in p or p.startswith("git+") for p in cmd)
    # pytest is mentioned by the installed sources, so no bare install.
    assert not any(c[-1] == "pytest" for c in seen)


def test_setup_venv_skips_plain_requirements_without_pytest(tmp_path: Path) -> None:
    """A plain requirements.txt without pytest is runtime deps, not tests."""
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "requirements.txt").write_text("requests\n", encoding="utf-8")
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    assert not any("-r" in c for c in seen)
    assert any(c[-1] == "pytest" for c in seen)


def _addopts_repo(workspaces: Path, name: str, files: dict) -> Path:
    repo = _stub_repo(workspaces, name)
    for rel, text in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return repo


def test_setup_venv_installs_cov_plugin_from_each_config(tmp_path: Path) -> None:
    """addopts --cov in pytest.ini/setup.cfg/tox.ini/pyproject needs pytest-cov."""
    cases = [
        ("c-ini", {"pytest.ini": "[pytest]\naddopts = --cov=myapp\n"}),
        ("c-cfg", {"setup.cfg": "[tool:pytest]\naddopts = --cov=myapp\n"}),
        ("c-tox", {"tox.ini": "[pytest]\naddopts = --cov=myapp\n"}),
        (
            "c-toml",
            {
                "pyproject.toml": (
                    "[project]\nname = 'x'\n"
                    "[tool.pytest.ini_options]\naddopts = '--cov=myapp'\n"
                )
            },
        ),
    ]
    for name, files in cases:
        workspaces = tmp_path / f"ws-{name}"
        repo = _addopts_repo(workspaces, "run1", files)
        seen: list = []
        setup_venv(
            repo, workspaces_root=workspaces, uv_bin="uv",
            runner=_stub_install_runner(seen),
        )
        assert any(c[-1] == "pytest-cov" for c in seen), name


def test_setup_venv_installs_timeout_and_xdist_plugins(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "pytest.ini").write_text(
        "[pytest]\naddopts = --timeout=100 -n auto --numprocesses=2\n",
        encoding="utf-8",
    )
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    assert any(c[-1] == "pytest-timeout" for c in seen)
    assert any(c[-1] == "pytest-xdist" for c in seen)


def test_setup_venv_skips_plugin_already_covered(tmp_path: Path) -> None:
    """No extra install when a requirements file already names the plugin."""
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "requirements-test.txt").write_text(
        "pytest\npytest-cov\n", encoding="utf-8"
    )
    (repo / "pytest.ini").write_text(
        "[pytest]\naddopts = --cov=myapp\n", encoding="utf-8"
    )
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    assert any("-r" in c for c in seen)
    assert not any(c[-1] == "pytest-cov" for c in seen)


def test_setup_venv_no_plugin_without_plugin_addopts(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    repo = _stub_repo(workspaces)
    (repo / "pytest.ini").write_text(
        "[pytest]\naddopts = -q -p no:cacheprovider\n", encoding="utf-8"
    )
    seen: list = []
    setup_venv(
        repo, workspaces_root=workspaces, uv_bin="uv", runner=_stub_install_runner(seen)
    )
    assert not any("pytest-cov" in c or "pytest-timeout" in c for c in seen)
    assert not any(c[-1] == "pytest-xdist" for c in seen)


def test_required_pytest_plugins_token_variants() -> None:
    from issa_as.workspace import _required_pytest_plugins

    assert _required_pytest_plugins(["-n", "auto"]) == ["pytest-xdist"]
    assert _required_pytest_plugins(["-n4"]) == ["pytest-xdist"]
    assert _required_pytest_plugins(["-nauto"]) == ["pytest-xdist"]
    assert _required_pytest_plugins(["--numprocesses=2"]) == ["pytest-xdist"]
    assert _required_pytest_plugins(["--timeout=60"]) == ["pytest-timeout"]
    assert _required_pytest_plugins(["--no-cov"]) == ["pytest-cov"]
    assert _required_pytest_plugins(["-q", "-p", "no:cacheprovider"]) == []


def test_reverify_verdict_json_records_suite_flags(tmp_path: Path) -> None:
    from issa_as.pipeline import reverify_run

    run_id = "setup-flags-1"
    _stored_run(tmp_path, run_id)
    result = reverify_run(
        run_id,
        workspaces_root=tmp_path / "workspaces",
        proof_root=tmp_path / "proof",
        traces_dir=tmp_path / "traces",
    )
    assert result.verdict == "VERIFIED"
    assert result.suite_flags == []
    doc = json.loads(
        (tmp_path / "proof" / run_id / "verdict.json").read_text(encoding="utf-8")
    )
    assert doc["suite_flags"] == []
