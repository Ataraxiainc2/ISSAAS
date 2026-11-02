"""Workspace prep for one issue. Read-only GitHub use, no writes upstream.

Steps: parse the issue URL, fetch the issue (title, body, labels, repo)
via the GitHub REST API (``GITHUB_TOKEN`` optional, read-only), shallow
clone the repo into ``workspaces/<run_id>/<repo>``, and create a
project-local venv with ``uv`` inside that folder plus a best-effort
install of the repo's test dependencies. Every path is validated to stay
under ``workspaces/`` before any write.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from issa_as import ratelimit as limits
from issa_as.verifier import _sanitized_env

DEFAULT_WORKSPACE_ROOT = Path("workspaces")
GITHUB_API_BASE = "https://api.github.com"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ISSUE_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/issues/(?P<number>\d+)/?$"
)
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Test extras/groups to try in pyproject.toml, in order.
_TEST_EXTRA_NAMES = ("test", "tests", "testing", "dev")

# Test requirement files to try, in order (glob patterns, first match wins).
_TEST_REQUIREMENT_PATTERNS = (
    "requirements-test*.txt",
    "requirements-dev*.txt",
    "test/requirements*.txt",
    "tests/requirements*.txt",
)

# Legacy fallback when no dedicated test requirements exist.
_LEGACY_REQUIREMENTS_FILE = "requirements.txt"

# tox deps lines that are URLs or VCS checkouts are never installed.
_URL_OR_VCS_PREFIXES = ("http://", "https://", "ftp://", "git+", "svn+", "hg+", "bzr+")


class WorkspaceError(Exception):
    """Setup of the issue workspace failed (fetch, clone, or venv)."""


@dataclass
class IssueInfo:
    """Read-only metadata for one GitHub issue."""

    issue_url: str
    owner: str
    repo: str
    repo_full: str
    number: int
    title: str = ""
    body: str = ""
    labels: list[str] = field(default_factory=list)
    clone_url: str = ""


@dataclass
class VenvInfo:
    """Result of creating the project-local venv."""

    python: Path
    created: bool = False
    log: str = ""


def parse_issue_url(url: str) -> tuple[str, str, int]:
    """Split a GitHub issue URL into (owner, repo, number)."""
    match = _ISSUE_URL_RE.match(url.strip())
    if match is None:
        raise WorkspaceError(f"not a GitHub issue URL: {url!r}")
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def validate_run_id(run_id: str) -> str:
    """Reject run ids that could escape ``workspaces/``."""
    if not _RUN_ID_RE.match(run_id or "") or run_id in (".", ".."):
        raise WorkspaceError(f"unsafe run_id: {run_id!r}")
    return run_id


def resolve_under(root: Path, *parts: str) -> Path:
    """Join parts onto root and refuse results outside root."""
    base = root.resolve()
    target = (base.joinpath(*parts)).resolve()
    if target != base and base not in target.parents:
        raise WorkspaceError(f"path escapes workspace root: {target}")
    return target


def repo_dir(
    run_id: str, repo: str, workspaces_root: Path | str = DEFAULT_WORKSPACE_ROOT
) -> Path:
    """Validated destination ``workspaces/<run_id>/<repo>`` for the clone."""
    validate_run_id(run_id)
    if not _RUN_ID_RE.match(repo or ""):
        raise WorkspaceError(f"unsafe repo name: {repo!r}")
    return resolve_under(Path(workspaces_root), run_id, repo)


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_issue(
    issue_url: str,
    *,
    client: httpx.Client | None = None,
    timeout_s: float = 30.0,
    throttle: limits.GitHubThrottle | None = None,
) -> IssueInfo:
    """Fetch one issue read-only via the GitHub REST API.

    ``client`` is injectable for offline tests (use ``httpx.MockTransport``).
    ``throttle`` is the shared GitHub throttle (same instance scout uses);
    it enforces the minimum REST delay and stop-early budget. Raises
    :class:`WorkspaceError` on transport, status, or shape errors.
    """
    owner, repo, number = parse_issue_url(issue_url)
    api_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{number}"
    close = False
    if client is None:
        client = httpx.Client(headers=_github_headers(), timeout=timeout_s)
        close = True
    try:
        try:
            if throttle is not None:
                throttle.wait("rest")
            response = client.get(api_url, headers=_github_headers())
            if throttle is not None:
                throttle.note_response(response.status_code, response.headers)
        except limits.RateLimited as exc:
            raise WorkspaceError(f"GitHub API rate limited: {exc.reason}") from exc
        except httpx.HTTPError as exc:
            raise WorkspaceError(f"GitHub API request failed: {exc}") from exc
        if response.status_code != 200:
            raise WorkspaceError(
                f"GitHub API returned {response.status_code} for {owner}/{repo}#{number}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise WorkspaceError("GitHub API returned invalid JSON") from exc
    finally:
        if close:
            client.close()
    if not isinstance(data, dict):
        raise WorkspaceError("GitHub API returned an unexpected shape")
    try:
        title = str(data.get("title", "") or "")
        body = str(data.get("body", "") or "")
        raw_labels = data.get("labels", []) or []
        labels = [str(item.get("name", "")) for item in raw_labels if isinstance(item, dict)]
        labels = [name for name in labels if name]
    except (AttributeError, ValueError) as exc:
        raise WorkspaceError("GitHub API returned an unexpected shape") from exc
    return IssueInfo(
        issue_url=issue_url,
        owner=owner,
        repo=repo,
        repo_full=f"{owner}/{repo}",
        number=number,
        title=title,
        body=body,
        labels=labels,
        clone_url=f"https://github.com/{owner}/{repo}.git",
    )


def clone_repo(
    clone_url: str,
    dest: Path | str,
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    depth: int = 1,
    timeout_s: float = 300.0,
    git_bin: str = "git",
) -> Path:
    """Shallow-clone ``clone_url`` into ``dest`` (must sit under workspaces).

    Read-only: a ``--depth 1`` clone, no push, no writes upstream.
    """
    base = Path(workspaces_root).resolve()
    target = Path(dest).resolve()
    if target != base and base not in target.parents:
        raise WorkspaceError(f"clone destination escapes workspace root: {target}")
    if target.exists() and any(target.iterdir()):
        raise WorkspaceError(f"clone destination is not empty: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [git_bin, "clone", "--depth", str(depth), clone_url, str(target)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"git clone timed out after {timeout_s}s") from exc
    except OSError as exc:
        raise WorkspaceError(f"git clone failed to start: {exc}") from exc
    if proc.returncode != 0:
        raise WorkspaceError(f"git clone failed: {proc.stderr.strip()[-500:]}")
    return target


def find_uv_bin() -> str:
    """Locate the project uv binary (``.tools/`` first, then PATH)."""
    for candidate in (PROJECT_ROOT / ".tools" / "uv.exe", PROJECT_ROOT / ".tools" / "uv"):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("uv")
    if found:
        return found
    raise WorkspaceError(
        "uv not found (.tools/uv.exe and PATH both empty); "
        "stopping instead of downloading (see AGENTS.md rule 6)"
    )


def _venv_python(repo_path: Path) -> Path:
    if os.name == "nt":
        return repo_path / ".venv" / "Scripts" / "python.exe"
    return repo_path / ".venv" / "bin" / "python"


def _pyproject_test_extra(repo: Path) -> str | None:
    """First test extra in ``pyproject.toml`` (optional deps, then groups)."""
    try:
        doc = tomllib.loads((repo / "pyproject.toml").read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    project = doc.get("project")
    if isinstance(project, dict):
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for name in _TEST_EXTRA_NAMES:
                if name in optional:
                    return name
    groups = doc.get("dependency-groups")
    if isinstance(groups, dict):
        for name in _TEST_EXTRA_NAMES:
            if name in groups:
                return name
    return None


def _first_test_requirements_file(repo: Path) -> Path | None:
    """First test requirements file in pattern order, else legacy fallback."""
    for pattern in _TEST_REQUIREMENT_PATTERNS:
        for candidate in sorted(repo.glob(pattern)):
            if candidate.is_file():
                return candidate
    legacy = repo / _LEGACY_REQUIREMENTS_FILE
    if legacy.is_file():
        return legacy
    return None


def _is_url_or_vcs_spec(spec: str) -> bool:
    """True for URL / VCS lines, which are never installed from tox deps."""
    low = spec.strip().lower()
    return "://" in low or low.startswith(_URL_OR_VCS_PREFIXES)


def _tox_test_installs(repo: Path) -> tuple[list[Path], list[str]]:
    """Parse ``tox.ini`` ``[testenv] deps`` into (requirement files, specs).

    ``-r`` references resolve against the repo (``{toxinidir}`` expands to
    it); anything unexpandable, missing, or a URL/VCS line is skipped.
    """
    ini = repo / "tox.ini"
    if not ini.is_file():
        return [], []
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(ini, encoding="utf-8")
    except (OSError, configparser.Error):
        return [], []
    if not parser.has_section("testenv") or not parser.has_option("testenv", "deps"):
        return [], []
    files: list[Path] = []
    specs: list[str] = []
    for line in parser.get("testenv", "deps").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if item.startswith("-r"):
            ref = item[2:].strip().replace("{toxinidir}", str(repo))
            if not ref or "{" in ref or "}" in ref or _is_url_or_vcs_spec(ref):
                continue
            candidate = Path(ref)
            if not candidate.is_absolute():
                candidate = repo / candidate
            if candidate.is_file():
                files.append(candidate)
            continue
        if item.startswith("-"):
            continue  # other flags (-e, -c, --...) are not test deps
        if _is_url_or_vcs_spec(item) or "{" in item or "}" in item:
            continue
        specs.append(item)
    return files, specs


def _mentions_pytest(text: str) -> bool:
    """True when a requirements source already pulls in pytest."""
    return "pytest" in text.lower()


def _install_test_deps(
    repo: Path,
    python: Path,
    uv: str,
    run: Callable[..., subprocess.CompletedProcess[str]],
    env: dict[str, str],
    timeout_s: float,
    logs: list[str],
) -> bool:
    """Install the first test requirements found; return pytest coverage.

    Order: ``pyproject.toml`` test extras/groups, requirement-file
    patterns, ``tox.ini`` ``[testenv] deps``. Returns True when the
    installed source already mentions pytest. Every command and exit code
    is appended to ``logs``.
    """

    def _pip(args: list[str], label: str) -> int | None:
        try:
            inst = run(
                [uv, "pip", "install", "--python", str(python), *args],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logs.append(f"{label}\nFAILED: {exc}")
            return None
        logs.append(f"{label}\n(exit {inst.returncode})\n{inst.stdout}{inst.stderr}")
        return inst.returncode

    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    extra = _pyproject_test_extra(repo)
    if extra is not None:
        _pip(["-e", f".[{extra}]"], f"$ uv pip install -e .[{extra}]")
        return _mentions_pytest(_read(repo / "pyproject.toml"))
    req = _first_test_requirements_file(repo)
    if req is not None:
        shown = str(req.relative_to(repo)).replace("\\", "/")
        _pip(["-r", str(req)], f"$ uv pip install -r {shown}")
        return _mentions_pytest(_read(req))
    tox_files, tox_specs = _tox_test_installs(repo)
    if tox_files:
        first = tox_files[0]
        shown = str(first.relative_to(repo)).replace("\\", "/")
        _pip(["-r", str(first)], f"$ uv pip install -r {shown}")
        return _mentions_pytest(_read(first))
    if tox_specs:
        _pip(tox_specs, f"$ uv pip install {' '.join(tox_specs)}")
        return _mentions_pytest(" ".join(tox_specs))
    return False


def setup_venv(
    repo_path: Path | str,
    *,
    workspaces_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    uv_bin: str | None = None,
    timeout_s: float = 600.0,
    install: bool = True,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> VenvInfo:
    """Create ``.venv`` inside the cloned repo and install test dependencies.

    Install order: editable ``.``, then the first test requirements found
    (``pyproject`` test extras/groups, requirement-file patterns,
    ``tox.ini`` ``[testenv] deps`` with URL/VCS lines skipped), then a
    bare ``pytest`` when nothing installed mentions it.

    ``runner`` is injectable for offline tests. Venv creation failing raises
    :class:`WorkspaceError`; dependency installs are best-effort and only
    recorded in the returned log. Every subprocess runs with the verifier's
    sanitized environment (no secrets, temp dirs pinned under
    ``workspaces/``) and a hard timeout, because installs execute
    third-party build code outside the OS sandbox.
    """
    base = Path(workspaces_root).resolve()
    repo = Path(repo_path).resolve()
    if repo != base and base not in repo.parents:
        raise WorkspaceError(f"repo path escapes workspace root: {repo}")
    if not repo.is_dir():
        raise WorkspaceError(f"repo not found: {repo}")
    uv = uv_bin or find_uv_bin()
    run = runner or subprocess.run
    tmpdir = Path(tempfile.mkdtemp(prefix="issa-venv-", dir=base))
    try:
        env = _sanitized_env(tmpdir)
        logs: list[str] = []
        try:
            proc = run(
                [uv, "venv", ".venv"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError(f"uv venv timed out after {timeout_s}s") from exc
        except OSError as exc:
            raise WorkspaceError(f"uv venv failed to start: {exc}") from exc
        logs.append(
            f"$ uv venv .venv\n(exit {proc.returncode})\n{proc.stdout}{proc.stderr}"
        )
        if proc.returncode != 0:
            raise WorkspaceError("uv venv failed (see log)")
        python = _venv_python(repo)
        if install:
            pyproject = repo / "pyproject.toml"
            if pyproject.is_file():
                try:
                    inst = run(
                        [uv, "pip", "install", "--python", str(python), "-e", "."],
                        cwd=repo,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=timeout_s,
                    )
                except (subprocess.TimeoutExpired, OSError) as exc:
                    logs.append(f"$ uv pip install -e .\nFAILED: {exc}")
                else:
                    logs.append(
                        f"$ uv pip install -e .\n"
                        f"(exit {inst.returncode})\n{inst.stdout}{inst.stderr}"
                    )
            pytest_covered = _install_test_deps(repo, python, uv, run, env, timeout_s, logs)
            if not pytest_covered:
                try:
                    inst = run(
                        [uv, "pip", "install", "--python", str(python), "pytest"],
                        cwd=repo,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=timeout_s,
                    )
                except (subprocess.TimeoutExpired, OSError) as exc:
                    logs.append(f"$ uv pip install pytest\nFAILED: {exc}")
                else:
                    logs.append(
                        f"$ uv pip install pytest\n"
                        f"(exit {inst.returncode})\n{inst.stdout}{inst.stderr}"
                    )
        return VenvInfo(python=python, created=True, log="".join(logs))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


__all__ = [
    "DEFAULT_WORKSPACE_ROOT",
    "IssueInfo",
    "VenvInfo",
    "WorkspaceError",
    "clone_repo",
    "fetch_issue",
    "find_uv_bin",
    "parse_issue_url",
    "repo_dir",
    "resolve_under",
    "setup_venv",
    "validate_run_id",
]
