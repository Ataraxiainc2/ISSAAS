"""Tests for the verifier attack harness and runs metrics (Prompt 21).

Offline: the synthetic project and the bundle clone source are throwaway
git repos under pytest tmp_path; the runs eval reads fixture files only.
Bundle venvs are built from the system interpreter plus the project's uv
cache (``UV_OFFLINE=1``) — no network. Slow live runs are marked slow.
"""

import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from issa_as import evals as evals_mod
from issa_as import traces as trace_log
from issa_as import verifier as verify_mod

CALC = "def add(a, b):\n    return a - b\n"
GOOD_CALC = "def add(a, b):\n    return a + b\n"
TEST_CALC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
TEST_EXTRA = "from calc import add\n\n\ndef test_extra():\n    assert add(0, 0) == 0\n"


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


def test_attack_specs_shape() -> None:
    specs = evals_mod.attack_specs()
    assert len(specs) == 9
    names = [spec["name"] for spec in specs]
    assert names == [
        "real-fix", "noop", "delete-test", "skip-test", "xfail-test",
        "conftest-hook", "config-deselect", "fix-plus-regression",
        "overfit-literal",
    ]
    valid = {"VERIFIED", "UNPROVEN", "NO_TEST", "REGRESSION", "ERROR"}
    for spec in specs:
        assert spec["description"]
        assert spec["expected"] in valid
        assert spec["patch"]
    overfit = next(spec for spec in specs if spec["name"] == "overfit-literal")
    assert overfit["expected"] == "VERIFIED"
    assert overfit.get("known_limit") is True


def test_synthetic_patches_cover_every_attack(tmp_path: Path) -> None:
    target = evals_mod.build_synthetic_target(tmp_path / "ws")
    patches = evals_mod.synthetic_patches(target.repo)
    assert set(patches) == {spec["patch"] for spec in evals_mod.attack_specs()}
    assert all(patch.strip() for patch in patches.values())
    assert _git(target.repo, "status", "--porcelain") == ""


def _expected_role(name: str) -> str:
    if name == "real-fix":
        return "control"
    if name == "overfit-literal":
        return "known_limit"
    return "attack"


@pytest.mark.slow
def test_synthetic_attacks_get_expected_verdicts(tmp_path: Path) -> None:
    """End to end: all nine attacks on the synthetic project (slow)."""
    ws_root = tmp_path / "wsroot"
    report = evals_mod.run_verifier_eval(
        out=tmp_path / "verifier-synthetic.json", workspace_root=ws_root
    )
    assert report["target"] == "synthetic"
    assert report["total"] == 9
    assert report["passed"] == 9
    assert report["attacks_rejected"] == 7
    assert report["attacks_total"] == 7
    assert report["controls_ok"] is True
    assert report["known_limits"] == 1
    assert report["summary"] == (
        "7/7 attacks rejected · real fix accepted · 1 known limit (overfitting)"
    )
    by_name = {attack["name"]: attack for attack in report["attacks"]}
    seconds = []
    for spec in evals_mod.attack_specs():
        attack = by_name[spec["name"]]
        assert attack["expected"] == spec["expected"]
        assert attack["got"] == spec["expected"]
        assert attack["ok"] is True
        assert attack["role"] == _expected_role(spec["name"])
        assert isinstance(attack["seconds"], float)
        assert attack["seconds"] >= 0.0
        seconds.append(attack["seconds"])
        assert set(attack) >= {
            "name", "description", "expected", "got", "ok",
            "known_limit", "role", "seconds",
        }
    assert report["total_seconds"] == round(sum(seconds), 2)
    assert report["median_seconds"] == round(statistics.median(seconds), 2)
    assert list(ws_root.glob("eval-*")) == []
    written = json.loads((tmp_path / "verifier-synthetic.json").read_text())
    assert written["summary"] == report["summary"]


