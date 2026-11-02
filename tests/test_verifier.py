"""Tests for verifier.py. Offline: throwaway git repos under pytest tmp_path."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from issa_as import traces
from issa_as.verifier import verify

CALC = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
GOOD_CALC = CALC.replace("return a - b", "return a + b")
BAD_CALC = "def add(a, b):\n    return 3\n\n\ndef mul(a, b):\n    return 0\n"
TEST_CALC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
TEST_MISC = "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t.t",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture
def workspace_repo(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "ws"
    repo = ws / "repo"
    repo.mkdir(parents=True)
    (repo / "calc.py").write_text(CALC, encoding="utf-8")
    (repo / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    (repo / "test_misc.py").write_text(TEST_MISC, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return ws, repo


def _base(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _patch_for(repo: Path, new_calc: str) -> str:
    (repo / "calc.py").write_text(new_calc, encoding="utf-8")
    diff = _git(repo, "diff")
    _git(repo, "checkout", "--", "calc.py")
    assert diff
    return diff


def _cmd(test: str) -> list[str]:
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test]


def _traces_dir(tmp_path: Path) -> Path:
    return tmp_path / "traces"


def test_verified(workspace_repo: tuple[Path, Path], tmp_path: Path) -> None:
    ws, repo = workspace_repo
    base = _base(repo)
    patch = _patch_for(repo, GOOD_CALC)
    result = verify(
        repo,
        base,
        patch,
        "test_calc.py",
        _cmd("test_calc.py"),
        suite_cmd=_cmd("test_misc.py"),
        workspace_root=ws,
        run_id="v-verified",
        traces_dir=_traces_dir(tmp_path),
    )
    assert result.verdict == "VERIFIED"
    assert result.base_returncode != 0
    assert result.patched_returncode == 0
    (event,) = traces.load("v-verified", _traces_dir(tmp_path))
    assert event.type == "verify_verdict"
    assert event.verdict == "VERIFIED"
    assert event.f2p_pass is True
    assert _git(repo, "rev-parse", "HEAD") == base
    assert _git(repo, "status", "--porcelain") == ""


def test_unproven_when_test_passes_on_base(workspace_repo: tuple[Path, Path]) -> None:
    ws, repo = workspace_repo
    result = verify(
        repo, _base(repo), None, "test_misc.py", _cmd("test_misc.py"), workspace_root=ws
    )
    assert result.verdict == "UNPROVEN"
    assert result.base_returncode == 0


def test_no_test_when_file_missing(workspace_repo: tuple[Path, Path]) -> None:
    ws, repo = workspace_repo
    result = verify(
        repo, _base(repo), None, "test_missing.py", _cmd("test_missing.py"), workspace_root=ws
    )
    assert result.verdict == "NO_TEST"


def test_regression_when_suite_breaks(workspace_repo: tuple[Path, Path]) -> None:
    ws, repo = workspace_repo
    patch = _patch_for(repo, BAD_CALC)
    result = verify(
        repo,
        _base(repo),
        patch,
        "test_calc.py",
        _cmd("test_calc.py"),
        suite_cmd=_cmd("test_misc.py"),
        workspace_root=ws,
    )
    assert result.verdict == "REGRESSION"
    assert result.patched_returncode == 0
    assert result.regressions == 1


def test_error_when_patch_does_not_apply(workspace_repo: tuple[Path, Path]) -> None:
    ws, repo = workspace_repo
    result = verify(
        repo, _base(repo), "not a diff", "test_calc.py", _cmd("test_calc.py"), workspace_root=ws
    )
    assert result.verdict == "ERROR"
    assert "patch" in result.reason


def test_error_when_repo_escapes_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = verify(
        outside, "HEAD", None, "test_x.py", _cmd("test_x.py"), workspace_root=ws
    )
    assert result.verdict == "ERROR"
    assert "workspace" in result.reason


def test_subprocess_gets_no_secrets(
    workspace_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, repo = workspace_repo
    monkeypatch.setenv("MODEL_API_KEY", "super-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "super-secret")
    probe = [
        sys.executable,
        "-c",
        "import os; print('LEAK' if os.environ.get('MODEL_API_KEY') else 'CLEAN'); "
        "print('TMP=' + os.environ.get('TMP', ''))",
    ]
    result = verify(repo, _base(repo), None, "test_misc.py", probe, workspace_root=ws)
    assert result.verdict == "UNPROVEN"  # probe exits 0, so the "test" passes on base
    assert "\nCLEAN\n" in result.log
    assert "super-secret" not in result.log
    assert f"TMP={ws.resolve()}" in result.log


def test_still_fails_after_patch_is_error(workspace_repo: tuple[Path, Path]) -> None:
    ws, repo = workspace_repo
    result = verify(
        repo, _base(repo), None, "test_calc.py", _cmd("test_calc.py"), workspace_root=ws
    )
    assert result.verdict == "ERROR"
    assert "still fails" in result.reason


def test_resolve_relative_argv0_anchored_at_repo(tmp_path: Path) -> None:
    from issa_as.verifier import _resolve_cmd

    repo = tmp_path / "ws" / "repo"
    repo.mkdir(parents=True)
    resolved = _resolve_cmd(["tools/myuv.exe", "--version"], repo)
    assert resolved[0] == str(repo / "tools/myuv.exe")
    assert resolved[1:] == ["--version"]
    # Forward slashes (live run #3 shape) anchor the same way.
    resolved = _resolve_cmd([".venv/Scripts/python.exe", "-m", "pytest"], repo)
    assert resolved[0] == str(repo / ".venv/Scripts/python.exe")
    # Absolute paths pass through untouched.
    resolved = _resolve_cmd([sys.executable, "-m", "pytest"], repo)
    assert resolved[0] == sys.executable


def test_resolve_bare_python_and_pytest_map_to_venv(tmp_path: Path) -> None:
    from issa_as.verifier import _resolve_cmd, _venv_python_for

    repo = tmp_path / "ws" / "repo"
    repo.mkdir(parents=True)
    # No venv present: fall back to the current interpreter.
    assert _resolve_cmd(["python", "-m", "pytest"], repo)[0] == sys.executable
    mapped = _resolve_cmd(["pytest", "-q", "test_x.py"], repo)
    assert mapped[:2] == [sys.executable, "-m"]
    assert mapped[2:] == ["pytest", "-q", "test_x.py"]
    # Venv interpreter present: bare names map to it.
    venv_py = _venv_python_for(repo)
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()
    assert _resolve_cmd(["python", "-c", "pass"], repo)[0] == str(venv_py)
    mapped = _resolve_cmd(["pytest", "-q"], repo)
    assert mapped == [str(venv_py), "-m", "pytest", "-q"]


def test_verify_with_relative_interpreter_binary(
    workspace_repo: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative argv[0] with a separator must start (Windows live run #3)."""
    import shutil

    ws, repo = workspace_repo
    uv_src = Path(".tools/uv.exe")
    if not uv_src.is_file():
        pytest.skip("project uv binary missing")
    tools_dir = repo / "tools"
    tools_dir.mkdir()
    shutil.copy2(uv_src, tools_dir / "myuv.exe")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add runner stub")
    base = _base(repo)
    monkeypatch.chdir(tmp_path)  # parent cwd differs from the repo cwd
    result = verify(
        repo,
        base,
        None,
        "test_misc.py",
        ["tools/myuv.exe", "--version"],
        workspace_root=ws,
    )
    assert result.verdict == "UNPROVEN"  # stub exits 0, so the "test" passes on base
    assert "FAILED TO START" not in result.log


