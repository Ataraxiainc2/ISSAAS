"""Deterministic Fail-to-Pass gate. Plain Python — no LLM calls.

Given a repo under ``workspaces/``, a base commit, a unified-diff patch, a
test file and the commands to run it, returns one of ``VERIFIED`` (the test
fails on base and passes with the patch, with no regressions), ``UNPROVEN``
(the test already passes on base), ``NO_TEST`` (no test file), ``REGRESSION``
(the patch breaks pre-existing tests) or ``ERROR`` (anything else).

Every command runs in a sanitized subprocess: secret-like variables are
stripped from the environment, ``cwd``/``TMP``/``TEMP`` stay inside the
workspace, and each command has a hard timeout. The working tree is restored
to its original commit before returning. The verdict is appended as a
``verify_verdict`` trace event when ``run_id`` is given.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from issa_as import traces as trace_log

Verdict = Literal["VERIFIED", "UNPROVEN", "NO_TEST", "REGRESSION", "ERROR"]

DEFAULT_WORKSPACE_ROOT = Path("workspaces")
DEFAULT_TIMEOUT_S = 120.0
MAX_LOG_CHARS = 20_000
EVENT_LOG_CHARS = 4_000

_SECRET_MARKERS = (
    "SECRET",
    "TOKEN",
    "KEY",
    "PASSWORD",
    "AUTH",
    "CREDENTIAL",
    "PRIVATE",
    "GITHUB",
    "SPARK_OPEN",
    "MODEL_API",
)
_KNOWN_SECRETS = ("MODEL_API_KEY", "SPARK_OPEN_API_KEY", "GITHUB_TOKEN")


@dataclass
class VerifyResult:
    """Outcome of one verification run, with captured logs."""

    verdict: Verdict
    reason: str = ""
    log: str = ""
    base_returncode: int | None = None
    patched_returncode: int | None = None
    regressions: int = 0


def _sanitized_env(tmpdir: Path) -> dict[str, str]:
    """Copy of ``os.environ`` without secrets and with local temp dirs."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in _SECRET_MARKERS)
    }
    for name in _KNOWN_SECRETS:
        env.pop(name, None)
    env["TMP"] = str(tmpdir)
    env["TEMP"] = str(tmpdir)
    env["TMPDIR"] = str(tmpdir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(
    cmd: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float
) -> tuple[int | None, str]:
    """Run a command without a shell; ``None`` returncode means timeout/start failure."""
    header = f"$ {' '.join(cmd)}"
    try:
        proc = subprocess.run(
            list(cmd), cwd=cwd, env=dict(env), capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        return None, f"{header}\nTIMEOUT after {timeout_s}s\n{out}"
    except OSError as exc:
        return None, f"{header}\nFAILED TO START: {exc}"
    return proc.returncode, f"{header}\n(exit {proc.returncode})\n{proc.stdout}{proc.stderr}"


def _git_out(
    repo: Path, env: Mapping[str, str], timeout_s: float, *args: str
) -> str | None:
    """Stdout of a plumbing git command, or None on any failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def verify(
    repo_path: Path | str,
    base_commit: str,
    patch_text: str | None,
    test_file: Path | str | None,
    test_cmd: Sequence[str],
    suite_cmd: Sequence[str] | None = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    run_id: str | None = None,
    issue_id: str | None = None,
    traces_dir: Path | None = None,
) -> VerifyResult:
    """Fail-to-Pass check of a patch inside a workspace git repo."""
    logs: list[str] = []
    env: dict[str, str] = {}
    tmpdir: Path | None = None
    orig_ref = ""
    initial_untracked: set[str] = set()

    def finish(result: VerifyResult) -> VerifyResult:
        if len(result.log) > MAX_LOG_CHARS:
            result.log = "...[truncated]...\n" + result.log[-MAX_LOG_CHARS:]
        if run_id is not None:
            trace_log.append(
                run_id,
                trace_log.VerifyVerdict(
                    run_id=run_id,
                    issue_id=issue_id,
                    verdict=result.verdict,
                    reason=result.reason,
                    f2p_pass=result.verdict == "VERIFIED",
                    regressions=result.regressions,
                    log=result.log[-EVENT_LOG_CHARS:],
                ),
                traces_dir,
            )
        return result

    def error(reason: str) -> VerifyResult:
        return finish(VerifyResult("ERROR", reason, "".join(logs)))

    try:
        repo = Path(repo_path).resolve()
        workspace = Path(workspace_root).resolve()
        if workspace != repo and workspace not in repo.parents:
            return error(f"repo path escapes workspace: {repo}")
        if not repo.is_dir():
            return error(f"repo not found: {repo}")
        if not test_cmd:
            return error("empty test command")
        tmpdir = Path(tempfile.mkdtemp(prefix="issa-verify-", dir=workspace))
        env = _sanitized_env(tmpdir)

        if _git_out(repo, env, timeout_s, "rev-parse", "--git-dir") is None:
            return error(f"not a git repo: {repo}")
        if _git_out(repo, env, timeout_s, "cat-file", "-e", base_commit) is None:
            return error(f"unknown base commit: {base_commit}")

        branch = _git_out(repo, env, timeout_s, "symbolic-ref", "-q", "HEAD")
        if branch is not None and branch.startswith("refs/heads/"):
            orig_ref = branch
        else:
            orig_ref = _git_out(repo, env, timeout_s, "rev-parse", "HEAD") or ""
        if not orig_ref:
            return error("cannot determine current HEAD")

        dirty = _git_out(repo, env, timeout_s, "status", "--porcelain", "--untracked-files=no")
        if dirty is None:
            return error("cannot read git status")
        if dirty:
            return error("refusing to run on a dirty repo")

        listed = _git_out(repo, env, timeout_s, "ls-files", "--others", "--exclude-standard")
        initial_untracked = set((listed or "").splitlines())

        if not test_file or not str(test_file).strip():
            return finish(VerifyResult("NO_TEST", "no test file given", "".join(logs)))
        test_abs = (repo / str(test_file)).resolve()
        if test_abs != repo and repo not in test_abs.parents:
            return error(f"test path escapes repo: {test_file}")

        rc, out = _run(["git", "checkout", "-q", base_commit], repo, env, timeout_s)
        logs.append(out)
        if rc != 0:
            return error(f"cannot checkout base commit: {base_commit}")

        base_exists = test_abs.is_file()
        base_rc: int | None = None
        if base_exists:
            base_rc, out = _run(test_cmd, repo, env, timeout_s)
            logs.append(out)
            if base_rc is None:
                return error(f"test command did not complete on base: {out[-500:]}")

        base_suite_rc: int | None = None
        if suite_cmd is not None:
            base_suite_rc, out = _run(suite_cmd, repo, env, timeout_s)
            logs.append(out)
            if base_suite_rc is None:
                return error(f"suite command did not complete on base: {out[-500:]}")

        if patch_text:
            patch_file = tmpdir / "fix.patch"
            patch_file.write_text(
                patch_text if patch_text.endswith("\n") else patch_text + "\n",
                encoding="utf-8",
            )
            rc, out = _run(["git", "apply", str(patch_file)], repo, env, timeout_s)
            logs.append(out)
            if rc != 0:
                return error("patch does not apply")

        patched_exists = test_abs.is_file()
        if not base_exists and not patched_exists:
            return finish(
                VerifyResult("NO_TEST", f"test file not found: {test_file}", "".join(logs))
            )
        if not patched_exists:
            return finish(
                VerifyResult(
                    "NO_TEST", f"test file missing after patch: {test_file}", "".join(logs)
                )
            )

        patched_rc, out = _run(test_cmd, repo, env, timeout_s)
        logs.append(out)
        if patched_rc is None:
            return error(f"test command did not complete after patch: {out[-500:]}")

        patched_suite_rc: int | None = None
        if suite_cmd is not None:
            patched_suite_rc, out = _run(suite_cmd, repo, env, timeout_s)
            logs.append(out)
            if patched_suite_rc is None:
                return error(f"suite command did not complete after patch: {out[-500:]}")

        if base_rc == 0:
            return finish(
                VerifyResult("UNPROVEN", "test passes on base", "".join(logs), base_rc, patched_rc)
            )
        if base_rc is None:
            if patched_rc == 0:
                return finish(
                    VerifyResult(
                        "UNPROVEN",
                        "test added by patch; no base failure to compare",
                        "".join(logs),
                        base_rc,
                        patched_rc,
                    )
                )
            return finish(
                VerifyResult(
                    "ERROR",
                    "test added by patch still fails",
                    "".join(logs),
                    base_rc,
                    patched_rc,
                )
            )
        if patched_rc != 0:
            return finish(
                VerifyResult(
                    "ERROR", "test still fails after patch", "".join(logs), base_rc, patched_rc
                )
            )
        if suite_cmd is not None and base_suite_rc == 0 and patched_suite_rc != 0:
            return finish(
                VerifyResult(
                    "REGRESSION",
                    "pre-existing tests pass on base but fail after patch",
                    "".join(logs),
                    base_rc,
                    patched_rc,
                    regressions=1,
                )
            )
        return finish(
            VerifyResult(
                "VERIFIED", "fail-to-pass with no regressions", "".join(logs), base_rc, patched_rc
            )
        )
    except OSError as exc:
        return error(f"filesystem error: {exc}")
    finally:
        if orig_ref and env:
            try:
                subprocess.run(
                    ["git", "checkout", "-qf", orig_ref],
                    cwd=repo,
                    env=dict(env),
                    capture_output=True,
                    timeout=30.0,
                )
                current = _git_out(repo, env, 30.0, "ls-files", "--others", "--exclude-standard")
                for rel in (current or "").splitlines():
                    if rel and rel not in initial_untracked:
                        victim = repo / rel
                        try:
                            if victim.is_symlink() or victim.is_file():
                                victim.unlink()
                            elif victim.is_dir():
                                shutil.rmtree(victim, ignore_errors=True)
                        except OSError:
                            pass
            except (subprocess.TimeoutExpired, OSError):
                pass
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)