def _fake_verify_factory(verdicts: list[str]):
    """Stub for ``verifier.verify`` returning canned verdicts in order."""
    calls: list[dict] = []

    def fake_verify(repo_path, base_commit, patch_text, *args, **kwargs):
        calls.append(
            {"repo": Path(repo_path), "kwargs": kwargs, "patch": patch_text}
        )
        verdict = verdicts.pop(0) if verdicts else "ERROR"
        return verify_mod.VerifyResult(verdict, "fake")

    fake_verify.calls = calls  # type: ignore[attr-defined]
    return fake_verify


def _block_system_temp(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args, **kwargs):
        raise AssertionError("system temp folder must not be used")

    monkeypatch.setattr(tempfile, "mkdtemp", _boom)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _boom)


def test_run_verifier_eval_stays_in_workspace_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scratch lives under the given root (never system temp) and is removed."""
    _block_system_temp(monkeypatch)
    fake = _fake_verify_factory(["VERIFIED"] * 9)
    monkeypatch.setattr(verify_mod, "verify", fake)
    ws_root = tmp_path / "wsroot"
    report = evals_mod.run_verifier_eval(
        out=tmp_path / "report.json", workspace_root=ws_root
    )
    assert report["total"] == 9
    assert report["summary"] == (
        "0/7 attacks rejected · real fix accepted · 1 known limit (overfitting)"
    )
    assert report["attacks_rejected"] == 0
    assert report["attacks_total"] == 7
    assert report["controls_ok"] is True
    assert report["known_limits"] == 1
    assert len(fake.calls) == 9  # type: ignore[attr-defined]
    for call in fake.calls:  # type: ignore[attr-defined]
        assert call["repo"].resolve().is_relative_to(ws_root.resolve())
        roots = call["kwargs"]["workspace_root"]
        assert Path(roots).resolve().is_relative_to(ws_root.resolve())
    assert list(ws_root.glob("eval-*")) == []


def test_run_verifier_eval_keep_preserves_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``keep=True`` leaves the ``eval-<label>-<timestamp>`` dir behind."""
    _block_system_temp(monkeypatch)
    monkeypatch.setattr(verify_mod, "verify", _fake_verify_factory(["VERIFIED"] * 9))
    ws_root = tmp_path / "wsroot"
    evals_mod.run_verifier_eval(
        out=tmp_path / "report.json", workspace_root=ws_root, keep=True
    )
    leftovers = list(ws_root.glob("eval-synthetic-*"))
    assert len(leftovers) == 1
    assert (leftovers[0] / "synth-proj").is_dir()


def test_verifier_eval_roles_summary_and_timings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All-ok run: roles, honest summary text, and timing fields."""
    _block_system_temp(monkeypatch)
    verdicts = [spec["expected"] for spec in evals_mod.attack_specs()]
    monkeypatch.setattr(verify_mod, "verify", _fake_verify_factory(verdicts))
    report = evals_mod.run_verifier_eval(
        out=tmp_path / "report.json", workspace_root=tmp_path / "wsroot"
    )
    assert report["passed"] == 9
    assert report["attacks_rejected"] == 7
    assert report["attacks_total"] == 7
    assert report["controls_ok"] is True
    assert report["known_limits"] == 1
    assert report["summary"] == (
        "7/7 attacks rejected · real fix accepted · 1 known limit (overfitting)"
    )
    by_name = {attack["name"]: attack for attack in report["attacks"]}
    for spec in evals_mod.attack_specs():
        assert by_name[spec["name"]]["role"] == _expected_role(spec["name"])
    seconds = [attack["seconds"] for attack in report["attacks"]]
    assert all(isinstance(value, float) for value in seconds)
    assert report["total_seconds"] == round(sum(seconds), 2)
    assert report["median_seconds"] == round(statistics.median(seconds), 2)


def _fixture_bundle(tmp_path: Path, run_id: str = "rbundle-01") -> tuple[Path, str]:
    """Local upstream repo plus a committed proof bundle fixture (offline)."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "calc.py").write_text(CALC, encoding="utf-8")
    (upstream / "test_extra.py").write_text(TEST_EXTRA, encoding="utf-8")
    _git(upstream, "init", "-q")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-qm", "upstream base")
    parent = _git(upstream, "rev-parse", "HEAD")
    (upstream / "calc.py").write_text(GOOD_CALC, encoding="utf-8")
    patch = _git(upstream, "diff")
    _git(upstream, "checkout", "--", "calc.py")

    proof_root = tmp_path / "proof"
    bundle_dir = proof_root / run_id
    (bundle_dir / "test").mkdir(parents=True)
    (bundle_dir / "test" / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    (bundle_dir / "fix.patch").write_text(patch, encoding="utf-8")
    (bundle_dir / "verdict.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "issue_id": "o/r#1",
                "test_parent": parent,
                "test_commit": parent,
                "test_file": "test_calc.py",
                "test_command": [
                    sys.executable, "-m", "pytest", "-q",
                    "-p", "no:cacheprovider", "test_calc.py",
                ],
            }
        ),
        encoding="utf-8",
    )
    return proof_root, parent


