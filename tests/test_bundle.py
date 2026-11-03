"""Tests for bundle.py (`issa verify-bundle`). Offline: temp upstream git
repos as clone sources plus fixture proof bundles; venv setup stubbed.
No network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from issa_as import bundle as bundle_mod
from issa_as.cli import main
from issa_as.workspace import WorkspaceError

CALC = "def add(a, b):\n    return a - b\n"
GOOD_CALC = "def add(a, b):\n    return a + b\n"
TEST_CALC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
TEST_MISC = "from calc import add\n\n\ndef test_misc():\n    assert add(0, 0) == 0\n"


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


def _upstream(tmp_path: Path) -> tuple[Path, str, str]:
    """Upstream repo at test_parent plus a fix patch for the missing test."""
    src = tmp_path / "upstream"
    src.mkdir()
    (src / "calc.py").write_text(CALC, encoding="utf-8")
    (src / "test_misc.py").write_text(TEST_MISC, encoding="utf-8")
    _git(src, "init", "-q")
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "upstream base")
    parent = _git(src, "rev-parse", "HEAD")
    (src / "calc.py").write_text(GOOD_CALC, encoding="utf-8")
    patch = _git(src, "diff")
    _git(src, "checkout", "--", "calc.py")
    assert patch
    return src, parent, patch


def _proof_bundle(
    proof_root: Path, run_id: str, parent: str, patch: str, *, test_file: str = "test_calc.py"
) -> Path:
    bundle_dir = proof_root / run_id
    (bundle_dir / "test").mkdir(parents=True)
    (bundle_dir / "test" / test_file).write_text(TEST_CALC, encoding="utf-8")
    (bundle_dir / "fix.patch").write_text(patch, encoding="utf-8")
    (bundle_dir / "verdict.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "issue_id": "o/r#1",
                "test_parent": parent,
                "test_commit": parent,
                "test_file": test_file,
                "test_command": [
                    sys.executable, "-m", "pytest", "-q",
                    "-p", "no:cacheprovider", test_file,
                ],
            }
        ),
        encoding="utf-8",
    )
    return bundle_dir


def _stub_setup(repo: Path) -> SimpleNamespace:
    return SimpleNamespace(log="stub setup (offline)\n")


def _snapshot(bundle_dir: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(bundle_dir)): p.read_bytes()
        for p in sorted(bundle_dir.rglob("*"))
        if p.is_file()
    }


def test_verify_bundle_rebuilds_and_verifies(tmp_path: Path) -> None:
    src, parent, patch = _upstream(tmp_path)
    ws = tmp_path / "workspaces"
    proof = tmp_path / "proof"
    proof.mkdir()
    run_id = "tbundle-01"
    bundle_dir = _proof_bundle(proof, run_id, parent, patch)
    before = _snapshot(bundle_dir)

    outcome = bundle_mod.verify_bundle(
        run_id,
        clone_url=str(src),
        workspaces_root=ws,
        proof_root=proof,
        setup_fn=_stub_setup,
    )
    assert outcome.verdict == "VERIFIED"
    assert "run_id=tbundle-01" in outcome.summary
    assert "verdict=VERIFIED" in outcome.summary

    result = json.loads((outcome.out_dir / "verdict.json").read_text(encoding="utf-8"))
    assert result["verdict"] == "VERIFIED"
    assert result["f2p_pass"] is True
    assert result["regressions"] == 0
    assert result["platform"]["python"] == ".".join(
        str(p) for p in sys.version_info[:3]
    )
    assert (outcome.out_dir / "verify.log").is_file()
    assert (outcome.out_dir / "install.log").read_text(
        encoding="utf-8"
    ) == "stub setup (offline)\n"

    # The test commit is a fresh local commit on top of test_parent.
    clone = ws / f"bundle-{run_id}" / "r"
    assert _git(clone, "rev-parse", "HEAD^") == parent
    assert _git(clone, "rev-parse", "HEAD") == outcome.test_commit
    assert outcome.test_commit != parent

    # The original proof bundle is never overwritten.
    assert _snapshot(bundle_dir) == before


def test_verify_bundle_refuses_missing_fields(tmp_path: Path) -> None:
    proof = tmp_path / "proof"
    bundle_dir = proof / "tbundle-02"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "verdict.json").write_text(
        json.dumps({"run_id": "tbundle-02", "issue_id": "o/r#1"}),
        encoding="utf-8",
    )
    with pytest.raises(WorkspaceError, match="missing fields"):
        bundle_mod.verify_bundle(
            "tbundle-02",
            workspaces_root=tmp_path / "workspaces",
            proof_root=proof,
            setup_fn=_stub_setup,
        )


def test_cli_exit_code_nonzero_for_non_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    src, parent, _patch = _upstream(tmp_path)
    proof = tmp_path / "proof"
    proof.mkdir()
    # An empty patch cannot fix anything: rebuild ends in ERROR.
    _proof_bundle(proof, "tbundle-03", parent, "")
    rc = main(["verify-bundle", "tbundle-03", "--clone-url", str(src)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "run_id=tbundle-03" in out and "verdict=ERROR" in out

    # Missing bundle fields are a usage error, not a verdict.
    (proof / "tbundle-04").mkdir()
    (proof / "tbundle-04" / "verdict.json").write_text("{}", encoding="utf-8")
    assert main(["verify-bundle", "tbundle-04"]) == 2


def test_cli_exit_code_zero_for_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    # Real venv setup would hit the network, so stub it at the seam the
    # CLI itself uses (workspace.setup_venv).
    monkeypatch.setattr(
        "issa_as.workspace.setup_venv",
        lambda repo_path, **kwargs: SimpleNamespace(log="stub\n"),
    )
    src, parent, patch = _upstream(tmp_path)
    proof = tmp_path / "proof"
    proof.mkdir()
    _proof_bundle(proof, "tbundle-05", parent, patch)
    rc = main(["verify-bundle", "tbundle-05", "--clone-url", str(src)])
    assert rc == 0
    assert "verdict=VERIFIED" in capsys.readouterr().out


def test_verify_bundles_workflow_is_tokenless() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github" / "workflows" / "verify-bundles.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch" in text
    assert "contents: read" in text
    assert "persist-credentials: false" in text
    assert "ubuntu-latest" in text
    assert "timeout-minutes: 45" in text
    assert "continue-on-error" in text or "continuing" in text
    assert "secrets." not in text


def test_summary_includes_reason_and_platform(tmp_path: Path) -> None:
    src, parent, _patch = _upstream(tmp_path)
    ws = tmp_path / "workspaces"
    proof = tmp_path / "proof"
    proof.mkdir()
    _proof_bundle(proof, "tsum-01", parent, "")
    outcome = bundle_mod.verify_bundle(
        "tsum-01", clone_url=str(src), workspaces_root=ws,
        proof_root=proof, setup_fn=_stub_setup,
    )
    assert outcome.verdict == "ERROR"
    assert "reason=" in outcome.summary
    assert "platform=/" not in outcome.summary
    assert "platform=" in outcome.summary
    doc = json.loads((outcome.out_dir / "verdict.json").read_text(encoding="utf-8"))
    assert doc["verdict"] == "ERROR" and doc["reason"]


def test_unexpected_exception_writes_error_verdict(tmp_path: Path) -> None:
    src, parent, patch = _upstream(tmp_path)
    ws = tmp_path / "workspaces"
    proof = tmp_path / "proof"
    proof.mkdir()
    _proof_bundle(proof, "terr-01", parent, patch)

    def bad_setup(repo: Path) -> object:
        raise RuntimeError("boom-nope")

    outcome = bundle_mod.verify_bundle(
        "terr-01", clone_url=str(src), workspaces_root=ws,
        proof_root=proof, setup_fn=bad_setup,
    )
    assert outcome.verdict == "ERROR"
    doc = json.loads((outcome.out_dir / "verdict.json").read_text(encoding="utf-8"))
    assert doc["verdict"] == "ERROR"
    assert "RuntimeError: boom-nope" in doc["reason"]
    assert "reason=" in outcome.summary


def test_suite_timeout_used_for_suite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from issa_as import verifier as verify_mod

    src, parent, patch = _upstream(tmp_path)
    ws = tmp_path / "workspaces"
    proof = tmp_path / "proof"
    proof.mkdir()
    _proof_bundle(proof, "ttime-01", parent, patch)
    seen: dict[str, object] = {}

    def fake_verify(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return verify_mod.VerifyResult("VERIFIED", "ok", "")

    monkeypatch.setattr(verify_mod, "verify", fake_verify)
    bundle_mod.verify_bundle(
        "ttime-01", clone_url=str(src), workspaces_root=ws,
        proof_root=proof, setup_fn=_stub_setup, timeout_s=1234,
    )
    assert seen.get("suite_timeout_s") == 1234
    assert seen.get("timeout_s") == verify_mod.DEFAULT_TIMEOUT_S


UNIT_OK = "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"


def _upstream_unit(tmp_path: Path) -> tuple[Path, str, str]:
    """Upstream with a passing test under ``test/unit/`` plus a root test."""
    src = tmp_path / "uunit"
    src.mkdir()
    (src / "calc.py").write_text(CALC, encoding="utf-8")
    (src / "test_misc.py").write_text(TEST_MISC, encoding="utf-8")
    unit = src / "test" / "unit"
    unit.mkdir(parents=True)
    (unit / "test_unit_ok.py").write_text(UNIT_OK, encoding="utf-8")
    _git(src, "init", "-q")
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "upstream base")
    parent = _git(src, "rev-parse", "HEAD")
    (src / "calc.py").write_text(GOOD_CALC, encoding="utf-8")
    patch = _git(src, "diff")
    _git(src, "checkout", "--", "calc.py")
    assert patch
    return src, parent, patch


def _proof_bundle_unit(
    proof_root: Path, run_id: str, parent: str, patch: str
) -> Path:
    bundle_dir = proof_root / run_id
    (bundle_dir / "test" / "test" / "unit").mkdir(parents=True)
    (bundle_dir / "test" / "test" / "unit" / "test_calc.py").write_text(
        TEST_CALC, encoding="utf-8"
    )
    (bundle_dir / "fix.patch").write_text(patch, encoding="utf-8")
    (bundle_dir / "verdict.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "issue_id": "o/r#1",
                "test_parent": parent,
                "test_commit": parent,
                "test_file": "test/unit/test_calc.py",
                "test_command": [
                    sys.executable, "-m", "pytest", "-q",
                    "-p", "no:cacheprovider", "test/unit/test_calc.py",
                ],
            }
        ),
        encoding="utf-8",
    )
    return bundle_dir


def test_windows_style_stored_command_rebuilds(tmp_path: Path) -> None:
    """A Windows interpreter path stored in the bundle verifies on POSIX."""
    src, parent, patch = _upstream(tmp_path)
    ws = tmp_path / "workspaces"
    proof = tmp_path / "proof"
    proof.mkdir()
    bundle_dir = _proof_bundle(proof, "twin-01", parent, patch)
    doc = json.loads((bundle_dir / "verdict.json").read_text(encoding="utf-8"))
    doc["test_command"] = [
        "C:\\x\\.venv\\Scripts\\python.exe", "-m", "pytest", "-q",
        "-p", "no:cacheprovider", "test_calc.py",
    ]
    (bundle_dir / "verdict.json").write_text(json.dumps(doc), encoding="utf-8")
    outcome = bundle_mod.verify_bundle(
        "twin-01", clone_url=str(src), workspaces_root=ws,
        proof_root=proof, setup_fn=_stub_setup,
    )
    assert outcome.verdict == "VERIFIED"
    out = json.loads((outcome.out_dir / "verdict.json").read_text(encoding="utf-8"))
    assert out["test_command"][0] == "python"
    assert out["suite_scope"] == "full"


def test_unit_scoping_in_verdict_and_summary(tmp_path: Path) -> None:
    src, parent, patch = _upstream_unit(tmp_path)
    ws = tmp_path / "workspaces"
    proof = tmp_path / "proof"
    proof.mkdir()
    _proof_bundle_unit(proof, "tscope-01", parent, patch)
    outcome = bundle_mod.verify_bundle(
        "tscope-01", clone_url=str(src), workspaces_root=ws,
        proof_root=proof, setup_fn=_stub_setup,
    )
    assert outcome.verdict == "VERIFIED"
    assert "suite_scope=test/unit" in outcome.summary
    out = json.loads((outcome.out_dir / "verdict.json").read_text(encoding="utf-8"))
    assert out["suite_scope"] == "test/unit"
    assert out["fail_before"] is True
    assert out["pass_after"] is True
    assert out["f2p_pass"] is True


def test_suite_path_override(tmp_path: Path) -> None:
    """``--suite-path`` rescopes the regression suite run."""
    src, parent, patch = _upstream_unit(tmp_path)
    ws = tmp_path / "workspaces"
    proof = tmp_path / "proof"
    proof.mkdir()
    _proof_bundle_unit(proof, "tscope-02", parent, patch)
    outcome = bundle_mod.verify_bundle(
        "tscope-02", clone_url=str(src), workspaces_root=ws,
        proof_root=proof, setup_fn=_stub_setup, suite_path="test",
    )
    assert outcome.verdict == "VERIFIED"
    out = json.loads((outcome.out_dir / "verdict.json").read_text(encoding="utf-8"))
    assert out["suite_scope"] == "test"
    assert "suite_scope=test" in outcome.summary


def test_verify_bundle_suite_path_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    import issa_as.workspace as workspace_mod

    monkeypatch.setattr(
        workspace_mod, "setup_venv",
        lambda repo_path, **kwargs: SimpleNamespace(log="stub\n"),
    )
    src, parent, patch = _upstream_unit(tmp_path)
    proof = tmp_path / "proof"
    proof.mkdir()
    _proof_bundle_unit(proof, "tscope-03", parent, patch)
    rc = main(["verify-bundle", "tscope-03", "--clone-url", str(src),
               "--suite-path", "test"])
    assert rc == 0
    assert "suite_scope=test" in capsys.readouterr().out


def test_reverify_suite_path_cli_flag_reaches_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import issa_as.pipeline as pipeline_mod
    from issa_as.verifier import VerifyResult

    seen: dict = {}

    def fake(run_id, *, suite_path=None, **kwargs):
        seen["run_id"] = run_id
        seen["suite_path"] = suite_path
        return VerifyResult("VERIFIED", "ok", "")

    monkeypatch.setattr(pipeline_mod, "reverify_run", fake)
    assert main(["reverify", "r1", "--suite-path", "test/unit"]) == 0
    assert seen == {"run_id": "r1", "suite_path": "test/unit"}
