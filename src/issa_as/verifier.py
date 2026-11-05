"""Deterministic Fail-to-Pass gate with Pass-to-Pass preservation. Plain Python — no LLM calls.

Given a repo under ``workspaces/``, a base commit, a unified-diff patch, a
test file and the commands to run it, returns one of ``VERIFIED`` (the test
fails on base and passes with the patch, with no regressions), ``UNPROVEN``
(the test already passes on base, or the patch touches the regression test
or its configuration), ``NO_TEST`` (no test file), ``REGRESSION``
(the patch breaks pre-existing tests) or ``ERROR`` (anything else).

Fail-to-Pass runs with ``--junitxml`` and the XML — not the exit code —
decides what "fails" and "passes" mean: the target file's tests must have
actually run, with at least one passed and zero failed/errored/skipped for
a pass, and at least one failed/errored for a failure. A patch that touches
the regression test file, any ``conftest.py``, ``pytest.ini``, ``tox.ini``,
``setup.cfg`` or the ``[tool.pytest*]`` section of ``pyproject.toml`` is
``UNPROVEN`` without running any test. When ``preserve_file`` is
given, it must also be clean on base
and with the patch applied (see ``verify()``).

A target venv that cannot run pytest — failed collection, exit codes
2-5, or a baseline suite with no JUnit output — is ``ERROR`` with a
reason naming the environment problem, never "test still fails".

Every command runs with an allowlisted subprocess environment (see
:func:`target_env`): only locale, OS, cache, and workspace-temp keys pass
through, ``cwd``/``TMP``/``TEMP`` stay inside the workspace, and each
command has a hard timeout. Host secrets and host DB URLs never reach
target-repo code. The working tree is restored to its original commit
before returning. The verdict is appended as a ``verify_verdict`` trace
event when ``run_id`` is given.

Every pytest run in a target repo is isolated from parent checkouts at
execution time only (never stored in ``test_command``): ``--rootdir``,
``-p no:cacheprovider``, a fresh unique ``--basetemp`` under
``workspaces/<run_id>/.issa-tmp/``, and ``-c`` with an empty ``[pytest]``
file when the repo has no pytest config of its own.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
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

# Environment keys third-party target-repo code may inherit.
# Everything else in ``os.environ`` is dropped -- in particular every
# ``*_KEY``, ``*_TOKEN``, ``*_SECRET``, ``*_PASSWORD`` credential,
# ``DATABASE_URL`` (a host Postgres URL once overrode a target suite's
# sqlite default and made "0 regressions" meaningless), and the
# ``GITHUB_*``/``BASETEN_*``/``META_*``/``MUSE_*``/``LLAMA_*``/
# ``OPENAI_*``/``AWS_*``/``GOOGLE_*``/``AZURE_*`` provider namespaces.
TARGET_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "LANG",
        "LC_ALL",
        "TZ",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "PIP_CACHE_DIR",
        "VIRTUAL_ENV",
        "CI",
    }
)

# Values forced into every target environment (never inherited).
_TARGET_ENV_FORCED = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}

# Pytest exit codes that mean the environment is broken, not the tests:
# 2 interrupted, 3 internal error, 4 usage error, 5 no tests collected.
_INFRA_EXIT_CODES = frozenset({2, 3, 4, 5})
_INFRA_PREFIX = "test environment incomplete"

# Per-test timeout added to suite runs when the target venv provides
# pytest-timeout: a hanging test then fails on its own and is compared
# against the baseline like any other failure.
SUITE_PER_TEST_TIMEOUT_S = 120.0
SUITE_TIMEOUT_FLAG = f"--timeout={SUITE_PER_TEST_TIMEOUT_S:g}"
# Parallel suite runs when the target venv provides pytest-xdist.
SUITE_XDIST_FLAGS = ("-n", "auto")

# A short ``-n`` flag with its value attached (``-n4``, ``-nauto``).
_N_ATTACHED_RE = re.compile(r"-n(=.*|[A-Za-z0-9].*)")


def _last_output_line(out: str) -> str:
    """Last non-empty line of captured output, for one-line reasons."""
    for line in reversed(out.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return "(no output)"


def _is_pytest_cmd(parts: Sequence[str]) -> bool:
    """True when the command invokes pytest (bare, ``-m``, or venv path)."""
    toks = [str(p) for p in parts]
    if not toks:
        return False
    if _is_pytest_leaf(_argv0_leaf(toks[0])):
        return True
    return any(
        toks[i] == "-m" and _is_pytest_leaf(_argv0_leaf(toks[i + 1]))
        for i in range(len(toks) - 1)
    )


def _has_timeout_flag(parts: Sequence[str]) -> bool:
    """True when the pytest command already sets ``--timeout``."""
    return any(
        tok == "--timeout" or tok.startswith("--timeout=") for tok in parts
    )


def _has_xdist_flag(parts: Sequence[str]) -> bool:
    """True when the pytest command already enables xdist."""
    return any(
        tok == "-n"
        or tok == "--numprocesses"
        or tok.startswith("--numprocesses=")
        or _N_ATTACHED_RE.fullmatch(tok) is not None
        for tok in parts
    )


def _with_suite_flags(cmd: Sequence[str], flags: Sequence[str]) -> list[str]:
    """Append suite-only pytest flags (no duplicates, pytest commands only).

    Fail-to-Pass commands never pass through here; non-pytest suite
    commands are returned unchanged.
    """
    parts = [str(p) for p in cmd]
    if not flags or not _is_pytest_cmd(parts):
        return parts
    out = list(parts)
    if SUITE_TIMEOUT_FLAG in flags and not _has_timeout_flag(out):
        out.append(SUITE_TIMEOUT_FLAG)
    if all(flag in flags for flag in SUITE_XDIST_FLAGS) and not _has_xdist_flag(out):
        out.extend(SUITE_XDIST_FLAGS)
    return out


def _toml_has_pytest_options(path: Path) -> bool:
    """Whether ``pyproject.toml`` declares ``[tool.pytest.ini_options]``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return True  # pytest would choke on it too: treat as configured
    if not isinstance(data, dict):
        return True
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return False
    section = tool.get("pytest")
    return isinstance(section, dict) and "ini_options" in section