def _offline_venv_setup(repo: Path) -> SimpleNamespace:
    """Build a real isolated venv offline (system python + cached pytest).

    Mirrors what ``workspace.setup_venv`` would install for this fixture
    (just pytest), but with ``UV_OFFLINE=1`` and the project's uv cache
    so tests never touch the network.
    """
    from issa_as import workspace as ws_mod

    uv = ws_mod.find_uv_bin()
    env = dict(os.environ)
    env["UV_OFFLINE"] = "1"
    env["UV_CACHE_DIR"] = str(evals_mod.PROJECT_ROOT / ".cache" / "uv")
    created = subprocess.run(
        [uv, "venv", ".venv", "--allow-existing"],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert created.returncode == 0, created.stderr[-500:]
    if os.name == "nt":
        python = repo / ".venv" / "Scripts" / "python.exe"
    else:
        python = repo / ".venv" / "bin" / "python"
    installed = subprocess.run(
        [uv, "pip", "install", "--offline", "--python", str(python), "pytest"],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert installed.returncode == 0, installed.stderr[-500:]
    return SimpleNamespace(log="offline venv stub\n")


def _target_venv_python(repo: Path) -> Path:
    if os.name == "nt":
        return repo / ".venv" / "Scripts" / "python.exe"
    return repo / ".venv" / "bin" / "python"


def test_bundle_target_materializes_and_fix_verifies(tmp_path: Path) -> None:
    proof_root, parent = _fixture_bundle(tmp_path)
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    target = evals_mod.materialize_bundle_target(
        ws_root, "rbundle-01", proof_root=proof_root,
        clone_url=str(tmp_path / "upstream"),
        setup_fn=_offline_venv_setup,
    )
    assert target.base_commit != parent
    assert target.baseline_commit == parent
    assert (target.repo / "test_calc.py").is_file()
    venv_python = _target_venv_python(target.repo)
    assert venv_python.is_file()
    assert Path(target.test_cmd[0]).resolve() == venv_python.resolve()
    assert Path(target.test_cmd[0]).resolve() != Path(sys.executable).resolve()
    bundled = evals_mod.bundle_patches(target, target.fix_patch)
    assert set(bundled.patches) == {
        spec["patch"] for spec in evals_mod.attack_specs()
    }

    fixed = verify_mod.verify(
        target.repo, target.base_commit, bundled.patches["fix"],
        target.test_file, target.test_cmd, suite_cmd=target.suite_cmd,
        baseline_commit=target.baseline_commit, workspace_root=target.workspace_root,
    )
    assert fixed.verdict == "VERIFIED"
    skipped = verify_mod.verify(
        target.repo, target.base_commit, bundled.patches["skip-test"],
        target.test_file, target.test_cmd, workspace_root=target.workspace_root,
    )
    assert skipped.verdict == "UNPROVEN"
    assert "test configuration" in skipped.reason or "regression test" in skipped.reason


def test_materialize_bundle_defaults_to_bundle_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``setup_fn`` the bundle's own setup code provisions the venv."""
    from issa_as import bundle as bundle_mod

    proof_root, _ = _fixture_bundle(tmp_path, run_id="bbundle-01")
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    seen: dict[str, Path] = {}

    def fake_default_setup(repo, workspaces_root=None):
        seen["repo"] = Path(repo)
        seen["root"] = Path(workspaces_root)
        venv_py = _target_venv_python(Path(repo))
        venv_py.parent.mkdir(parents=True, exist_ok=True)
        venv_py.write_bytes(b"fake-venv-python")
        return SimpleNamespace(log="fake setup\n")

    monkeypatch.setattr(bundle_mod, "_default_setup", fake_default_setup)
    target = evals_mod.materialize_bundle_target(
        ws_root, "bbundle-01", proof_root=proof_root,
        clone_url=str(tmp_path / "upstream"),
    )
    assert seen["repo"] == target.repo
    assert seen["root"] == ws_root
    venv_py = _target_venv_python(target.repo)
    assert venv_py.is_file()
    assert Path(target.test_cmd[0]).resolve() == venv_py.resolve()
    assert Path(target.test_cmd[0]).resolve() != Path(sys.executable).resolve()


def test_materialize_bundle_setup_failure_is_eval_error(tmp_path: Path) -> None:
    """A broken venv setup fails the harness cleanly (no traceback exit)."""
    proof_root, _ = _fixture_bundle(tmp_path, run_id="ebundle-01")
    ws_root = tmp_path / "ws"
    ws_root.mkdir()

    def bad_setup(repo: Path) -> SimpleNamespace:
        raise RuntimeError("no uv here")

    with pytest.raises(evals_mod.EvalError, match="test environment setup failed"):
        evals_mod.materialize_bundle_target(
            ws_root, "ebundle-01", proof_root=proof_root,
            clone_url=str(tmp_path / "upstream"), setup_fn=bad_setup,
        )


def test_runs_eval_on_fixture_files(tmp_path: Path) -> None:
    proof = tmp_path / "proof"
    traces_dir = tmp_path / "traces"
    triage_dir = tmp_path / "triage"
    (proof / "r-a").mkdir(parents=True)
    (proof / "r-a" / "verdict.json").write_text(
        json.dumps({"verdict": "VERIFIED", "reason": "x"}), encoding="utf-8"
    )
    (proof / "r-a" / "linux-verify.json").write_text(
        json.dumps({"verdict": "ERROR", "reason": "linux says otherwise"}),
        encoding="utf-8",
    )
    (proof / "r-a" / "cost.json").write_text(
        json.dumps({"cost_usd": 0.5}), encoding="utf-8"
    )
    (proof / "r-a" / "issue.json").write_text(
        json.dumps({"issue_url": "https://github.com/o/r/issues/1"}), encoding="utf-8"
    )
    (proof / "r-b").mkdir(parents=True)
    (proof / "r-b" / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": "VERIFIED",
                "issue_url": "https://github.com/o/r/issues/2",
            }
        ),
        encoding="utf-8",
    )
    (proof / "r-b" / "cost.json").write_text(
        json.dumps({"cost_usd": 0.25}), encoding="utf-8"
    )
    (proof / "r-c").mkdir(parents=True)
    (proof / "r-c" / "cost.json").write_text(
        json.dumps({"cost_usd": None}), encoding="utf-8"
    )
    (proof / "r-c" / "issue.json").write_text(
        json.dumps({"issue_url": "https://github.com/o/r/issues/3"}), encoding="utf-8"
    )
    traces_dir.mkdir(parents=True)
    trace_log.append("r-c", {"type": "run_started", "stage": "run"}, traces_dir)
    trace_log.append(
        "r-c",
        {
            "type": "verify_verdict",
            "stage": "verifier",
            "verdict": "REGRESSION",
            "reason": "r",
            "f2p_pass": True,
            "regressions": 1,
            "log": "",
        },
        traces_dir,
    )
    triage_dir.mkdir(parents=True)
    (triage_dir / "t1.json").write_text(
        json.dumps(
            {
                "run_id": "t1",
                "scout_run_id": "s1",
                "scored": [
                    {"issue_url": "https://github.com/o/r/issues/2", "is_real_bug": True},
                    {"issue_url": "https://github.com/o/r/issues/9", "is_real_bug": False},
                    {"issue_url": "https://github.com/o/r/issues/10"},
                ],
                "selection": {
                    "picked": {"issue_url": "https://github.com/o/r/issues/2"},
                    "skipped": [],
                },
            }
        ),
        encoding="utf-8",
    )

    out = tmp_path / "runs.json"
    metrics = evals_mod.run_runs_eval(out=out, root=tmp_path)
    assert metrics["n_runs"] == 3
    assert metrics["verdict_counts"] == {"ERROR": 1, "VERIFIED": 1, "REGRESSION": 1}
    assert metrics["n_verified"] == 1
    assert metrics["verified_rate"] == pytest.approx(1 / 3)
    assert metrics["total_solver_usd"] == pytest.approx(0.75)
    assert metrics["cost_per_verified_fix_usd"] == pytest.approx(0.75)
    by_id = {run["run_id"]: run for run in metrics["runs"]}
    assert by_id["r-a"]["verdict"] == "ERROR"
    assert by_id["r-a"]["verdict_source"] == "linux"
    assert by_id["r-b"]["verdict_source"] == "proof"
    assert by_id["r-c"]["verdict"] == "REGRESSION"
    assert by_id["r-c"]["verdict_source"] == "trace"
    assert by_id["r-c"]["cost_usd"] == 0.0
    assert metrics["triage"]["n_reports"] == 1
    assert metrics["triage"]["scored"] == 3
    assert metrics["triage"]["judged_not_real"] == 1
    assert metrics["triage"]["n_picked"] == 1
    (pick,) = metrics["triage"]["picks"]
    assert pick["issue_url"] == "https://github.com/o/r/issues/2"
    assert pick["outcome"] == "VERIFIED"
    assert json.loads(out.read_text(encoding="utf-8"))["n_runs"] == 3


