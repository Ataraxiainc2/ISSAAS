"""Deterministic Fail-to-Pass gate. Plain Python — no LLM calls.

Given a repo under ``workspaces/``, a base commit, a unified-diff patch, a
test file and the commands to run it, returns one of ``VERIFIED`` (the test
fails on base and passes with the patch, with no regressions), ``UNPROVEN``
(the test already passes on base), ``NO_TEST`` (no test file), ``REGRESSION``
(the patch breaks pre-existing tests) or ``ERROR`` (anything else).

A target venv that cannot run pytest — failed collection, exit codes
2-5, or a baseline suite with no JUnit output — is ``ERROR`` with a
reason naming the environment problem, never "test still fails".

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
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from issa_as import traces as trace_log

Verdict = Literal["VERIFIED", "UNPROVEN", "NO_TEST", "REGRESSION", "ERROR"]

DEFAULT_WORKSPACE_ROOT = Path("workspaces")
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_SUITE_TIMEOUT_S = 1200.0
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

# Pytest exit codes that mean the environment is broken, not the tests:
# 2 interrupted, 3 internal error, 4 usage error, 5 no tests collected.
_INFRA_EXIT_CODES = frozenset({2, 3, 4, 5})
_INFRA_PREFIX = "test environment incomplete"


@dataclass
class VerifyResult:
    """Outcome of one verification run, with captured logs."""

    verdict: Verdict
    reason: str = ""
    log: str = ""
    base_returncode: int | None = None
    patched_returncode: int | None = None
    regressions: int = 0
    baseline_failures: list[str] = field(default_factory=list)


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


def _venv_python_for(repo: Path) -> Path:
    """Workspace venv interpreter (``.venv`` inside the cloned repo)."""
    if os.name == "nt":
        return repo / ".venv" / "Scripts" / "python.exe"
    return repo / ".venv" / "bin" / "python"


_BARE_PYTHON = {"python", "python3", "python.exe", "python3.exe"}
_BARE_PYTEST = {"pytest", "pytest.exe"}


def _resolve_cmd(cmd: Sequence[str], repo: Path) -> list[str]:
    """Resolve ``argv[0]`` so it starts from the repo, not the parent's cwd.

    On Windows ``subprocess`` resolves a relative ``argv[0]`` against the
    parent process's cwd, ignoring ``cwd=``. A bare ``python``/``pytest``
    maps to the workspace venv interpreter (falling back to the current
    interpreter when the venv is absent). A relative path containing a
    separator is anchored at ``repo``, unless it already resolves inside
    ``repo`` (e.g. a ``workspaces/<run>/<repo>/...`` path relative to the
    project root, which must not be anchored a second time). Absolute
    paths pass through. ``shell=False`` is always kept by the caller.
    """
    parts = [str(p) for p in cmd]
    if not parts:
        return parts
    arg0 = parts[0]
    leaf = arg0.replace("\\", "/").split("/")[-1]
    has_sep = ("/" in arg0) or ("\\" in arg0) or (os.sep in arg0)
    if not has_sep:
        key = leaf.lower() if os.name == "nt" else leaf
        bare_py = {n.lower() if os.name == "nt" else n for n in _BARE_PYTHON}
        bare_pt = {n.lower() if os.name == "nt" else n for n in _BARE_PYTEST}
        if key in bare_py:
            venv_py = _venv_python_for(repo)
            parts[0] = str(venv_py) if venv_py.is_file() else sys.executable
            return parts
        if key in bare_pt:
            venv_py = _venv_python_for(repo)
            python = str(venv_py) if venv_py.is_file() else sys.executable
            return [python, "-m", "pytest", *parts[1:]]
        return parts
    if os.path.isabs(arg0) or (len(arg0) >= 2 and arg0[1] == ":"):
        return parts
    if arg0.startswith(("\\\\", "//")):
        return parts
    try:
        repo_abs = Path(repo).resolve()
        given_abs = Path(arg0).resolve()
        if given_abs == repo_abs or repo_abs in given_abs.parents:
            return parts
    except OSError:
        pass
    parts[0] = str(Path(repo) / arg0)
    return parts


def _is_target_arg(arg: str, repo: Path) -> bool:
    """Heuristic: is this pytest arg a test target (file/dir/node) rather than a flag."""
    if not arg or arg.startswith("-") or arg == "--":
        return False
    if "::" in arg:
        return True
    if arg.endswith(".py"):
        return True
    try:
        if (repo / arg).exists():
            return True
    except OSError:
        pass
    return ("/" in arg) or ("\\" in arg)


def _f2p_cmd(cmd: Sequence[str], test_file: Path | str, repo: Path) -> list[str]:
    """Isolate the Fail-to-Pass check to ``test_file`` alone.

    Whatever extra files the model's ``test_command`` lists are dropped;
    pytest flags (including flag values such as ``-p no:cacheprovider``)
    are kept. Non-pytest commands are returned unchanged.
    """
    parts = [str(p) for p in cmd]
    marker = -1
    for i, tok in enumerate(parts):
        leaf = tok.replace("\\", "/").split("/")[-1]
        if leaf in _BARE_PYTEST or leaf.lower() in ("pytest.exe",):
            marker = i
            break
        if tok == "pytest":
            marker = i
            break
    if marker == -1:
        for i in range(len(parts) - 1):
            if parts[i] == "-m" and parts[i + 1] in ("pytest", "pytest.exe"):
                marker = i + 1
                break
    if marker == -1:
        return parts
    prefix = parts[: marker + 1]
    kept = [a for a in parts[marker + 1 :] if not _is_target_arg(a, repo)]
    return [*prefix, *kept, str(test_file)]


def _suite_cmd_with_junit(suite_cmd: Sequence[str], xml_path: Path) -> list[str]:
    """Append ``--junitxml`` unless the suite command already sets it."""
    parts = [str(p) for p in suite_cmd]
    for tok in parts:
        if tok == "--junitxml" or tok.startswith("--junitxml="):
            return parts
    return [*parts, "--junitxml", str(xml_path)]


def _parse_junit(xml_path: Path) -> tuple[set[str], set[str]] | None:
    """Parse a pytest junitxml file into ``(all_tests, failed_tests)``.

    Returns ``None`` when the file is missing or unparseable, so the
    caller can fall back to return-code comparison.
    """
    try:
        tree = ET.parse(xml_path)
    except (OSError, ET.ParseError):
        return None
    root = tree.getroot()
    all_tests: set[str] = set()
    failed: set[str] = set()
    for tc in root.iter("testcase"):
        classname = str(tc.get("classname") or "")
        name = str(tc.get("name") or "")
        node = f"{classname}::{name}" if classname else name
        file_attr = tc.get("file")
        if file_attr:
            node = f"{Path(file_attr).name}::{node}"
        if not node:
            continue
        all_tests.add(node)
        if tc.find("failure") is not None or tc.find("error") is not None:
            failed.add(node)
    return all_tests, failed


def _junit_candidates(cmd: Sequence[str], default: Path, repo: Path) -> list[Path]:
    """JUnit files the suite run may have written (default first)."""
    paths = [default]
    parts = [str(p) for p in cmd]
    for i, tok in enumerate(parts):
        custom: str | None = None
        if tok == "--junitxml" and i + 1 < len(parts):
            custom = parts[i + 1]
        elif tok.startswith("--junitxml="):
            custom = tok.split("=", 1)[1]
        if custom:
            candidate = Path(custom)
            if not candidate.is_absolute():
                candidate = repo / candidate
            if candidate != default:
                paths.append(candidate)
    return paths


def _parse_suite_junit(
    cmd: Sequence[str], default: Path, repo: Path
) -> tuple[set[str], set[str]] | None:
    """Parse the suite's JUnit output, or None when no file was produced."""
    for candidate in _junit_candidates(cmd, default, repo):
        parsed = _parse_junit(candidate)
        if parsed is not None:
            return parsed
    return None


