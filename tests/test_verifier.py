"""Tests for verifier.py. Offline: throwaway git repos under pytest tmp_path."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from issa_as import traces
from issa_as import verifier as verify_mod
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
    # F2P runs first, so the patch must make it pass to reach the suite.
    patch = _patch_for(repo, GOOD_CALC)
    result = verify(
        repo,
        _base(repo),
        patch,
        "test_calc.py",
        _cmd("test_calc.py"),
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


def test_run_decodes_bytes_timeout_output(tmp_path: Path) -> None:
    """Bytes stdout/stderr from TimeoutExpired must decode, never crash."""
    import subprocess as _sp

    def boom(*args: object, **kwargs: object) -> object:
        raise _sp.TimeoutExpired(
            cmd="pytest", timeout=4, output=b"partial-\xff-bytes", stderr=b"err-\xff"
        )

    real = verify_mod.subprocess.run
    verify_mod.subprocess.run = boom  # type: ignore[assignment]
    try:
        rc, log = verify_mod._run(["pytest", "-q"], tmp_path, {}, 4)
    finally:
        verify_mod.subprocess.run = real
    assert rc is None
    assert "TIMEOUT" in log
    assert "partial-" in log and "err-" in log


def test_portable_rebuild_both_directions(tmp_path: Path) -> None:
    """A stored foreign-OS interpreter path rebuilds to this machine's venv."""
    only_bin = tmp_path / "only-bin"
    venv_bin = only_bin / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").touch()
    win_stored = [r"C:\x\.venv\Scripts\python.exe", "-m", "pytest", "-q", "t.py"]
    assert verify_mod.portable_test_command(win_stored, only_bin) == [
        str(venv_bin / "python"), "-m", "pytest", "-q", "t.py",
    ]

    only_scripts = tmp_path / "only-scripts"
    venv_scripts = only_scripts / ".venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    (venv_scripts / "python.exe").touch()
    posix_stored = ["/x/.venv/bin/python", "-m", "pytest", "-q", "t.py"]
    assert verify_mod.portable_test_command(posix_stored, only_scripts) == [
        str(venv_scripts / "python.exe"), "-m", "pytest", "-q", "t.py",
    ]

    # No venv file yet: neutral form, and bare pytest normalizes too.
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    assert verify_mod.portable_test_command(
        ["python", "-m", "pytest", "-q", "t.py"], empty_repo
    ) == ["python", "-m", "pytest", "-q", "t.py"]
    assert verify_mod.portable_test_command(
        ["pytest", "-q", "t.py"], empty_repo
    ) == ["python", "-m", "pytest", "-q", "t.py"]
    # Non-pytest commands pass through untouched.
    assert verify_mod.portable_test_command(["make", "test"], only_bin) == [
        "make", "test",
    ]


def test_suite_scope_for_unit_rule_and_override() -> None:
    assert verify_mod.suite_scope_for("test/unit/test_x.py") == "test/unit"
    assert verify_mod.suite_scope_for("tests/test_x.py") == "full"
    assert verify_mod.suite_scope_for(None) == "full"
    assert verify_mod.suite_scope_for("test/unit/test_x.py", "tests/e2e") == "tests/e2e"
    assert verify_mod.suite_scope_for("tests/test_x.py", "tests/e2e") == "tests/e2e"


def test_default_suite_cmd_scopes_unit_dir(tmp_path: Path) -> None:
    from issa_as.pipeline import _default_suite_cmd

    assert _default_suite_cmd(tmp_path, "test/unit/test_x.py", None)[-1] == "test/unit"
    assert _default_suite_cmd(tmp_path, "tests/test_x.py", None)[-1] == "no:cacheprovider"
    assert _default_suite_cmd(tmp_path, "tests/test_x.py", "tests/e2e")[-1] == "tests/e2e"