def _sibling_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    """Repo where the new test is fixed by the patch but a sibling always fails."""
    ws = tmp_path / "ws"
    repo = ws / "repo"
    repo.mkdir(parents=True)
    (repo / "calc.py").write_text(CALC, encoding="utf-8")
    (repo / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    (repo / "test_sibling.py").write_text(
        "def test_sibling():\n    assert False, 'always broken'\n", encoding="utf-8"
    )
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return ws, repo, _base(repo)


def test_f2p_ignores_extra_files_in_test_command(tmp_path: Path) -> None:
    """F2P runs only test_file, even when test_command lists a failing sibling."""
    ws, repo, base = _sibling_repo(tmp_path)
    patch = _patch_for(repo, GOOD_CALC)
    both = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "test_calc.py", "test_sibling.py",
    ]
    result = verify(
        repo, base, patch, "test_calc.py", both, workspace_root=ws,
    )
    assert result.verdict == "VERIFIED"
    assert result.patched_returncode == 0


def test_preexisting_failure_not_counted_as_regression(tmp_path: Path) -> None:
    ws, repo, base = _sibling_repo(tmp_path)
    patch = _patch_for(repo, GOOD_CALC)
    suite = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_sibling.py"]
    result = verify(
        repo,
        base,
        patch,
        "test_calc.py",
        _cmd("test_calc.py"),
        suite_cmd=suite,
        baseline_commit=base,
        workspace_root=ws,
    )
    assert result.verdict == "VERIFIED"
    assert result.regressions == 0
    assert len(result.baseline_failures) == 1
    assert "test_sibling" in result.baseline_failures[0]


