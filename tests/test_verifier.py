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
