"""Rebuild a proof bundle from ``proof/<run_id>/`` alone (Prompt 17).

``issa verify-bundle <run_id>`` answers the question "can anyone check this
fix on a clean Linux machine, from the committed repo alone, without Muse
and without keys?" It never touches the original ``proof/<run_id>/`` files:

1. clone ``https://github.com/<owner>/<repo>.git`` (owner/repo parsed from
   the bundle's ``issue_id``) into ``workspaces/bundle-<run_id>/`` and check
   out ``test_parent`` — the public upstream commit;
2. copy the committed test file (``proof/<run_id>/test/<test_file>``) into
   place and commit it locally with a fixed ``git -c user.name=…`` /
   ``-c user.email=…`` author (global git config is never touched) — that
   commit is the new test commit;
3. set up the venv with the Prompt 16 dependency logic
   (:func:`workspace.setup_venv`) and run the existing verifier with
   ``fix.patch`` (Fail-to-Pass on the test file, suite regressions measured
   against the baseline on ``test_parent``);
4. write ``verdict.json``, ``verify.log`` and ``install.log`` plus a
   ``platform`` field (OS, Python version) to ``--out`` (default
   ``workspaces/bundle-<run_id>/result/``).

No network except the clone; no Muse; no keys. Clone source and venv setup
are injectable so tests stay offline (a local path as the clone source, a
stub setup function).
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from issa_as import verifier as verify_mod
from issa_as import workspace as ws
from issa_as.workspace import WorkspaceError

BUNDLE_PREFIX = "bundle-"
GIT_AUTHOR_NAME = "ISSA AS verify-bundle"
GIT_AUTHOR_EMAIL = "issa-as@example.com"

_REQUIRED_FIELDS = ("issue_id", "test_parent", "test_file", "test_command")


@dataclass
class BundleOutcome:
    """Outcome of one ``verify-bundle`` rebuild."""

    run_id: str
    verdict: str
    out_dir: Path
    test_commit: str = ""
    summary: str = ""


def _decode_chunk(value: bytes | str | None) -> str:
    """Decode a subprocess output chunk that may be bytes, str, or None."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_git(repo: Path, *args: str, timeout_s: float = 120.0) -> str:
    """Run a git command in ``repo``; raise WorkspaceError on failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        _decode_chunk(exc.stdout)
        _decode_chunk(exc.stderr)
        raise WorkspaceError(
            f"git {' '.join(args)} timed out after {timeout_s:g} s"
        ) from exc
    except OSError as exc:
        raise WorkspaceError(f"git {' '.join(args)} failed: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr.strip() or proc.stdout.strip())[-500:]
        raise WorkspaceError(f"git {' '.join(args)} failed: {tail}")
    return proc.stdout.strip()


def _parse_issue_id(issue_id: str) -> tuple[str, str]:
    """Split ``owner/repo#number`` into (owner, repo)."""
    text = (issue_id or "").strip()
    head, sep, number = text.rpartition("#")
    if not sep or not number.isdigit() or "/" not in head:
        raise WorkspaceError(f"bad issue_id in bundle: {issue_id!r}")
    owner, _, repo = head.rpartition("/")
    if not owner or not repo or any(c in owner + repo for c in " \t\n"):
        raise WorkspaceError(f"bad issue_id in bundle: {issue_id!r}")
    return owner, repo


def _default_setup(repo: Path, workspaces_root: Path) -> Any:
    return ws.setup_venv(repo, workspaces_root=workspaces_root)


def _summary_line(
    run_id: str,
    verdict: str,
    f2p: bool,
    regressions: int,
    baseline_failures: int,
    reason: str = "",
    platform_tag: str = "",
    suite_scope: str = "full",
) -> str:
    first_line = (reason or "").splitlines()[0].strip() if (reason or "").strip() else ""
    tag = platform_tag or f"{platform.system()}/{platform.python_version()}"
    return (
        f"issa verify-bundle: run_id={run_id} verdict={verdict} "
        f"f2p={str(f2p).lower()} regressions={regressions} "
        f"baseline_failures={baseline_failures} "
        f"suite_scope={suite_scope} "
        f"reason={first_line} platform={tag}"
    )