def _ini_has_section(path: Path, section: str) -> bool:
    """Whether an INI-style config file defines ``section``."""
    parser = configparser.RawConfigParser()
    try:
        with path.open("r", encoding="utf-8") as fh:
            parser.read_file(fh)
    except (OSError, configparser.Error, UnicodeDecodeError):
        return True  # pytest would choke on it too: treat as configured
    try:
        return parser.has_section(section)
    except configparser.Error:
        return True


def _repo_has_pytest_config(repo: Path) -> bool:
    """Whether the target repo carries its own pytest config (root only).

    ``pytest.ini``/``pytest.toml``/``.pytest.ini``, a ``pyproject.toml``
    with ``[tool.pytest.ini_options]``, a ``tox.ini`` with ``[pytest]``
    or a ``setup.cfg`` with ``[tool:pytest]``. Anything else means pytest
    would walk up past the repo and read a parent config (e.g. ours).
    """
    for name in ("pytest.ini", "pytest.toml", ".pytest.ini"):
        if (repo / name).is_file():
            return True
    if (repo / "pyproject.toml").is_file() and _toml_has_pytest_options(
        repo / "pyproject.toml"
    ):
        return True
    if (repo / "tox.ini").is_file() and _ini_has_section(repo / "tox.ini", "pytest"):
        return True
    if (repo / "setup.cfg").is_file() and _ini_has_section(
        repo / "setup.cfg", "tool:pytest"
    ):
        return True
    return False


def _with_isolation_flags(
    cmd: Sequence[str],
    *,
    rootdir: str,
    basetemp: str,
    config_file: str | None,
) -> list[str]:
    """Append pytest isolation flags (execution time only, pytest only).

    Every pytest run inside a target repo gets ``--rootdir``, ``-p
    no:cacheprovider`` and a ``--basetemp`` under the run workspace, plus
    ``-c`` pointing at an empty ``[pytest]`` file when the repo has no
    pytest config of its own. Flags already present are never duplicated
    and non-pytest commands pass through unchanged, so the stored
    ``test_command`` is never modified here.
    """
    parts = [str(p) for p in cmd]
    if not _is_pytest_cmd(parts):
        return parts
    out = list(parts)
    if not any(tok == "--rootdir" or tok.startswith("--rootdir=") for tok in out):
        out.append(f"--rootdir={rootdir}")
    if not any("no:cacheprovider" in tok for tok in out):
        out.extend(["-p", "no:cacheprovider"])
    if not any(tok == "--basetemp" or tok.startswith("--basetemp=") for tok in out):
        out.append(f"--basetemp={basetemp}")
    if config_file is not None and not any(
        tok == "-c" or tok == "--config-file" or tok.startswith("--config-file=")
        for tok in out
    ):
        out.extend(["-c", config_file])
    return out