def test_real_new_failure_counted_as_regression(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    repo = ws / "repo"
    repo.mkdir(parents=True)
    (repo / "calc.py").write_text(CALC, encoding="utf-8")
    (repo / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    (repo / "test_misc.py").write_text(TEST_MISC, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _base(repo)
    patch = _patch_for(repo, BAD_CALC)
    suite = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_misc.py"]
    result = verify(
        repo,
        base,
        patch,
        "test_calc.py",
        _cmd("test_calc.py"),
        suite_cmd=suite,
        baseline_commit=base,
        workspace_root=ws,
    )
    # BAD_CALC fixes add() (F2P passes) but breaks mul(): one real regression,
    # and the baseline itself was green so nothing is recorded as pre-existing.
    assert result.verdict == "REGRESSION"
    assert result.regressions == 1
    assert result.baseline_failures == []


def test_tampered_trace_line_is_reported(tmp_path: Path) -> None:
    traces.append("chain-t", {"type": "run_started", "stage": "run"}, tmp_path)
    traces.append("chain-t", {"type": "triage_scored", "stage": "triage"}, tmp_path)
    traces.append("chain-t", {"type": "selected", "stage": "select"}, tmp_path)
    assert traces.verify_chain("chain-t", tmp_path).ok

    path = tmp_path / "chain-t.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["stage"] = "tampered"
    lines[1] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = traces.verify_chain("chain-t", tmp_path)
    assert not report.ok
    assert report.bad_line == 2


def test_collect_only_failure_is_infrastructure_error(
    workspace_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing pytest must blame the environment, never the patch."""
    from issa_as import verifier as verify_mod

    ws, repo = workspace_repo
    real_run = verify_mod._run

    def fake(cmd, cwd, env, timeout_s):
        if "--collect-only" in [str(p) for p in cmd]:
            return 1, (
                "$ pytest --collect-only -q test_calc.py\n(exit 1)\n"
                "ModuleNotFoundError: No module named pytest\n"
            )
        return real_run(cmd, cwd, env, timeout_s)

    monkeypatch.setattr(verify_mod, "_run", fake)
    result = verify(
        repo, _base(repo), None, "test_calc.py", _cmd("test_calc.py"), workspace_root=ws
    )
    assert result.verdict == "ERROR"
    assert "test environment incomplete" in result.reason
    assert "No module named pytest" in result.reason
    assert "still fails" not in result.reason


def test_pytest_no_tests_collected_is_infrastructure_error(
    workspace_repo: tuple[Path, Path],
) -> None:
    ws, repo = workspace_repo
    (repo / "test_empty.py").write_text("X = 1\n", encoding="utf-8")
    _git(repo, "add", "test_empty.py")
    _git(repo, "commit", "-qm", "add file with no tests")
    result = verify(
        repo, _base(repo), None, "test_empty.py", _cmd("test_empty.py"), workspace_root=ws
    )
    assert result.verdict == "ERROR"
    assert "test environment incomplete" in result.reason


def test_pytest_usage_error_is_infrastructure_error(
    workspace_repo: tuple[Path, Path],
) -> None:
    ws, repo = workspace_repo
    cmd = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "--no-such-flag-xyz", "test_misc.py",
    ]
    result = verify(
        repo, _base(repo), None, "test_misc.py", cmd, workspace_root=ws
    )
    assert result.verdict == "ERROR"
    assert "test environment incomplete" in result.reason


def test_pytest_collection_error_is_infrastructure_error(
    workspace_repo: tuple[Path, Path],
) -> None:
    ws, repo = workspace_repo
    (repo / "test_broken.py").write_text("def test_x(:\n    pass\n", encoding="utf-8")
    _git(repo, "add", "test_broken.py")
    _git(repo, "commit", "-qm", "add broken test file")
    result = verify(
        repo, _base(repo), None, "test_broken.py", _cmd("test_broken.py"), workspace_root=ws
    )
    assert result.verdict == "ERROR"
    assert "test environment incomplete" in result.reason


def test_missing_baseline_junit_is_infrastructure_error(
    workspace_repo: tuple[Path, Path],
) -> None:
    ws, repo = workspace_repo
    probe = [sys.executable, "-c", "pass"]  # exits 0, writes no JUnit file
    result = verify(
        repo,
        _base(repo),
        None,
        "test_misc.py",
        _cmd("test_misc.py"),
        suite_cmd=probe,
        baseline_commit=_base(repo),
        workspace_root=ws,
    )
    assert result.verdict == "ERROR"
    assert "test environment incomplete" in result.reason


def test_nested_workspace_suite_and_f2p_resolve_to_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested ``workspaces/<run>/<repo>``: suite + F2P resolve to the venv."""
    import os as _os

    from issa_as.pipeline import _default_suite_cmd
    from issa_as.verifier import _resolve_cmd, _venv_python_for

    run, repo_name = "run-1", "repo"
    repo_abs = tmp_path / "workspaces" / run / repo_name
    venv_py = _venv_python_for(repo_abs)
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()
    monkeypatch.chdir(tmp_path)  # mimic the project root as cwd
    rel_repo = Path("workspaces") / run / repo_name

    suite = _default_suite_cmd(rel_repo)
    assert _os.path.isabs(suite[0])
    resolved_suite = _resolve_cmd(suite, repo_abs)[0]
    assert Path(resolved_suite).is_file()
    assert Path(resolved_suite).resolve() == venv_py.resolve()

    # Solver-style venv-relative interpreter also resolves.
    f2p = [str(venv_py.relative_to(repo_abs)), "-m", "pytest", "test_x.py"]
    resolved_f2p = _resolve_cmd(f2p, repo_abs)[0]
    assert Path(resolved_f2p).is_file()

    # Root-relative suite path must not be anchored a second time.
    root_rel = str(rel_repo / venv_py.relative_to(repo_abs))
    assert _resolve_cmd([root_rel, "-m", "pytest"], repo_abs)[0] == root_rel