def test_suite_flags_added_to_suite_runs_only(
    workspace_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detected plugins add --timeout/-n to the suite runs, never to F2P."""
    ws, repo = workspace_repo
    monkeypatch.setattr(
        verify_mod,
        "_suite_extra_flags",
        lambda *args, **kwargs: ["--timeout=120", "-n", "auto"],
    )
    seen: list[list[str]] = []
    real_run = verify_mod._run

    def fake(cmd, cwd, env, timeout_s):
        seen.append([str(p) for p in cmd])
        stripped = [
            p for p in cmd if p not in ("--timeout=120", "-n", "auto")
        ]
        return real_run(stripped, cwd, env, timeout_s)

    monkeypatch.setattr(verify_mod, "_run", fake)
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
    )
    assert result.verdict == "VERIFIED"
    assert result.suite_flags == ["--timeout=120", "-n", "auto"]
    suite_runs = [c for c in seen if "test_misc.py" in c]
    assert suite_runs
    assert all(
        "--timeout=120" in c and "-n" in c and "auto" in c for c in suite_runs
    )
    f2p_runs = [c for c in seen if "test_calc.py" in c]
    assert f2p_runs
    assert all("--timeout=120" not in c and "-n" not in c for c in f2p_runs)


def test_suite_flags_empty_without_plugins(
    workspace_repo: tuple[Path, Path],
) -> None:
    """No venv in the fixture repo means no plugin probes and no flags."""
    ws, repo = workspace_repo
    result = verify(
        repo,
        _base(repo),
        _patch_for(repo, GOOD_CALC),
        "test_calc.py",
        _cmd("test_calc.py"),
        suite_cmd=_cmd("test_misc.py"),
        workspace_root=ws,
    )
    assert result.verdict == "VERIFIED"
    assert result.suite_flags == []


def test_suite_extra_flags_only_for_importable_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only importable target-venv plugins contribute suite flags."""
    repo = tmp_path / "repo"
    venv_py = verify_mod._venv_python_for(repo)
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()
    monkeypatch.setattr(
        verify_mod, "_venv_can_import",
        lambda python, module, env, timeout_s: module == "xdist",
    )
    assert verify_mod._suite_extra_flags(repo, {}, 5) == ["-n", "auto"]
    monkeypatch.setattr(verify_mod, "_venv_can_import", lambda *a, **k: False)
    assert verify_mod._suite_extra_flags(repo, {}, 5) == []
    assert verify_mod._suite_extra_flags(tmp_path / "no-venv", {}, 5) == []


def test_with_suite_flags_dedupes_and_skips_non_pytest() -> None:
    full = verify_mod._with_suite_flags(
        ["/venv/bin/python", "-m", "pytest", "-q", "test_x.py"],
        ["--timeout=120", "-n", "auto"],
    )
    assert full[-3:] == ["--timeout=120", "-n", "auto"]
    # Existing timeout/xdist settings are not duplicated.
    timed = verify_mod._with_suite_flags(
        ["python", "-m", "pytest", "--timeout=300", "test_x.py"],
        ["--timeout=120", "-n", "auto"],
    )
    assert "--timeout=120" not in timed
    assert timed[-2:] == ["-n", "auto"]
    parallel = verify_mod._with_suite_flags(
        ["pytest", "-n", "2", "test_x.py"], ["--timeout=120", "-n", "auto"]
    )
    assert parallel.count("-n") == 1
    assert parallel[-1] == "--timeout=120"
    # Non-pytest suite commands pass through untouched.
    probe = ["/venv/bin/python", "-c", "pass"]
    assert verify_mod._with_suite_flags(probe, ["--timeout=120", "-n", "auto"]) == probe


def test_error_reason_is_single_line_with_last_pytest_line(
    workspace_repo: tuple[Path, Path],
) -> None:
    """ERROR reasons end with the last pytest output line, not the command."""
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
    assert "\n" not in result.reason
    last = [line.strip() for line in result.log.splitlines() if line.strip()][-1]
    assert result.reason.endswith(last)


def test_f2p_fields_kept_when_suite_times_out(
    workspace_repo: tuple[Path, Path],
) -> None:
    """A hanging suite is ERROR, but Fail-to-Pass stays recorded."""
    ws, repo = workspace_repo
    base = _base(repo)
    patch = _patch_for(repo, GOOD_CALC)
    suite = [sys.executable, "-c", "import time; time.sleep(60)"]
    result = verify(
        repo, base, patch, "test_calc.py", _cmd("test_calc.py"),
        suite_cmd=suite, suite_timeout_s=2, workspace_root=ws,
    )
    assert result.verdict == "ERROR"
    assert "timed out" in result.reason
    assert result.fail_before is True
    assert result.pass_after is True
    assert result.f2p_pass is True
    assert result.suite_scope == "full"


def test_verify_f2p_timeout_is_error_with_reason(
    workspace_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bytes TimeoutExpired on the F2P run is an ERROR verdict with a reason."""
    import subprocess as _sp

    ws, repo = workspace_repo
    real_run = verify_mod.subprocess.run

    def fake(cmd: object, **kwargs: object) -> object:
        parts = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        if any("pytest" in str(p) for p in parts):
            raise _sp.TimeoutExpired(
                cmd=cmd, timeout=4, output=b"out-\xff", stderr=b"err-\xff"
            )
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(verify_mod.subprocess, "run", fake)
    result = verify(
        repo, _base(repo), None, "test_calc.py", _cmd("test_calc.py"),
        workspace_root=ws, timeout_s=4,
    )
    assert result.verdict == "ERROR"
    assert "timed out after 4 s" in result.reason


def test_patch_touched_paths_parses_git_headers() -> None:
    patch = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
        "diff --git a/test_calc.py b/test_calc.py\n"
        "--- a/test_calc.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-a\n"
        "-b\n"
        "diff --git a/conftest.py b/conftest.py\n"
        "--- /dev/null\n"
        "+++ b/conftest.py\n"
        "@@ -0,0 +1 @@\n"
        "+pass\n"
    )
    assert verify_mod._patch_touched_paths(patch) == [
        "calc.py", "test_calc.py", "conftest.py",
    ]
    assert verify_mod._patch_touched_paths("not a diff") == []


def test_integrity_violations_lists_test_and_config_paths() -> None:
    violations = verify_mod._test_integrity_violations(
        ["calc.py", "test_calc.py", "sub/conftest.py", "pytest.ini",
         "tox.ini", "setup.cfg"],
        "test_calc.py",
        "",
    )
    assert violations == [
        "pytest.ini", "setup.cfg", "sub/conftest.py", "test_calc.py", "tox.ini",
    ]
    assert verify_mod._test_integrity_violations(["calc.py"], "test_calc.py", "") == []
    assert verify_mod._test_integrity_violations([], "test_calc.py", "") == []


def test_integrity_pyproject_only_when_pytest_section_touched() -> None:
    header = "diff --git a/pyproject.toml b/pyproject.toml\n"
    addopts_patch = (
        header
        + "--- a/pyproject.toml\n"
        + "+++ b/pyproject.toml\n"
        + "@@ -1,3 +1,4 @@\n"
        + " [tool.pytest.ini_options]\n"
        + "+addopts = \"--deselect test_calc.py::test_add\"\n"
        + " x = 1\n"
    )
    assert verify_mod._test_integrity_violations(
        ["pyproject.toml"], "test_calc.py", addopts_patch
    ) == ["pyproject.toml"]
    unrelated_patch = (
        header
        + "--- a/pyproject.toml\n"
        + "+++ b/pyproject.toml\n"
        + "@@ -1,3 +1,4 @@\n"
        + " [tool.other]\n"
        + "+y = 2\n"
        + " x = 1\n"
    )
    assert verify_mod._test_integrity_violations(
        ["pyproject.toml"], "test_calc.py", unrelated_patch
    ) == []


def test_f2p_gating_helpers() -> None:
    clean = verify_mod.F2PCounts(total=1, passed=1)
    assert verify_mod._base_failed(1, None) is True
    assert verify_mod._base_failed(0, None) is False
    assert verify_mod._base_failed(1, clean) is False
    assert verify_mod._base_failed(
        1, verify_mod.F2PCounts(total=2, passed=1, failed=1)
    ) is True
    assert verify_mod._patched_clean(clean) is True
    assert verify_mod._patched_clean(verify_mod.F2PCounts()) is False
    assert verify_mod._patched_clean(
        verify_mod.F2PCounts(total=2, passed=1, skipped=1)
    ) is False
    assert verify_mod._patched_clean(
        verify_mod.F2PCounts(total=2, passed=1, failed=1)
    ) is False


def test_count_f2p_cases_from_junit(tmp_path: Path) -> None:
    xml = tmp_path / "j.xml"
    xml.write_text(
        "<testsuite>"
        '<testcase classname="m" name="a"/>'
        '<testcase classname="m" name="b"><failure message="x">t</failure></testcase>'
        '<testcase classname="m" name="c"><skipped message="s"/></testcase>'
        '<testcase classname="m" name="d"><error message="e">t</error></testcase>'
        "</testsuite>",
        encoding="utf-8",
    )
    counts = verify_mod._count_f2p_cases(xml)
    assert counts is not None
    assert (counts.total, counts.passed, counts.failed, counts.skipped) == (4, 1, 1, 1)
    assert counts.errored == 1
    assert verify_mod._count_f2p_cases(tmp_path / "missing.xml") is None


def _tracked_patch(repo: Path, mutate) -> str:
    """Apply ``mutate`` to the repo and return the working-tree diff, restored.

    The diff keeps its raw trailing context: ``strip()`` would eat a blank
    context line and corrupt the patch for ``git apply``.
    """
    import subprocess as _subprocess

    mutate(repo)
    proc = _subprocess.run(
        ["git", "diff"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    diff = proc.stdout
    _git(repo, "checkout", "--", ".")
    _git(repo, "clean", "-fdq")
    assert diff.strip()
    return diff


def _integrity_patch_variants(repo: Path) -> dict[str, str]:
    def delete_test(path: Path) -> None:
        (path / "test_calc.py").unlink()

    def skip_test(path: Path) -> None:
        target = path / "test_calc.py"
        target.write_text(
            'import pytest\npytest.skip("evade", allow_module_level=True)\n'
            + target.read_text(encoding="utf-8")
        )

    def xfail_test(path: Path) -> None:
        target = path / "test_calc.py"
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "def test_add():",
                '@pytest.mark.xfail(reason="evade", strict=False)\ndef test_add():',
                1,
            )
        )

    patches = {
        "delete-test": _tracked_patch(repo, delete_test),
        "skip-test": _tracked_patch(repo, skip_test),
        "xfail-test": _tracked_patch(repo, xfail_test),
        "conftest-hook": (
            "--- /dev/null\n"
            "+++ b/conftest.py\n"
            "@@ -0,0 +1,6 @@\n"
            "+import pytest\n"
            "+@pytest.hookimpl(hookwrapper=True)\n"
            "+def pytest_runtest_makereport(item, call):\n"
            "+    outcome = yield\n"
            "+    report = outcome.get_result()\n"
            "+    report.outcome = 'passed'\n"
        ),
        "pytest.ini": (
            "--- /dev/null\n"
            "+++ b/pytest.ini\n"
            "@@ -0,0 +1,2 @@\n"
            "+[pytest]\n"
            "+addopts = --deselect test_calc.py::test_add\n"
        ),
        "setup.cfg": (
            "--- /dev/null\n"
            "+++ b/setup.cfg\n"
            "@@ -0,0 +1,2 @@\n"
            "+[tool:pytest]\n"
            "+addopts = --deselect test_calc.py::test_add\n"
        ),
        "tox.ini": (
            "--- /dev/null\n"
            "+++ b/tox.ini\n"
            "@@ -0,0 +1,2 @@\n"
            "+[pytest]\n"
            "+addopts = --deselect test_calc.py::test_add\n"
        ),
    }
    return patches


@pytest.mark.parametrize("variant", [
    "delete-test", "skip-test", "xfail-test", "conftest-hook",
    "pytest.ini", "setup.cfg", "tox.ini",
])
def test_integrity_violation_is_unproven_without_tests(
    workspace_repo: tuple[Path, Path], variant: str
) -> None:
    """Patches touching the test or its config are UNPROVEN; nothing runs."""
    ws, repo = workspace_repo
    base = _base(repo)
    patch = _integrity_patch_variants(repo)[variant]
    result = verify(
        repo, base, patch, "test_calc.py", _cmd("test_calc.py"), workspace_root=ws
    )
    assert result.verdict == "UNPROVEN"
    assert result.reason.startswith(
        "patch modifies the regression test or test configuration: "
    )
    assert result.log == ""
    assert _git(repo, "rev-parse", "HEAD") == base
    assert _git(repo, "status", "--porcelain") == ""


def test_integrity_violation_names_pyproject_section(
    workspace_repo: tuple[Path, Path],
) -> None:
    ws, repo = workspace_repo
    base = _base(repo)
    (repo / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = \"\"\n", encoding="utf-8"
    )
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-qm", "add pytest config")
    base = _base(repo)

    def add_deselect(path: Path) -> None:
        target = path / "pyproject.toml"
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                'addopts = ""', 'addopts = "--deselect test_calc.py::test_add"'
            )
        )

    patch = _tracked_patch(repo, add_deselect)
    result = verify(
        repo, base, patch, "test_calc.py", _cmd("test_calc.py"), workspace_root=ws
    )
    assert result.verdict == "UNPROVEN"
    assert result.reason == (
        "patch modifies the regression test or test configuration: pyproject.toml"
    )
    assert result.log == ""


def test_unrelated_pyproject_change_still_verifies(
    workspace_repo: tuple[Path, Path],
) -> None:
    ws, repo = workspace_repo
    (repo / "pyproject.toml").write_text('[tool.other]\nx = 1\n', encoding="utf-8")
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-qm", "add unrelated config")
    base = _base(repo)

    def bump_both(path: Path) -> None:
        (path / "calc.py").write_text(GOOD_CALC, encoding="utf-8")
        target = path / "pyproject.toml"
        target.write_text(
            target.read_text(encoding="utf-8").replace("x = 1", "x = 2")
        )

    patch = _tracked_patch(repo, bump_both)
    result = verify(
        repo, base, patch, "test_calc.py", _cmd("test_calc.py"), workspace_root=ws
    )
    assert result.verdict == "VERIFIED"
    assert result.f2p_tests_passed >= 1
    assert result.f2p_tests_skipped == 0


def test_skip_hidden_in_source_is_rejected_after_patch(
    workspace_repo: tuple[Path, Path],
) -> None:
    """A patch that skips the test from the source file is ERROR, not VERIFIED."""
    ws, repo = workspace_repo
    base = _base(repo)

    def skip_from_source(path: Path) -> None:
        (path / "calc.py").write_text(
            "import pytest\n"
            "\n"
            "\n"
            "def add(a, b):\n"
            '    pytest.skip("evade")\n'
            "\n"
            "\n"
            "def mul(a, b):\n"
            "    return a * b\n",
            encoding="utf-8",
        )

    patch = _tracked_patch(repo, skip_from_source)
    result = verify(
        repo, base, patch, "test_calc.py", _cmd("test_calc.py"), workspace_root=ws
    )
    assert result.verdict == "ERROR"
    assert "skipped" in result.reason
    assert result.patched_returncode == 0
    assert result.f2p_tests_skipped == 1


def test_inconclusive_base_parse_is_unproven(
    workspace_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonzero base exit with no failing tests in XML is UNPROVEN."""
    ws, repo = workspace_repo
    monkeypatch.setattr(
        verify_mod,
        "_parse_f2p_junit",
        lambda *args: verify_mod.F2PCounts(total=1, passed=1),
    )
    result = verify(
        repo, _base(repo), None, "test_calc.py", _cmd("test_calc.py"), workspace_root=ws
    )
    assert result.verdict == "UNPROVEN"
    assert "no failing tests ran" in result.reason


def test_skipped_patch_parse_is_error(
    workspace_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passing exit with only skipped tests in XML is ERROR."""
    ws, repo = workspace_repo
    base = _base(repo)
    patch = _patch_for(repo, GOOD_CALC)
    calls: list[int] = []

    def fake_parse(*args: object) -> verify_mod.F2PCounts:
        calls.append(1)
        if len(calls) == 1:
            return verify_mod.F2PCounts(total=1, failed=1)
        return verify_mod.F2PCounts(total=1, skipped=1)

    monkeypatch.setattr(verify_mod, "_parse_f2p_junit", fake_parse)
    result = verify(
        repo, base, patch, "test_calc.py", _cmd("test_calc.py"), workspace_root=ws
    )
    assert result.verdict == "ERROR"
    assert "skipped" in result.reason


# -- Prompt 29.4: target pytest isolation -------------------------------------


def _evil_parent_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A configless git repo under a parent with a hostile pytest config."""
    parent = tmp_path / "parent"
    repo = parent / "repo"
    repo.mkdir(parents=True)
    (repo / "calc.py").write_text(CALC, encoding="utf-8")
    (repo / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    (parent / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = \"--no-such-flag-xyz\"\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return parent, repo


def _pytest_invocations(log: str) -> list[str]:
    """Logged ``$ ...pytest...`` command lines from a verify run."""
    return [
        line
        for line in log.splitlines()
        if line.startswith("$ ") and "pytest" in line and not line.startswith("$ git")
    ]


def test_target_pytest_ignores_parent_config(tmp_path: Path) -> None:
    """A hostile parent ``addopts`` never reaches the target pytest run."""
    parent, repo = _evil_parent_repo(tmp_path)
    base = _base(repo)
    patch = _patch_for(repo, GOOD_CALC)
    cmd = _cmd("test_calc.py")
    before = list(cmd)
    result = verify(
        repo,
        base,
        patch,
        "test_calc.py",
        cmd,
        workspace_root=parent,
        run_id="v-isolated",
        traces_dir=tmp_path / "traces",
    )
    assert result.verdict == "VERIFIED"
    assert cmd == before  # isolation is execution-time only, never stored
    calls = _pytest_invocations(result.log)
    assert len(calls) == 3  # base F2P, collect-only, patched F2P
    for line in calls:
        assert f"--rootdir={repo.resolve()}" in line
        assert "-p no:cacheprovider" in line
        assert ".issa-tmp" in line and "v-isolated" in line
        assert " -c " in line
    assert (parent.resolve() / "v-isolated" / ".issa-pytest.ini").is_file()


def test_repo_config_detection(tmp_path: Path) -> None:
    """Only the listed config shapes count as the repo's own pytest config."""
    cases = [
        ("pytest.ini", "[pytest]\n", True),
        ("pytest.toml", "[pytest]\n", True),
        (".pytest.ini", "[pytest]\n", True),
        ("pyproject.toml", "[tool.pytest.ini_options]\n", True),
        ("pyproject.toml", "[tool.other]\nx = 1\n", False),
        ("pyproject.toml", "[tool.pytest]\nx = 1\n", False),
        ("pyproject.toml", "not toml [[[\n", True),
        ("tox.ini", "[pytest]\n", True),
        ("tox.ini", "[tox]\n", False),
        ("setup.cfg", "[tool:pytest]\n", True),
        ("setup.cfg", "[other]\n", False),
    ]
    for i, (name, content, expected) in enumerate(cases):
        repo = tmp_path / f"repo-{i}"
        repo.mkdir()
        (repo / name).write_text(content, encoding="utf-8")
        assert verify_mod._repo_has_pytest_config(repo) is expected, (name, content)
    empty = tmp_path / "repo-empty"
    empty.mkdir()
    assert verify_mod._repo_has_pytest_config(empty) is False


def test_own_config_skips_generated_ini(
    workspace_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    """A repo with its own config gets rootdir/basetemp but no ``-c``."""
    ws, repo = workspace_repo
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    _git(repo, "add", "pytest.ini")
    _git(repo, "commit", "-qm", "add pytest config")
    result = verify(
        repo,
        _base(repo),
        _patch_for(repo, GOOD_CALC),
        "test_calc.py",
        _cmd("test_calc.py"),
        workspace_root=ws,
        run_id="v-own-config",
        traces_dir=tmp_path / "traces",
    )
    assert result.verdict == "VERIFIED"
    calls = _pytest_invocations(result.log)
    assert calls
    for line in calls:
        assert "--rootdir=" in line
        assert "--basetemp=" in line
        assert " -c " not in line


def test_setup_error_only_base_is_environment_error(
    workspace_repo: tuple[Path, Path],
) -> None:
    """A fail-before with no assertion run is ERROR, never Fail-to-Pass."""
    ws, repo = workspace_repo
    (repo / "test_setup_boom.py").write_text(
        "import pytest\n"
        "\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def _boom():\n"
        '    raise RuntimeError("boom-setup")\n'
        "\n"
        "\n"
        "def test_ok():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    _git(repo, "add", "test_setup_boom.py")
    _git(repo, "commit", "-qm", "add setup-error test")
    result = verify(
        repo,
        _base(repo),
        None,
        "test_setup_boom.py",
        _cmd("test_setup_boom.py"),
        workspace_root=ws,
    )
    assert result.verdict == "ERROR"
    assert "test environment incomplete" in result.reason
    assert "no test ran to an assertion" in result.reason
    assert "still fails" not in result.reason


def test_stored_test_command_has_no_isolation_flags(tmp_path: Path) -> None:
    """Stored commands never carry the execution-time isolation flags."""
    from issa_as.pipeline import _default_suite_cmd

    suite = _default_suite_cmd(tmp_path / "repo", "tests/test_x.py", None)
    assert not any(tok.startswith("--rootdir") for tok in suite)
    assert not any(tok.startswith("--basetemp") for tok in suite)
    assert "-c" not in suite
    neutral = verify_mod.neutral_test_command(
        [sys.executable, "-m", "pytest", "-q", "t.py"]
    )
    assert neutral == ["python", "-m", "pytest", "-q", "t.py"]