def test_cli_eval_runs_and_verifier_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from issa_as.cli import main

    (tmp_path / "proof").mkdir()
    (tmp_path / "traces").mkdir()
    (tmp_path / "triage").mkdir()
    monkeypatch.chdir(tmp_path)
    assert main(["eval", "runs"]) == 0
    assert "issa eval runs: runs=0" in capsys.readouterr().out
    assert (tmp_path / "evals" / "results" / "runs.json").is_file()

    assert main(["eval", "verifier", "--bundle", "missing-run"]) == 2
    assert "proof bundle not found" in capsys.readouterr().out


def test_cli_eval_verifier_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Exit 0 when every record is ok, 1 when any record has ok: false."""
    from issa_as.cli import main

    seen: dict = {}

    def fake_ok(**kwargs):
        seen.update(kwargs)
        return {
            "summary": "7/7 attacks rejected",
            "passed": 1,
            "total": 1,
            "attacks": [{"ok": True}],
        }

    monkeypatch.setattr(evals_mod, "run_verifier_eval", fake_ok)
    monkeypatch.chdir(tmp_path)
    assert main(["eval", "verifier"]) == 0
    assert seen.get("keep") is False
    assert "7/7 attacks rejected" in capsys.readouterr().out
    assert main(["eval", "verifier", "--keep"]) == 0
    assert seen.get("keep") is True

    def fake_fail(**kwargs):
        return {
            "summary": "6/7 attacks rejected",
            "passed": 0,
            "total": 1,
            "attacks": [{"ok": False}],
        }

    monkeypatch.setattr(evals_mod, "run_verifier_eval", fake_fail)
    assert main(["eval", "verifier"]) == 1
