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