def verify_bundle(
    run_id: str,
    *,
    out_dir: Path | str | None = None,
    clone_url: str | None = None,
    workspaces_root: Path | str = ws.DEFAULT_WORKSPACE_ROOT,
    proof_root: Path | str = "proof",
    setup_fn: Callable[[Path], Any] | None = None,
    timeout_s: float = verify_mod.DEFAULT_SUITE_TIMEOUT_S,
    suite_path: Path | str | None = None,
) -> BundleOutcome:
    """Rebuild the run stored in ``proof/<run_id>/`` and verify it fresh.

    ``timeout_s`` bounds the suite runs; the per-test Fail-to-Pass runs keep
    the shorter :data:`verifier.DEFAULT_TIMEOUT_S` limit. The stored test
    command is rebuilt for this machine's venv (never reused verbatim) and
    ``suite_path`` scopes the regression suite (explicit path wins, else
    the ``unit`` directory rule, else the full suite).

    Raises :class:`WorkspaceError` when the bundle is missing or lacks the
    fields a rebuild needs (``issue_id``, ``test_parent``, ``test_file``,
    ``test_command``); anything that goes wrong after the clone becomes an
    ``ERROR`` verdict written to ``out_dir`` instead, including unexpected
    exceptions (recorded as ``"<Type>: <message>"``).
    """
    ws.validate_run_id(run_id)
    root = Path(workspaces_root)
    bundle_dir = Path(proof_root) / run_id
    verdict_path = bundle_dir / "verdict.json"
    if not verdict_path.is_file():
        raise WorkspaceError(f"proof bundle not found for run: {run_id}")
    try:
        doc = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceError(f"cannot read verdict for run {run_id}: {exc}") from exc
    if not isinstance(doc, dict):
        raise WorkspaceError(f"cannot read verdict for run {run_id}: not an object")
    missing = [name for name in _REQUIRED_FIELDS if not doc.get(name)]
    if missing:
        raise WorkspaceError(
            f"bundle for run {run_id} is missing fields: {', '.join(missing)}"
        )
    issue_id = str(doc["issue_id"])
    test_parent = str(doc["test_parent"])
    test_file = str(doc["test_file"])
    test_command = [str(p) for p in doc["test_command"]]
    if not test_command:
        raise WorkspaceError(f"bundle for run {run_id} has an empty test_command")

    owner, repo_name = _parse_issue_id(issue_id)
    source = (clone_url or "").strip() or f"https://github.com/{owner}/{repo_name}.git"

    out = Path(out_dir) if out_dir is not None else root / f"{BUNDLE_PREFIX}{run_id}" / "result"
    try:
        if out.resolve() == bundle_dir.resolve() or bundle_dir.resolve() in out.resolve().parents:
            raise WorkspaceError(f"refusing to write results into proof bundle: {out}")
    except OSError as exc:
        raise WorkspaceError(f"bad --out directory: {exc}") from exc

    dest = ws.repo_dir(f"{BUNDLE_PREFIX}{run_id}", repo_name, root)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["git", "clone", source, str(dest)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300.0,
        )
    except subprocess.TimeoutExpired as exc:
        _decode_chunk(exc.stdout)
        _decode_chunk(exc.stderr)
        raise WorkspaceError(
            f"git clone timed out after {300.0:g} s for run {run_id}"
        ) from exc
    except OSError as exc:
        raise WorkspaceError(f"git clone failed for run {run_id}: {exc}") from exc
    if proc.returncode != 0:
        raise WorkspaceError(f"git clone failed for run {run_id}: {proc.stderr.strip()[-500:]}")

    install_log = ""
    reason = ""
    verdict = "ERROR"
    result = verify_mod.VerifyResult("ERROR", "rebuild did not reach verification")
    new_test_commit = ""
    try:
        _run_git(dest, "checkout", "-q", test_parent)
        committed = bundle_dir / "test" / test_file
        if not committed.is_file():
            raise WorkspaceError(
                f"bundle for run {run_id} has no committed test file: test/{test_file}"
            )
        target = dest / test_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(committed.read_bytes())
        _run_git(dest, "add", "--", test_file)
        _run_git(
            dest,
            "-c",
            f"user.name={GIT_AUTHOR_NAME}",
            "-c",
            f"user.email={GIT_AUTHOR_EMAIL}",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            f"issa verify-bundle: regression test for {run_id}",
            "--",
            test_file,
        )
        new_test_commit = _run_git(dest, "rev-parse", "HEAD")

        setup = setup_fn or (lambda repo: _default_setup(repo, root))
        try:
            venv_info = setup(dest)
            install_log = str(getattr(venv_info, "log", "") or "")
        except WorkspaceError as exc:
            install_log = (getattr(exc, "log", "") or "") + f"\nsetup failed: {exc}\n"
            raise WorkspaceError(f"test environment incomplete: {exc}") from exc

        patch_file = bundle_dir / "fix.patch"
        try:
            patch_text = patch_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorkspaceError(
                f"cannot read fix.patch for run {run_id}: {exc}"
            ) from exc
        if not patch_text.strip():
            raise WorkspaceError(f"fix.patch for run {run_id} is empty")

        from issa_as.pipeline import _default_suite_cmd

        portable_cmd = verify_mod.portable_test_command(test_command, dest)
        result = verify_mod.verify(
            dest,
            new_test_commit,
            patch_text,
            test_file,
            portable_cmd,
            suite_cmd=_default_suite_cmd(dest, test_file, suite_path),
            baseline_commit=test_parent,
            timeout_s=verify_mod.DEFAULT_TIMEOUT_S,
            suite_timeout_s=timeout_s,
            workspace_root=root,
            suite_path=suite_path,
        )
        verdict = result.verdict
        reason = result.reason
    except WorkspaceError as exc:
        verdict = "ERROR"
        reason = str(exc)
        result = verify_mod.VerifyResult("ERROR", reason, result.log if result else "")
    except Exception as exc:  # noqa: BLE001 — unexpected failures are verdicts, not crashes
        verdict = "ERROR"
        reason = f"{type(exc).__name__}: {exc}"
        try:
            prior_log = result.log if result else ""
        except Exception:
            prior_log = ""
        result = verify_mod.VerifyResult("ERROR", reason, prior_log)

    out.mkdir(parents=True, exist_ok=True)
    (out / "install.log").write_text(install_log, encoding="utf-8")
    (out / "verify.log").write_text(result.log, encoding="utf-8")
    verdict_doc = {
        "run_id": run_id,
        "issue_id": issue_id,
        "verdict": verdict,
        "reason": reason,
        "base_commit": new_test_commit,
        "test_parent": test_parent,
        "test_commit": new_test_commit,
        "test_file": test_file,
        "test_command": verify_mod.neutral_test_command(test_command),
        "fail_before": result.fail_before,
        "pass_after": result.pass_after,
        "f2p_pass": result.f2p_pass,
        "suite_scope": result.suite_scope,
        "regressions": result.regressions,
        "baseline_failures": list(result.baseline_failures),
        "rebuilt_from": f"proof/{run_id}/",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
        },
    }
    (out / "verdict.json").write_text(json.dumps(verdict_doc, indent=2), encoding="utf-8")
    summary = _summary_line(
        run_id, verdict, result.f2p_pass, result.regressions,
        len(result.baseline_failures), reason,
        suite_scope=result.suite_scope,
    )
    return BundleOutcome(
        run_id=run_id, verdict=verdict, out_dir=out,
        test_commit=new_test_commit, summary=summary,
    )


__all__ = ["BundleOutcome", "verify_bundle"]