def _collect_only_cmd(repo: Path, test_file: Path | str) -> list[str]:
    """``pytest --collect-only -q <test_file>`` with the workspace interpreter."""
    venv_py = _venv_python_for(repo)
    python = str(venv_py) if venv_py.is_file() else sys.executable
    return [python, "-m", "pytest", "--collect-only", "-q", str(test_file)]


def _decode_chunk(value: bytes | str | None) -> str:
    """Decode a subprocess output chunk that may be bytes, str, or None."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _fmt_timeout(timeout_s: float) -> str:
    """Format a timeout like ``120 s`` (no trailing ``.0``)."""
    return f"{timeout_s:g} s"


def _is_timeout_log(out: str) -> bool:
    """True when a :func:`_run` log records a timeout rather than a start failure."""
    return "TIMEOUT after" in out


def _run(
    cmd: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float
) -> tuple[int | None, str]:
    """Run a command without a shell; ``None`` returncode means timeout/start failure."""
    resolved = _resolve_cmd(cmd, Path(cwd))
    header = f"$ {' '.join(resolved)}"
    try:
        proc = subprocess.run(
            resolved,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        out = _decode_chunk(exc.stdout) + _decode_chunk(exc.stderr)
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
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        _decode_chunk(exc.stdout)
        _decode_chunk(exc.stderr)
        return None
    except OSError:
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
    baseline_commit: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    suite_timeout_s: float | None = None,
    workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    run_id: str | None = None,
    issue_id: str | None = None,
    traces_dir: Path | None = None,
) -> VerifyResult:
    """Fail-to-Pass check of a patch inside a workspace git repo.

    The F2P check runs on ``test_file`` alone (extra files in ``test_cmd``
    are dropped). When ``suite_cmd`` and ``baseline_commit`` are both given,
    the existing suite runs on the baseline commit and after the patch with
    ``--junitxml``; only tests that passed on the baseline and fail after
    the patch count as regressions, and baseline failures are reported via
    :attr:`VerifyResult.baseline_failures` instead.

    ``timeout_s`` bounds the per-test Fail-to-Pass runs (and git plumbing);
    ``suite_timeout_s`` bounds the suite runs (defaults to ``timeout_s``).
    A timeout is an ``ERROR`` verdict with reason
    ``"<step> timed out after <N> s"``, never an exception.
    """
    suite_timeout: float = suite_timeout_s if suite_timeout_s is not None else timeout_s
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
        if baseline_commit is not None and (
            _git_out(repo, env, timeout_s, "cat-file", "-e", baseline_commit) is None
        ):
            return error(f"unknown baseline commit: {baseline_commit}")

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

        use_baseline = suite_cmd is not None and baseline_commit is not None
        f2p_cmd = _f2p_cmd(_resolve_cmd(list(test_cmd), repo), test_file, repo)

        rc, out = _run(["git", "checkout", "-q", base_commit], repo, env, timeout_s)
        logs.append(out)
        if rc is None and _is_timeout_log(out):
            return error(f"git checkout timed out after {_fmt_timeout(timeout_s)}")
        if rc != 0:
            return error(f"cannot checkout base commit: {base_commit}")

        base_exists = test_abs.is_file()
        base_rc: int | None = None
        if base_exists:
            base_rc, out = _run(f2p_cmd, repo, env, timeout_s)
            logs.append(out)
            if base_rc is None:
                if _is_timeout_log(out):
                    return error(f"test command timed out after {_fmt_timeout(timeout_s)}")
                return error(f"test command did not complete on base: {out[-500:]}")
            if base_rc in _INFRA_EXIT_CODES:
                return error(
                    f"{_INFRA_PREFIX}: pytest exited with code {base_rc} "
                    f"on base: {out[-500:]}"
                )

        base_suite_rc: int | None = None
        if suite_cmd is not None and not use_baseline:
            base_suite_rc, out = _run(suite_cmd, repo, env, suite_timeout)
            logs.append(out)
            if base_suite_rc is None:
                if _is_timeout_log(out):
                    return error(f"suite command timed out after {_fmt_timeout(suite_timeout)}")
                return error(f"suite command did not complete on base: {out[-500:]}")

        baseline_all: set[str] = set()
        baseline_failed: set[str] = set()
        baseline_suite_rc: int | None = None
        baseline_xml = tmpdir / "baseline-junit.xml"
        junit_usable = False
        if use_baseline:
            assert suite_cmd is not None and baseline_commit is not None
            rc, out = _run(["git", "checkout", "-q", baseline_commit], repo, env, timeout_s)
            logs.append(out)
            if rc is None and _is_timeout_log(out):
                return error(f"git checkout timed out after {_fmt_timeout(timeout_s)}")
            if rc != 0:
                return error(f"cannot checkout baseline commit: {baseline_commit}")
            suite_resolved = _resolve_cmd(list(suite_cmd), repo)
            baseline_suite_rc, out = _run(
                _suite_cmd_with_junit(suite_resolved, baseline_xml), repo, env, suite_timeout
            )
            logs.append(out)
            if baseline_suite_rc is None:
                if _is_timeout_log(out):
                    return error(f"suite command timed out after {_fmt_timeout(suite_timeout)}")
                return error(f"suite command did not complete on baseline: {out[-500:]}")
            parsed = _parse_suite_junit(suite_resolved, baseline_xml, repo)
            if parsed is None:
                return error(
                    f"{_INFRA_PREFIX}: baseline suite produced no JUnit file: "
                    f"{out[-500:]}"
                )
            junit_usable = True
            baseline_all, baseline_failed = parsed
            rc, out = _run(["git", "checkout", "-q", base_commit], repo, env, timeout_s)
            logs.append(out)
            if rc is None and _is_timeout_log(out):
                return error(f"git checkout timed out after {_fmt_timeout(timeout_s)}")
            if rc != 0:
                return error(f"cannot checkout base commit: {base_commit}")

        if patch_text:
            patch_file = tmpdir / "fix.patch"
            patch_file.write_text(
                patch_text if patch_text.endswith("\n") else patch_text + "\n",
                encoding="utf-8",
            )
            rc, out = _run(["git", "apply", str(patch_file)], repo, env, timeout_s)
            logs.append(out)
            if rc is None and _is_timeout_log(out):
                return error(f"git apply timed out after {_fmt_timeout(timeout_s)}")
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

        collect_rc, out = _run(_collect_only_cmd(repo, test_file), repo, env, timeout_s)
        logs.append(out)
        if collect_rc is None:
            if _is_timeout_log(out):
                return error(
                    f"pytest --collect-only timed out after {_fmt_timeout(timeout_s)}"
                )
            return error(
                f"{_INFRA_PREFIX}: pytest --collect-only did not complete "
                f"for {test_file}: {out[-500:]}"
            )
        if collect_rc != 0:
            return error(
                f"{_INFRA_PREFIX}: pytest cannot collect {test_file}: {out[-500:]}"
            )

        patched_rc, out = _run(f2p_cmd, repo, env, timeout_s)
        logs.append(out)
        if patched_rc is None:
            if _is_timeout_log(out):
                return error(f"test command timed out after {_fmt_timeout(timeout_s)}")
            return error(f"test command did not complete after patch: {out[-500:]}")
        if patched_rc in _INFRA_EXIT_CODES:
            return error(
                f"{_INFRA_PREFIX}: pytest exited with code {patched_rc} "
                f"after patch: {out[-500:]}"
            )

        patched_suite_rc: int | None = None
        patched_all: set[str] = set()
        patched_failed: set[str] = set()
        patched_xml = tmpdir / "patched-junit.xml"
        if suite_cmd is not None and not use_baseline:
            patched_suite_rc, out = _run(suite_cmd, repo, env, suite_timeout)
            logs.append(out)
            if patched_suite_rc is None:
                if _is_timeout_log(out):
                    return error(f"suite command timed out after {_fmt_timeout(suite_timeout)}")
                return error(f"suite command did not complete after patch: {out[-500:]}")
        elif use_baseline:
            assert suite_cmd is not None
            suite_resolved = _resolve_cmd(list(suite_cmd), repo)
            patched_suite_rc, out = _run(
                _suite_cmd_with_junit(suite_resolved, patched_xml), repo, env, suite_timeout
            )
            logs.append(out)
            if patched_suite_rc is None:
                if _is_timeout_log(out):
                    return error(f"suite command timed out after {_fmt_timeout(suite_timeout)}")
                return error(f"suite command did not complete after patch: {out[-500:]}")
            parsed = _parse_suite_junit(suite_resolved, patched_xml, repo)
            if parsed is not None and junit_usable:
                patched_all, patched_failed = parsed
            else:
                junit_usable = False

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
        if use_baseline:
            baseline_failures = sorted(baseline_failed)
            if junit_usable:
                baseline_passed = baseline_all - baseline_failed
                regressed = sorted(patched_failed & baseline_passed)
                if regressed:
                    shown = ", ".join(regressed[:5])
                    return finish(
                        VerifyResult(
                            "REGRESSION",
                            f"tests passed on baseline but fail after patch: {shown}",
                            "".join(logs),
                            base_rc,
                            patched_rc,
                            regressions=len(regressed),
                            baseline_failures=baseline_failures,
                        )
                    )
                return finish(
                    VerifyResult(
                        "VERIFIED",
                        "fail-to-pass with no regressions",
                        "".join(logs),
                        base_rc,
                        patched_rc,
                        baseline_failures=baseline_failures,
                    )
                )
            if baseline_suite_rc == 0 and patched_suite_rc != 0:
                return finish(
                    VerifyResult(
                        "REGRESSION",
                        "pre-existing tests pass on baseline but fail after patch",
                        "".join(logs),
                        base_rc,
                        patched_rc,
                        regressions=1,
                        baseline_failures=baseline_failures,
                    )
                )
            return finish(
                VerifyResult(
                    "VERIFIED",
                    "fail-to-pass with no regressions",
                    "".join(logs),
                    base_rc,
                    patched_rc,
                    baseline_failures=baseline_failures,
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
                    stdin=subprocess.DEVNULL,
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