def _venv_can_import(
    python: str, module: str, env: Mapping[str, str], timeout_s: float
) -> bool:
    """True when the target venv imports ``module`` (quiet probe, never raises)."""
    try:
        proc = subprocess.run(
            [python, "-c", f"import {module}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=min(timeout_s, 60.0),
            env=dict(env),
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return False
    return proc.returncode == 0


def _suite_extra_flags(
    repo: Path, env: Mapping[str, str], timeout_s: float
) -> list[str]:
    """Extra flags for suite runs from plugins in the target venv.

    ``--timeout=120`` when ``pytest_timeout`` imports, ``-n auto`` when
    ``xdist`` imports. No venv interpreter means no plugins, so no flags.
    """
    venv_py = _workspace_venv_python(repo)
    if venv_py is None:
        return []
    flags: list[str] = []
    if _venv_can_import(str(venv_py), "pytest_timeout", env, timeout_s):
        flags.append(SUITE_TIMEOUT_FLAG)
    if _venv_can_import(str(venv_py), "xdist", env, timeout_s):
        flags.extend(SUITE_XDIST_FLAGS)
    return flags


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
    fail_before: bool | None = None
    pass_after: bool | None = None
    f2p_pass: bool = False
    f2p_tests_passed: int = 0
    f2p_tests_skipped: int = 0
    p2p_checked: bool = False
    p2p_tests_passed: int = 0
    suite_scope: str = "full"
    suite_flags: list[str] = field(default_factory=list)


def _is_workspace_venv(value: str) -> bool:
    """Whether ``VIRTUAL_ENV`` points at a workspace checkout venv.

    The operator's own activated environment must never leak into target
    code; only a venv living under a ``workspaces/`` directory (the
    layout ``setup_venv`` creates) is passed through.
    """
    return any(part.lower() == "workspaces" for part in Path(value).parts)


def target_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Allowlisted environment for subprocesses running target-repo code.

    Copies only :data:`TARGET_ENV_ALLOWLIST` keys from ``os.environ``
    (``VIRTUAL_ENV`` solely when it points at a workspace venv), forces
    ``PYTHONDONTWRITEBYTECODE``/``PYTHONIOENCODING``/``PYTHONUTF8``, then
    applies ``extra`` overrides (callers pin ``TMP``/``TEMP``/``TMPDIR``
    under the run workspace here). Everything else -- every ``*_KEY``,
    ``*_TOKEN``, ``*_SECRET``, ``*_PASSWORD``, ``DATABASE_URL``,
    ``GITHUB_*``, ``BASETEN_*``, ``META_*``, ``MUSE_*``, ``LLAMA_*``,
    ``OPENAI_*``, ``AWS_*``, ``GOOGLE_*`` and ``AZURE_*`` entry -- is
    dropped, so host credentials and host database URLs never reach
    third-party venv creation, installs, or test runs.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if name in TARGET_ENV_ALLOWLIST and name != "VIRTUAL_ENV"
    }
    venv = os.environ.get("VIRTUAL_ENV", "")
    if venv and _is_workspace_venv(venv):
        env["VIRTUAL_ENV"] = venv
    env.update(_TARGET_ENV_FORCED)
    if extra:
        for key, value in dict(extra).items():
            env[str(key)] = str(value)
    return env


def _venv_python_for(repo: Path) -> Path:
    """Workspace venv interpreter (``.venv`` inside the cloned repo)."""
    if os.name == "nt":
        return repo / ".venv" / "Scripts" / "python.exe"
    return repo / ".venv" / "bin" / "python"


def _workspace_venv_python(repo: Path) -> Path | None:
    """Existing workspace venv interpreter, either OS layout, or None.

    The native layout for the current OS is preferred; the other layout
    is accepted as a fallback so a stored foreign-OS path still rebuilds
    (e.g. a Windows ``Scripts/python.exe`` path on Linux rebuilds to
    ``bin/python`` when that file exists, and vice versa).
    """
    native = _venv_python_for(repo)
    alt = (
        repo / ".venv" / "bin" / "python"
        if os.name == "nt"
        else repo / ".venv" / "Scripts" / "python.exe"
    )
    for candidate in (native, alt):
        if candidate.is_file():
            return candidate
    return None


def _argv0_leaf(arg0: str) -> str:
    """Basename of ``argv[0]`` split on both separators (cross-OS)."""
    return arg0.replace("\\", "/").split("/")[-1]


_PYTHON_LEAVES = frozenset({"python", "python3", "python.exe", "python3.exe"})
_PYTEST_LEAVES = frozenset({"pytest", "pytest.exe"})


def _is_python_leaf(leaf: str) -> bool:
    return leaf.lower() in _PYTHON_LEAVES


def _is_pytest_leaf(leaf: str) -> bool:
    return leaf.lower() in _PYTEST_LEAVES


def neutral_test_command(cmd: Sequence[str]) -> list[str]:
    """Interpreter-neutral form of a stored pytest command.

    Pytest invocations become ``python -m pytest ...`` so a new bundle
    never stores a machine-specific interpreter path. Non-pytest
    commands are returned unchanged.
    """
    parts = [str(p) for p in cmd]
    if not parts:
        return parts
    if _is_pytest_leaf(_argv0_leaf(parts[0])):
        return ["python", "-m", "pytest", *parts[1:]]
    if (
        len(parts) >= 3
        and parts[1] == "-m"
        and _is_pytest_leaf(_argv0_leaf(parts[2]))
    ):
        return ["python", "-m", "pytest", *parts[3:]]
    if _is_python_leaf(_argv0_leaf(parts[0])):
        return ["python", *parts[1:]]
    return parts


def portable_test_command(cmd: Sequence[str], repo: Path | str) -> list[str]:
    """Rebuild a stored test command with this machine's venv interpreter.

    Any stored interpreter path (Windows ``Scripts/python.exe``, POSIX
    ``bin/python``, another absolute path, or a bare ``python``/``pytest``)
    is replaced with the workspace venv interpreter for the current OS;
    the stored path is never reused. When no venv interpreter file exists
    yet, the neutral ``python -m pytest ...`` form is returned so the
    runtime resolver can fall back to the current interpreter.
    Non-pytest commands are returned unchanged.
    """
    parts = [str(p) for p in cmd]
    if not parts:
        return parts
    repo_path = Path(repo)
    if _is_pytest_leaf(_argv0_leaf(parts[0])):
        rest = parts[1:]
    elif (
        len(parts) >= 3
        and parts[1] == "-m"
        and _is_pytest_leaf(_argv0_leaf(parts[2]))
    ):
        rest = parts[3:]
    else:
        return parts
    venv_py = _workspace_venv_python(repo_path)
    if venv_py is None:
        return ["python", "-m", "pytest", *rest]
    return [str(venv_py), "-m", "pytest", *rest]


def suite_scope_for(test_file: Path | str | None, suite_path: Path | str | None = None) -> str:
    """Regression-suite scope for a test file: ``--suite-path`` wins, else
    the ``unit`` directory rule, else the full suite.

    Default rule (deterministic): when ``test_file`` lives under a
    directory named ``unit`` (e.g. ``test/unit/test_x.py``), the
    regression suite is that ``unit`` directory (``test/unit``);
    otherwise the full suite (``full``). An explicit ``suite_path``
    overrides the rule. The result is a posix-style directory string or
    ``"full"``.
    """
    if suite_path is not None and str(suite_path).strip():
        return str(suite_path).strip().replace("\\", "/")
    text = str(test_file or "").strip().replace("\\", "/").lstrip("./")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    parents = parts[:-1] if len(parts) > 1 else []
    for i in range(len(parents) - 1, -1, -1):
        if parents[i] == "unit":
            return "/".join(parts[: i + 1])
    return "full"


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
    key = leaf.lower()
    bare_py = {n.lower() for n in _BARE_PYTHON}
    bare_pt = {n.lower() for n in _BARE_PYTEST}
    if not has_sep or (
        (os.path.isabs(arg0) or (len(arg0) >= 2 and arg0[1] == ":"))
        and (key in bare_py or key in bare_pt)
    ):
        # Bare names map to the workspace venv; so does an absolute
        # interpreter path from another machine (e.g. a Windows
        # ``.venv/Scripts/python.exe`` path reused on Linux): the stored
        # path can never exist here, so rebuild it instead of passing a
        # dead interpreter through to the subprocess.
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


def _posix_rel(path: str) -> str:
    """Repo-relative path with forward slashes and no leading ``./``."""
    text = str(path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


_CONFIG_BASENAMES = frozenset({"pytest.ini", "tox.ini", "setup.cfg"})


def _patch_touched_paths(patch_text: str) -> list[str]:
    """Repo-relative paths the patch adds, modifies, or deletes.

    Parsed from the ``---``/``+++`` headers (``/dev/null`` sides skipped),
    so no git invocation is needed and nothing is applied yet.
    """
    paths: list[str] = []
    for line in patch_text.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        rest = line[4:].split("\t")[0].strip()
        if len(rest) >= 2 and rest.startswith('"') and rest.endswith('"'):
            rest = rest[1:-1]
        if rest.startswith("a/") or rest.startswith("b/"):
            rest = rest[2:]
        rest = _posix_rel(rest)
        if rest and rest != "/dev/null" and rest not in paths:
            paths.append(rest)
    return paths


def _diff_section_for(patch_text: str, touched_path: str) -> str:
    """Diff text for one touched file (whole patch without ``diff --git``)."""
    if "diff --git" not in patch_text:
        return patch_text
    want = _posix_rel(touched_path)
    for chunk in patch_text.split("diff --git ")[1:]:
        header = chunk.splitlines()[0] if chunk.splitlines() else ""
        names = set()
        for part in header.split():
            if part.startswith("a/") or part.startswith("b/"):
                names.add(_posix_rel(part[2:]))
            else:
                names.add(_posix_rel(part))
        if want in names:
            return chunk
    return ""


def _pyproject_pytest_touched(patch_text: str, touched_path: str) -> bool:
    """Whether the patch modifies the ``[tool.pytest*]`` section of pyproject.

    True when an added/removed line names pytest (e.g. the section header
    itself) or when an added line sits inside a ``[tool.pytest*]`` section
    (tracked through context lines and ``@@`` section hints).
    """
    current: str | None = None
    for line in _diff_section_for(patch_text, touched_path).splitlines():
        if line.startswith("diff --git") or line.startswith(("+++", "---")):
            continue
        if line.startswith("@@"):
            found = re.search(r"\[tool\.pytest[^\]]*\]", line)
            if found:
                current = found.group(0)
            continue
        if not line or line[0] not in (" ", "+", "-"):
            continue
        body = line[1:].strip()
        if body.startswith("[") and body.endswith("]"):
            current = body
            if line[0] in ("+", "-") and "pytest" in body.lower():
                return True
            continue
        if line[0] == "+" and current is not None:
            if current.lower().startswith("[tool.pytest"):
                return True
        if line[0] in ("+", "-") and "pytest" in line.lower():
            return True
    return False


def _test_integrity_violations(
    touched: Sequence[str], test_file: Path | str | None, patch_text: str
) -> list[str]:
    """Touched paths that forbid trusting the regression test.

    The regression test file itself, any ``conftest.py``, ``pytest.ini``,
    ``tox.ini`` or ``setup.cfg`` anywhere, or a ``pyproject.toml`` whose
    ``[tool.pytest*]`` section the patch modifies.
    """
    if not test_file or not str(test_file).strip():
        return []
    target = _posix_rel(str(test_file))
    violations: set[str] = set()
    for raw in touched:
        norm = _posix_rel(raw)
        base = norm.split("/")[-1].lower()
        if norm == target:
            violations.add(norm)
        elif base == "conftest.py":
            violations.add(norm)
        elif base in _CONFIG_BASENAMES:
            violations.add(norm)
        elif base == "pyproject.toml" and _pyproject_pytest_touched(
            patch_text, norm
        ):
            violations.add(norm)
    return sorted(violations)


@dataclass
class F2PCounts:
    """Pass/fail/skip tallies from one Fail-to-Pass ``--junitxml`` file."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0


def _count_f2p_cases(xml_path: Path) -> F2PCounts | None:
    """Tally every ``<testcase>`` in a JUnit file (None when unreadable).

    F2P commands are isolated to the target test file, so every collected
    case is one of its tests. A ``<skipped>`` child covers skipped and
    xfailed alike; either one means the test did not really pass.
    """
    try:
        tree = ET.parse(xml_path)
    except (OSError, ET.ParseError):
        return None
    counts = F2PCounts()
    for tc in tree.getroot().iter("testcase"):
        counts.total += 1
        failed = tc.find("failure") is not None
        errored = tc.find("error") is not None
        skipped = tc.find("skipped") is not None
        if failed:
            counts.failed += 1
        if errored:
            counts.errored += 1
        if skipped:
            counts.skipped += 1
        if not (failed or errored or skipped):
            counts.passed += 1
    return counts


def _parse_f2p_junit(
    cmd: Sequence[str], default: Path, repo: Path
) -> F2PCounts | None:
    """Tallies from the F2P run's JUnit output, or None when unavailable.

    Honors a custom ``--junitxml`` already present in the stored command;
    non-pytest commands never produce JUnit, so they fall back to the
    exit code as before.
    """
    for candidate in _junit_candidates(cmd, default, repo):
        counts = _count_f2p_cases(candidate)
        if counts is not None:
            return counts
    return None


def _f2p_cmd_with_junit(cmd: Sequence[str], xml_path: Path) -> list[str]:
    """Append ``--junitxml`` to a Fail-to-Pass command (pytest only)."""
    parts = [str(p) for p in cmd]
    if not _is_pytest_cmd(parts):
        return parts
    for tok in parts:
        if tok == "--junitxml" or tok.startswith("--junitxml="):
            return parts
    return [*parts, "--junitxml", str(xml_path)]


def _base_failed(base_rc: int | None, counts: F2PCounts | None) -> bool:
    """Whether the base run really failed: nonzero exit plus proof in XML.

    Without JUnit (non-pytest commands) the exit code alone decides; with
    JUnit the target file must show at least one failed or errored test.
    """
    if base_rc is None or base_rc == 0:
        return False
    if counts is None:
        return True
    return (counts.failed + counts.errored) >= 1


def _patched_clean(counts: F2PCounts) -> bool:
    """Whether the patched run really passed: at least one passed test and
    zero failed, errored, skipped, or xfailed."""
    return (
        counts.passed >= 1
        and counts.failed == 0
        and counts.errored == 0
        and counts.skipped == 0
    )


def _fmt_p2p_counts(counts: F2PCounts | None, rc: int | None) -> str:
    """One-line tally for a preservation-run reason (``<counts>``)."""
    if counts is None:
        return f"exit {rc}" if rc is not None else "no test results recorded"
    return (
        f"passed={counts.passed}, failed={counts.failed}, "
        f"errored={counts.errored}, skipped={counts.skipped}"
    )


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
    suite_path: Path | str | None = None,
    preserve_file: Path | str | None = None,
) -> VerifyResult:
    """Fail-to-Pass check of a patch inside a workspace git repo.

    The patch is inspected before anything runs: when it touches the
    regression test file, any ``conftest.py``, ``pytest.ini``,
    ``tox.ini``, ``setup.cfg`` or the ``[tool.pytest*]`` section of
    ``pyproject.toml``, the verdict is ``UNPROVEN`` and no test runs.
    When it touches ``preserve_file`` the verdict is ``UNPROVEN``
with reason ``patch modifies the preservation tests: <path>``
and no test runs.

    Fail-to-Pass runs first, before any suite run: the F2P check runs on
    ``test_file`` alone (extra files in ``test_cmd`` are dropped), on the
    base commit and after the patch, each with ``--junitxml``. The XML --
    not the exit code -- decides: the base run must show at least one
    failed/errored test, and the patched run needs at least one passed
    test with zero failed/errored/skipped/xfailed (recorded as
    ``f2p_tests_passed``/``f2p_tests_skipped``). Only when the test fails
    on base and passes with the patch does the regression suite run.
    A suite timeout or error therefore keeps the F2P outcome: the verdict
    is ``ERROR``
    with a reason naming the suite step, while ``fail_before``,
    ``pass_after`` and ``f2p_pass`` still record what F2P showed.

    When ``suite_cmd`` and ``baseline_commit`` are both given, the
    existing suite runs on the baseline commit and after the patch with
    ``--junitxml``; only tests that passed on the baseline and fail after
    the patch count as regressions, and baseline failures are reported via
    :attr:`VerifyResult.baseline_failures` instead.

    ``preserve_file`` (a repo-relative path like ``test_file``) pins down
    existing behaviour next to the change. When it is ``None`` or blank,
    behaviour is exactly what it is without it. When it is given, the file
    runs alone on the base commit (after the base Fail-to-Pass run, before
    the patch is applied) and again with the patch applied (after the
    patched Fail-to-Pass run passes, before the suite runs), built and
    parsed the same way as the Fail-to-Pass runs. It must be clean both
    times (at least one passed, zero failed/errored/skipped): a missing
    file is ``ERROR`` (``preservation file not found: <path>``), a base
    run that is not clean is ``UNPROVEN`` (``preservation tests do not
    pass on base: <counts>``), and a patched run that is not clean is
    ``REGRESSION`` (``preservation tests pass on base but fail after
    patch: <counts>``). Timeouts and infra exit codes follow the
    Fail-to-Pass handling. ``p2p_checked`` is True only when both
    preservation runs happened and were clean; ``p2p_tests_passed`` is
    the passed count of the patched preservation run.
    ``suite_path`` overrides the regression-suite target; otherwise the
    scope comes from :func:`suite_scope_for` and is recorded as
    :attr:`VerifyResult.suite_scope`.

    ``timeout_s`` bounds the per-test Fail-to-Pass runs (and git plumbing);
    ``suite_timeout_s`` bounds the suite runs (defaults to ``timeout_s``).
    A timeout is an ``ERROR`` verdict with reason
    ``"<step> timed out after <N> s"``, never an exception.
    """
    suite_timeout: float = suite_timeout_s if suite_timeout_s is not None else timeout_s
    logs: list[str] = []
    env: dict[str, str] = {}
    tmpdir: Path | None = None
    pytest_basetemp: Path | None = None
    pytest_config: str | None = None
    orig_ref = ""
    initial_untracked: set[str] = set()
    scope = suite_scope_for(test_file, suite_path)
    suite_flags: list[str] = []

    def finish(result: VerifyResult) -> VerifyResult:
        result.suite_flags = list(suite_flags)
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
                    f2p_pass=result.f2p_pass,
                    regressions=result.regressions,
                    log=result.log[-EVENT_LOG_CHARS:],
                ),
                traces_dir,
            )
        return result

    def error(reason: str) -> VerifyResult:
        return finish(VerifyResult("ERROR", reason, "".join(logs), suite_scope=scope))

    def _f2p_fields(
        b_rc: int | None, p_rc: int | None
    ) -> tuple[bool | None, bool | None, bool]:
        fail = (b_rc != 0) if b_rc is not None else None
        passed = (p_rc == 0) if p_rc is not None else None
        return fail, passed, bool(fail and passed)

    counted: list[int] = [0, 0]  # patched F2P [passed, skipped], set below
    p2p_passed: list[int] = [0]  # patched P2P passed count, set below
    p2p_ok: list[bool] = [False]  # True once both preservation runs are clean

    def decided(
        verdict: Verdict,
        reason: str,
        b_rc: int | None,
        p_rc: int | None,
        regressions: int = 0,
        baseline_failures: Sequence[str] = (),
        f2p_tests_passed: int | None = None,
        p2p_checked: bool | None = None,
        p2p_tests_passed: int | None = None,
        f2p_tests_skipped: int | None = None,
    ) -> VerifyResult:
        fail, passed, ok = _f2p_fields(b_rc, p_rc)
        return finish(
            VerifyResult(
                verdict,
                reason,
                "".join(logs),
                base_returncode=b_rc,
                patched_returncode=p_rc,
                regressions=regressions,
                baseline_failures=list(baseline_failures),
                fail_before=fail,
                pass_after=passed,
                f2p_pass=ok,
                f2p_tests_passed=(
                    counted[0] if f2p_tests_passed is None else f2p_tests_passed
                ),
                f2p_tests_skipped=(
                    counted[1] if f2p_tests_skipped is None else f2p_tests_skipped
                ),
                p2p_checked=(
                    p2p_ok[0] if p2p_checked is None else p2p_checked
                ),
                p2p_tests_passed=(
                    p2p_passed[0] if p2p_tests_passed is None else p2p_tests_passed
                ),
                suite_scope=scope,
            )
        )

    def drop_new_untracked() -> None:
        """Delete untracked files the patch (or a suite run) introduced."""
        current = _git_out(repo, env, timeout_s, "ls-files", "--others", "--exclude-standard")
        if current is None:
            return
        for rel in current.splitlines():
            if rel and rel not in initial_untracked:
                victim = repo / rel
                try:
                    if victim.is_symlink() or victim.is_file():
                        victim.unlink()
                    elif victim.is_dir():
                        shutil.rmtree(victim, ignore_errors=True)
                except OSError:
                    pass

    def reset_to(commit: str, label: str) -> str | None:
        """Force-checkout ``commit`` clean; None on success, else an ERROR reason."""
        rc, out = _run(["git", "checkout", "-qf", commit], repo, env, timeout_s)
        logs.append(out)
        if rc is None and _is_timeout_log(out):
            return f"git checkout timed out after {_fmt_timeout(timeout_s)}"
        if rc != 0:
            return f"cannot checkout {label} commit: {commit}"
        drop_new_untracked()
        return None

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
        env = target_env(
            extra={"TMP": str(tmpdir), "TEMP": str(tmpdir), "TMPDIR": str(tmpdir)}
        )

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

        try:
            if run_id is not None:
                iso_root = workspace / run_id / ".issa-tmp"
                ini_candidate = workspace / run_id / ".issa-pytest.ini"
            else:
                iso_root = tmpdir / ".issa-tmp"
                ini_candidate = tmpdir / ".issa-pytest.ini"
            iso_root.mkdir(parents=True, exist_ok=True)
            pytest_basetemp = Path(tempfile.mkdtemp(prefix="basetemp-", dir=iso_root))
            if not _repo_has_pytest_config(repo):
                ini_candidate.write_text("[pytest]\n", encoding="utf-8")
                pytest_config = str(ini_candidate)
        except OSError as exc:
            return error(f"cannot isolate pytest run: {exc}")

        def _iso(cmd: Sequence[str]) -> list[str]:
            """Add execution-time pytest isolation flags to one command."""
            assert pytest_basetemp is not None
            return _with_isolation_flags(
                cmd,
                rootdir=str(repo),
                basetemp=str(pytest_basetemp),
                config_file=pytest_config,
            )

        if not test_file or not str(test_file).strip():
            return finish(VerifyResult("NO_TEST", "no test file given", "".join(logs)))
        touched_paths = _patch_touched_paths(patch_text or "")
        tampered = _test_integrity_violations(touched_paths, test_file, patch_text or "")
        if tampered:
            shown = ", ".join(tampered)
            return decided(
                "UNPROVEN",
                f"patch modifies the regression test or test configuration: {shown}",
                None,
                None,
            )
        preserve = str(preserve_file).strip() if preserve_file is not None else ""
        if preserve:
            preserve_norm = _posix_rel(preserve)
            if preserve_norm in touched_paths:
                return decided(
                    "UNPROVEN",
                    f"patch modifies the preservation tests: {preserve_norm}",
                    None,
                    None,
                )
        # The allowlisted environment about to be passed to target
        # code (names only, never values).
        logs.append(f"target env: {', '.join(sorted(env))}\n")
        test_abs = (repo / str(test_file)).resolve()
        preserve_abs: Path | None = None
        if preserve:
            preserve_abs = (repo / preserve).resolve()
        if test_abs != repo and repo not in test_abs.parents:
            return error(f"test path escapes repo: {test_file}")
        if preserve_abs is not None:
            if preserve_abs != repo and repo not in preserve_abs.parents:
                return error(f"preservation path escapes repo: {preserve}")

        use_baseline = suite_cmd is not None and baseline_commit is not None
        if suite_cmd is not None:
            # Suite-only flags from the target venv's plugins (Fail-to-Pass
            # never gets them): --timeout per test, -n auto in parallel.
            suite_flags.extend(_suite_extra_flags(repo, env, timeout_s))
            suite_cmd = _with_suite_flags(list(suite_cmd), suite_flags)
        f2p_cmd = _f2p_cmd(_resolve_cmd(list(test_cmd), repo), test_file, repo)

        # Fail-to-Pass runs before any suite run, so a slow or hanging
        # suite can never hide whether the fix works.
        failed = reset_to(base_commit, "base")
        if failed is not None:
            return error(failed)

        base_exists = test_abs.is_file()
        base_rc: int | None = None
        base_counts: F2PCounts | None = None
        if base_exists:
            base_xml = tmpdir / "f2p-base.xml"
            base_f2p = _iso(_f2p_cmd_with_junit(f2p_cmd, base_xml))
            base_rc, out = _run(base_f2p, repo, env, timeout_s)
            logs.append(out)
            if base_rc is None:
                if _is_timeout_log(out):
                    return error(f"test command timed out after {_fmt_timeout(timeout_s)}")
                return error(
                    f"test command did not complete on base: {_last_output_line(out)}"
                )
            if base_rc in _INFRA_EXIT_CODES:
                return error(
                    f"{_INFRA_PREFIX}: pytest exited with code {base_rc} "
                    f"on base: {_last_output_line(out)}"
                )
            base_counts = _parse_f2p_junit(base_f2p, base_xml, repo)
            if (
                base_rc != 0
                and base_counts is not None
                and base_counts.passed == 0
                and base_counts.failed == 0
            ):
                detail = (
                    f"passed=0, failed=0, errored={base_counts.errored}, "
                    f"skipped={base_counts.skipped}"
                )
                return decided(
                    "ERROR",
                    "test environment incomplete: no test ran to an assertion "
                    f"on base in {test_file} ({detail}): "
                    f"{_last_output_line(out)}",
                    base_rc,
                    None,
                )
            if base_rc != 0 and not _base_failed(base_rc, base_counts):
                detail = (
                    f"passed={base_counts.passed}, skipped={base_counts.skipped}"
                    if base_counts is not None
                    else "no test results recorded"
                )
                return decided(
                    "UNPROVEN",
                    f"test did not fail on base: no failing tests ran "
                    f"in {test_file} ({detail})",
                    base_rc,
                    None,
                )

        if preserve:
            assert preserve_abs is not None
            if not preserve_abs.is_file():
                return error(f"preservation file not found: {preserve_norm}")
            p2p_base_xml = tmpdir / "p2p-base.xml"
            p2p_base_cmd = _iso(
                _f2p_cmd_with_junit(
                    _f2p_cmd(_resolve_cmd(list(test_cmd), repo), preserve, repo),
                    p2p_base_xml,
                )
            )
            p2p_base_rc, out = _run(p2p_base_cmd, repo, env, timeout_s)
            logs.append(out)
            if p2p_base_rc is None:
                if _is_timeout_log(out):
                    return error(f"test command timed out after {_fmt_timeout(timeout_s)}")
                return error(
                    f"test command did not complete on base: {_last_output_line(out)}"
                )
            if p2p_base_rc in _INFRA_EXIT_CODES:
                return error(
                    f"{_INFRA_PREFIX}: pytest exited with code {p2p_base_rc} "
                    f"on base: {_last_output_line(out)}"
                )
            p2p_base_counts = _parse_f2p_junit(p2p_base_cmd, p2p_base_xml, repo)
            if p2p_base_counts is not None:
                p2p_base_clean = _patched_clean(p2p_base_counts)
            else:
                p2p_base_clean = p2p_base_rc == 0
            if not p2p_base_clean:
                return decided(
                    "UNPROVEN",
                    "preservation tests do not pass on base: "
                    f"{_fmt_p2p_counts(p2p_base_counts, p2p_base_rc)}",
                    base_rc,
                    None,
                )
        patch_file: Path | None = None
        if patch_text:
            patch_file = tmpdir / "fix.patch"
            patch_file.write_text(
                patch_text if patch_text.endswith("\n") else patch_text + "\n",
                encoding="utf-8",
            )

        def apply_staged_patch() -> VerifyResult | None:
            """Apply the staged patch; None on success, else an ERROR result."""
            assert patch_file is not None
            rc, out = _run(["git", "apply", str(patch_file)], repo, env, timeout_s)
            logs.append(out)
            if rc is None and _is_timeout_log(out):
                return decided(
                    "ERROR",
                    f"git apply timed out after {_fmt_timeout(timeout_s)}",
                    base_rc,
                    None,
                )
            if rc != 0:
                return decided("ERROR", "patch does not apply", base_rc, None)
            return None

        if patch_file is not None:
            apply_failed = apply_staged_patch()
            if apply_failed is not None:
                return apply_failed

        patched_exists = test_abs.is_file()
        if not base_exists and not patched_exists:
            return decided("NO_TEST", f"test file not found: {test_file}", base_rc, None)
        if not patched_exists:
            return decided(
                "NO_TEST", f"test file missing after patch: {test_file}", base_rc, None
            )

        collect_rc, out = _run(
            _iso(_collect_only_cmd(repo, test_file)), repo, env, timeout_s
        )
        logs.append(out)
        if collect_rc is None:
            if _is_timeout_log(out):
                return decided(
                    "ERROR",
                    f"pytest --collect-only timed out after {_fmt_timeout(timeout_s)}",
                    base_rc,
                    None,
                )
            return error(
                f"{_INFRA_PREFIX}: pytest --collect-only did not complete "
                f"for {test_file}: {_last_output_line(out)}"
            )
        if collect_rc != 0:
            return error(
                f"{_INFRA_PREFIX}: pytest cannot collect "
                f"{test_file}: {_last_output_line(out)}"
            )

        patched_xml = tmpdir / "f2p-patched.xml"
        patched_f2p = _iso(_f2p_cmd_with_junit(f2p_cmd, patched_xml))
        patched_rc, out = _run(patched_f2p, repo, env, timeout_s)
        logs.append(out)
        if patched_rc is None:
            if _is_timeout_log(out):
                return decided(
                    "ERROR",
                    f"test command timed out after {_fmt_timeout(timeout_s)}",
                    base_rc,
                    None,
                )
            return decided(
                "ERROR",
                f"test command did not complete after patch: {_last_output_line(out)}",
                base_rc,
                None,
            )
        if patched_rc in _INFRA_EXIT_CODES:
            return error(
                f"{_INFRA_PREFIX}: pytest exited with code {patched_rc} "
                f"after patch: {_last_output_line(out)}"
            )
        patched_counts = _parse_f2p_junit(patched_f2p, patched_xml, repo)
        counted[0] = patched_counts.passed if patched_counts is not None else 0
        counted[1] = patched_counts.skipped if patched_counts is not None else 0

        if base_rc == 0:
            return decided("UNPROVEN", "test passes on base", base_rc, patched_rc)
        if base_rc is None:
            if patched_rc == 0:
                return decided(
                    "UNPROVEN",
                    "test added by patch; no base failure to compare",
                    base_rc,
                    patched_rc,
                )
            return decided(
                "ERROR", "test added by patch still fails", base_rc, patched_rc
            )
        if patched_rc != 0:
            return decided("ERROR", "test still fails after patch", base_rc, patched_rc)
        if patched_counts is not None and not _patched_clean(patched_counts):
            if patched_counts.failed + patched_counts.errored > 0:
                reason = "test still fails after patch"
            elif patched_counts.skipped > 0:
                reason = (
                    f"test skipped after patch: no conclusive pass "
                    f"in {test_file} (passed={patched_counts.passed}, "
                    f"skipped={patched_counts.skipped})"
                )
            else:
                reason = (
                    f"test produced no passing tests after patch: "
                    f"no tests ran in {test_file}"
                )
            return decided("ERROR", reason, base_rc, patched_rc)

        if preserve:
            assert preserve_abs is not None
            if not preserve_abs.is_file():
                return decided(
                    "REGRESSION",
                    "preservation tests pass on base but fail after patch: "
                    "preservation file not found",
                    base_rc,
                    patched_rc,
                    regressions=1,
                )
            p2p_patched_xml = tmpdir / "p2p-patched.xml"
            p2p_patched_cmd = _iso(
                _f2p_cmd_with_junit(
                    _f2p_cmd(_resolve_cmd(list(test_cmd), repo), preserve, repo),
                    p2p_patched_xml,
                )
            )
            p2p_patched_rc, out = _run(p2p_patched_cmd, repo, env, timeout_s)
            logs.append(out)
            if p2p_patched_rc is None:
                if _is_timeout_log(out):
                    return decided(
                        "ERROR",
                        f"test command timed out after {_fmt_timeout(timeout_s)}",
                        base_rc,
                        patched_rc,
                    )
                return decided(
                    "ERROR",
                    "test command did not complete after patch: "
                    f"{_last_output_line(out)}",
                    base_rc,
                    patched_rc,
                )
            if p2p_patched_rc in _INFRA_EXIT_CODES:
                return error(
                    f"{_INFRA_PREFIX}: pytest exited with code {p2p_patched_rc} "
                    f"after patch: {_last_output_line(out)}"
                )
            p2p_patched_counts = _parse_f2p_junit(
                p2p_patched_cmd, p2p_patched_xml, repo
            )
            if p2p_patched_counts is not None:
                p2p_clean = _patched_clean(p2p_patched_counts)
                p2p_failed = p2p_patched_counts.failed + p2p_patched_counts.errored
            else:
                p2p_clean = p2p_patched_rc == 0
                p2p_failed = 0 if p2p_clean else 1
            if not p2p_clean:
                return decided(
                    "REGRESSION",
                    "preservation tests pass on base but fail after patch: "
                    f"{_fmt_p2p_counts(p2p_patched_counts, p2p_patched_rc)}",
                    base_rc,
                    patched_rc,
                    regressions=max(1, p2p_failed),
                )
            if p2p_patched_counts is not None:
                p2p_passed[0] = p2p_patched_counts.passed
            p2p_ok[0] = True
        # Fail-to-Pass already passed (base failed, patch fixed it); now the
        # regression suite runs. Any suite timeout or error keeps the F2P
        # outcome via decided(), with a reason naming the suite step.
        assert base_rc is not None and base_rc != 0 and patched_rc == 0
        base_suite_rc: int | None = None
        baseline_all: set[str] = set()
        baseline_failed: set[str] = set()
        baseline_suite_rc: int | None = None
        baseline_xml = tmpdir / "baseline-junit.xml"
        junit_usable = False
        if suite_cmd is None:
            return decided(
                "VERIFIED", "fail-to-pass with no regressions", base_rc, patched_rc
            )
        if use_baseline:
            assert suite_cmd is not None and baseline_commit is not None
            failed = reset_to(baseline_commit, "baseline")
            if failed is not None:
                return decided("ERROR", failed, base_rc, patched_rc)
            suite_resolved = _resolve_cmd(list(suite_cmd), repo)
            baseline_suite_rc, out = _run(
                _iso(_suite_cmd_with_junit(suite_resolved, baseline_xml)),
                repo,
                env,
                suite_timeout,
            )
            logs.append(out)
            if baseline_suite_rc is None:
                if _is_timeout_log(out):
                    return decided(
                        "ERROR",
                        f"suite command timed out after {_fmt_timeout(suite_timeout)}",
                        base_rc,
                        patched_rc,
                    )
                return decided(
                    "ERROR",
                    f"suite command did not complete on baseline: "
                    f"{_last_output_line(out)}",
                    base_rc,
                    patched_rc,
                )
            parsed = _parse_suite_junit(suite_resolved, baseline_xml, repo)
            if parsed is None:
                return error(
                    f"{_INFRA_PREFIX}: baseline suite produced no JUnit file: "
                    f"{_last_output_line(out)}"
                )
            junit_usable = True
            baseline_all, baseline_failed = parsed
            failed = reset_to(base_commit, "base")
            if failed is not None:
                return decided("ERROR", failed, base_rc, patched_rc)
            if patch_file is not None:
                apply_failed = apply_staged_patch()
                if apply_failed is not None:
                    return apply_failed
        else:
            failed = reset_to(base_commit, "base")
            if failed is not None:
                return decided("ERROR", failed, base_rc, patched_rc)
            base_suite_rc, out = _run(_iso(suite_cmd), repo, env, suite_timeout)
            logs.append(out)
            if base_suite_rc is None:
                if _is_timeout_log(out):
                    return decided(
                        "ERROR",
                        f"suite command timed out after {_fmt_timeout(suite_timeout)}",
                        base_rc,
                        patched_rc,
                    )
                return decided(
                    "ERROR",
                    f"suite command did not complete on base: {_last_output_line(out)}",
                    base_rc,
                    patched_rc,
                )
            if patch_file is not None:
                apply_failed = apply_staged_patch()
                if apply_failed is not None:
                    return apply_failed

        patched_suite_rc: int | None = None
        patched_all: set[str] = set()
        patched_failed: set[str] = set()
        patched_xml = tmpdir / "patched-junit.xml"
        if not use_baseline:
            patched_suite_rc, out = _run(_iso(suite_cmd), repo, env, suite_timeout)
            logs.append(out)
            if patched_suite_rc is None:
                if _is_timeout_log(out):
                    return decided(
                        "ERROR",
                        f"suite command timed out after {_fmt_timeout(suite_timeout)}",
                        base_rc,
                        patched_rc,
                    )
                return decided(
                    "ERROR",
                    f"suite command did not complete after patch: "
                    f"{_last_output_line(out)}",
                    base_rc,
                    patched_rc,
                )
        else:
            assert suite_cmd is not None
            suite_resolved = _resolve_cmd(list(suite_cmd), repo)
            patched_suite_rc, out = _run(
                _iso(_suite_cmd_with_junit(suite_resolved, patched_xml)),
                repo,
                env,
                suite_timeout,
            )
            logs.append(out)
            if patched_suite_rc is None:
                if _is_timeout_log(out):
                    return decided(
                        "ERROR",
                        f"suite command timed out after {_fmt_timeout(suite_timeout)}",
                        base_rc,
                        patched_rc,
                    )
                return decided(
                    "ERROR",
                    f"suite command did not complete after patch: "
                    f"{_last_output_line(out)}",
                    base_rc,
                    patched_rc,
                )
            parsed = _parse_suite_junit(suite_resolved, patched_xml, repo)
            if parsed is not None and junit_usable:
                patched_all, patched_failed = parsed
            else:
                junit_usable = False

        if use_baseline:
            baseline_failures = sorted(baseline_failed)
            if junit_usable:
                baseline_passed = baseline_all - baseline_failed
                regressed = sorted(patched_failed & baseline_passed)
                if regressed:
                    shown = ", ".join(regressed[:5])
                    return decided(
                        "REGRESSION",
                        f"tests passed on baseline but fail after patch: {shown}",
                        base_rc,
                        patched_rc,
                        regressions=len(regressed),
                        baseline_failures=baseline_failures,
                    )
                return decided(
                    "VERIFIED",
                    "fail-to-pass with no regressions",
                    base_rc,
                    patched_rc,
                    baseline_failures=baseline_failures,
                )
            if baseline_suite_rc == 0 and patched_suite_rc != 0:
                return decided(
                    "REGRESSION",
                    "pre-existing tests pass on baseline but fail after patch",
                    base_rc,
                    patched_rc,
                    regressions=1,
                    baseline_failures=baseline_failures,
                )
            return decided(
                "VERIFIED",
                "fail-to-pass with no regressions",
                base_rc,
                patched_rc,
                baseline_failures=baseline_failures,
            )
        if base_suite_rc == 0 and patched_suite_rc != 0:
            return decided(
                "REGRESSION",
                "pre-existing tests pass on base but fail after patch",
                base_rc,
                patched_rc,
                regressions=1,
            )
        return decided(
            "VERIFIED", "fail-to-pass with no regressions", base_rc, patched_rc
        )
    except OSError as exc:
        return error(f"filesystem error: {exc}")
    finally:
        if pytest_basetemp is not None:
            shutil.rmtree(pytest_basetemp, ignore_errors=True)
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
