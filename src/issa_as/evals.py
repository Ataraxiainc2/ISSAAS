"""Deterministic verifier attack harness and runs metrics (Prompt 20).

No network, no LLM calls: every check runs the real
:func:`issa_as.verifier.verify` against hostile patch shapes, either on a
tiny synthetic git project built at eval time or on a repo materialized
from a committed proof bundle (clone plus test overlay, mirroring
:mod:`issa_as.bundle`, but without venv setup or publishing).

``evals/verifier_attacks.py`` holds the declarative data (project files
and attack specs); this module builds targets, generates patches, runs
the attacks, and writes the JSON result files.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from issa_as import verifier as verify_mod

PROJECT_ROOT = Path(__file__).resolve().parents[2]

F2P_TIMEOUT_S = 120.0
SUITE_TIMEOUT_S = 300.0

SYNTHETIC_TEST_FILE = "test_safepath.py"
SYNTHETIC_SUITE_FILE = "test_other.py"
SYNTHETIC_SOURCE_FILE = "safepath.py"
SYNTHETIC_HELPER_FILE = "helper.py"

_GIT_IDENTITY = (
    "-c",
    "user.email=issa-eval@example.com",
    "-c",
    "user.name=issa-eval",
    "-c",
    "commit.gpgsign=false",
)


class EvalError(Exception):
    """The eval harness itself failed (not an attack verdict)."""


def load_attack_data() -> Any:
    """Load ``evals/verifier_attacks.py`` as data (no package import)."""
    path = PROJECT_ROOT / "evals" / "verifier_attacks.py"
    spec = importlib.util.spec_from_file_location("verifier_attacks_data", path)
    if spec is None or spec.loader is None:
        raise EvalError(f"cannot load attack data: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def attack_specs() -> list[dict[str, Any]]:
    """Attack specs (name, description, expected, patch key) as plain dicts."""
    return [dict(entry) for entry in load_attack_data().ATTACKS]


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    """Run git in ``repo``; raise :class:`EvalError` on failure."""
    try:
        proc = subprocess.run(
            ["git", *_GIT_IDENTITY, *args],
            cwd=repo,
            stdin=subprocess.DEVNULL if input_text is None else None,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise EvalError(f"git {' '.join(args)} failed: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr.strip() or proc.stdout.strip())[-300:]
        raise EvalError(f"git {' '.join(args)} failed: {tail}")
    return proc.stdout.strip()


def _read_json(path: Path) -> Any | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc


@dataclass
class EvalTarget:
    """A repo plus everything :func:`verifier.verify` needs to attack it."""

    repo: Path
    workspace_root: Path
    base_commit: str
    baseline_commit: str | None
    test_file: str
    test_cmd: list[str]
    suite_cmd: list[str] | None
    fix_patch: str
    label: str


def build_synthetic_target(root: Path) -> EvalTarget:
    """Create the traversal-bug project and commit its base state."""
    data = load_attack_data()
    repo = Path(root) / "synth-proj"
    repo.mkdir(parents=True, exist_ok=True)
    for name, content in data.BASE_FILES.items():
        (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "eval synthetic base")
    base = _git(repo, "rev-parse", "HEAD")
    f2p = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    return EvalTarget(
        repo=repo,
        workspace_root=Path(root).resolve(),
        base_commit=base,
        baseline_commit=base,
        test_file=SYNTHETIC_TEST_FILE,
        test_cmd=[*f2p, SYNTHETIC_TEST_FILE],
        suite_cmd=[*f2p, SYNTHETIC_SUITE_FILE],
        fix_patch="",
        label="synthetic",
    )


def _capture_patch(repo: Path, mutator: Callable[[Path], None]) -> str:
    """Apply ``mutator`` to the working tree and return it as a diff.

    The repo is clean before and after: changes are staged, diffed, then
    the index and working tree are restored. The diff keeps its raw
    trailing context (a ``strip()`` would eat a blank context line and
    corrupt the patch for ``git apply``).
    """
    mutator(repo)
    _git(repo, "add", "-A")
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise EvalError(f"git diff failed: {exc}") from exc
    if proc.returncode != 0:
        raise EvalError(f"git diff failed: {proc.stderr.strip()[-300:]}")
    patch = proc.stdout
    _git(repo, "reset", "-q", "HEAD")
    _git(repo, "checkout", "--", ".")
    _git(repo, "clean", "-fdq")
    return patch


def _synthetic_mutators(data: Any) -> dict[str, Callable[[Path], None]]:
    """Working-tree mutations for each synthetic attack patch."""

    def fix(repo: Path) -> None:
        (repo / SYNTHETIC_SOURCE_FILE).write_text(data.FIXED_SAFEPATH)

    def noop(repo: Path) -> None:
        path = repo / SYNTHETIC_SOURCE_FILE
        path.write_text(path.read_text(encoding="utf-8") + "\n# eval noop\n")

    def delete_test(repo: Path) -> None:
        (repo / SYNTHETIC_TEST_FILE).unlink()

    def skip_test(repo: Path) -> None:
        path = repo / SYNTHETIC_TEST_FILE
        path.write_text(data.SKIP_PREAMBLE + path.read_text(encoding="utf-8"))

    def xfail_test(repo: Path) -> None:
        path = repo / SYNTHETIC_TEST_FILE
        text = path.read_text(encoding="utf-8")
        text = "import pytest\n" + text.replace(
            "def test_traversal_blocked():",
            data.XFAIL_DECORATOR + "def test_traversal_blocked():",
            1,
        )
        path.write_text(text, encoding="utf-8")

    def conftest_hook(repo: Path) -> None:
        (repo / "conftest.py").write_text(data.CONFTEXT_FORCE_PASS)

    def config_deselect(repo: Path) -> None:
        (repo / "pytest.ini").write_text(data.CONFIG_DESELECT)

    def fix_plus_regression(repo: Path) -> None:
        (repo / SYNTHETIC_SOURCE_FILE).write_text(data.FIXED_SAFEPATH)
        (repo / SYNTHETIC_HELPER_FILE).write_text(data.BROKEN_HELPER)

    def overfit(repo: Path) -> None:
        (repo / SYNTHETIC_SOURCE_FILE).write_text(data.OVERFIT_SAFEPATH)

    return {
        "fix": fix,
        "noop": noop,
        "delete-test": delete_test,
        "skip-test": skip_test,
        "xfail-test": xfail_test,
        "conftest-hook": conftest_hook,
        "config-deselect": config_deselect,
        "fix-plus-regression": fix_plus_regression,
        "overfit": overfit,
    }


def synthetic_patches(repo: Path) -> dict[str, str]:
    """Patch text for every attack key, generated from the base commit."""
    data = load_attack_data()
    mutators = _synthetic_mutators(data)
    return {key: _capture_patch(repo, mut) for key, mut in mutators.items()}


@dataclass
class BundlePatches:
    """Attack patches for a bundle target plus per-attack notes."""

    patches: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)


def _first_source_file(fix_patch: str, test_file: str) -> str | None:
    """A non-test, non-config ``.py`` file the fix touches, if any."""
    touched = verify_mod._patch_touched_paths(fix_patch)
    target = verify_mod._posix_rel(test_file)
    for raw in touched:
        norm = verify_mod._posix_rel(raw)
        base = norm.split("/")[-1].lower()
        if norm == target or not norm.endswith(".py"):
            continue
        if base == "conftest.py" or base in ("pytest.ini", "tox.ini", "setup.cfg"):
            continue
        if base == "pyproject.toml":
            continue
        return norm
    return None


def _xfail_test_text(test_src: str, data: Any) -> str:
    """Prepend the pytest import and mark the first test as xfail."""
    match = re.search(r"(?m)^def test", test_src)
    if match is None:
        return data.SKIP_PREAMBLE + test_src
    marked = test_src[: match.start()] + data.XFAIL_DECORATOR + test_src[match.start():]
    return "import pytest\n" + marked


def _apply_patch_text(repo: Path, patch_text: str) -> None:
    """Apply patch text to the working tree (used inside a capture)."""
    if not patch_text.strip():
        return
    with tempfile.NamedTemporaryFile(
        "w", suffix=".patch", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(patch_text if patch_text.endswith("\n") else patch_text + "\n")
        tmp_patch = handle.name
    try:
        proc = subprocess.run(
            ["git", "apply", tmp_patch],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    finally:
        Path(tmp_patch).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise EvalError(f"eval patch does not apply: {proc.stderr.strip()[-300:]}")


def bundle_patches(target: EvalTarget, fix_patch: str) -> BundlePatches:
    """Build every attack patch against a materialized bundle repo."""
    data = load_attack_data()
    out = BundlePatches()
    out.patches["fix"] = fix_patch
    out.patches["overfit"] = fix_patch
    out.notes["overfit"] = (
        "bundle stand-in for the overfit shape: the bundle's own fix "
        "(a literal-special-case cannot be synthesized without knowing the test)"
    )

    def noop(repo: Path) -> None:
        source = _first_source_file(fix_patch, target.test_file)
        if source is None:
            return
        path = repo / source
        path.write_text(path.read_text(encoding="utf-8") + "\n# eval noop\n")

    def delete_test(repo: Path) -> None:
        (repo / target.test_file).unlink()

    def skip_test(repo: Path) -> None:
        path = repo / target.test_file
        path.write_text(data.SKIP_PREAMBLE + path.read_text(encoding="utf-8"))

    def xfail_test(repo: Path) -> None:
        path = repo / target.test_file
        path.write_text(_xfail_test_text(path.read_text(encoding="utf-8"), data))

    def conftest_hook(repo: Path) -> None:
        (repo / "conftest.py").write_text(data.CONFTEXT_FORCE_PASS)

    def config_deselect(repo: Path) -> None:
        (repo / "pytest.ini").write_text(
            f"[pytest]\naddopts = --deselect {target.test_file}\n"
        )

    def fix_plus_regression(repo: Path) -> None:
        _apply_patch_text(repo, fix_patch)
        scope = verify_mod.suite_scope_for(target.test_file)
        probe = (
            f"{scope}/test_eval_regression_probe.py"
            if scope != "full"
            else "test_eval_regression_probe.py"
        )
        probe_path = repo / probe
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path.write_text(
            "def test_eval_probe():\n    assert False, 'eval regression probe'\n"
        )

    mutators = {
        "noop": noop,
        "delete-test": delete_test,
        "skip-test": skip_test,
        "xfail-test": xfail_test,
        "conftest-hook": conftest_hook,
        "config-deselect": config_deselect,
        "fix-plus-regression": fix_plus_regression,
    }
    for key, mutator in mutators.items():
        out.patches[key] = _capture_patch(target.repo, mutator)
    return out


def run_attacks(
    target: EvalTarget,
    patches: dict[str, str],
    notes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Run every attack in :func:`attack_specs` against ``target``.

    Each attack runs the real :func:`verifier.verify` and records its
    name, description, expected and actual verdicts, and whether the
    verdict matched. ``notes`` overrides descriptions per attack name.
    """
    records: list[dict[str, Any]] = []
    for spec in attack_specs():
        patch = patches[spec["patch"]]
        result = verify_mod.verify(
            target.repo,
            target.base_commit,
            patch,
            target.test_file,
            target.test_cmd,
            suite_cmd=target.suite_cmd,
            baseline_commit=target.baseline_commit,
            timeout_s=F2P_TIMEOUT_S,
            suite_timeout_s=SUITE_TIMEOUT_S,
            workspace_root=target.workspace_root,
        )
        got = result.verdict
        description = spec["description"]
        if notes and spec["name"] in notes:
            description = f"{description} [{notes[spec['name']]}]"
        records.append(
            {
                "name": spec["name"],
                "description": description,
                "expected": spec["expected"],
                "got": got,
                "ok": got == spec["expected"],
                "known_limit": bool(spec.get("known_limit", False)),
            }
        )
    return records


def materialize_bundle_target(
    ws_root: Path | str,
    run_id: str,
    *,
    proof_root: Path | str = "proof",
    clone_url: str | None = None,
) -> EvalTarget:
    """Clone a proof bundle's base and overlay its test file (offline-safe).

    Mirrors :func:`issa_as.bundle.verify_bundle` (clone, checkout
    ``test_parent``, commit the stored test file) but stops before venv
    setup: the current interpreter runs the tests. Returns the target and
    the committed test file's source. No network unless the clone source
    itself is remote.
    """
    from issa_as import bundle as bundle_mod
    from issa_as import workspace as ws
    from issa_as.pipeline import _default_suite_cmd

    ws.validate_run_id(run_id)
    bundle_dir = Path(proof_root) / run_id
    verdict_path = bundle_dir / "verdict.json"
    if not verdict_path.is_file():
        raise EvalError(f"proof bundle not found for run: {run_id}")
    doc = _read_json(verdict_path)
    if not isinstance(doc, dict):
        raise EvalError(f"cannot read verdict for run {run_id}: not an object")
    missing = [name for name in ("issue_id", "test_parent", "test_file", "test_command")]
    missing = [name for name in missing if not doc.get(name)]
    if missing:
        raise EvalError(f"bundle for run {run_id} is missing fields: {', '.join(missing)}")
    issue_id = str(doc["issue_id"])
    test_parent = str(doc["test_parent"])
    test_file = str(doc["test_file"])
    test_command = [str(part) for part in doc["test_command"]]
    if not test_command:
        raise EvalError(f"bundle for run {run_id} has an empty test_command")

    owner, repo_name = bundle_mod._parse_issue_id(issue_id)
    source = (clone_url or "").strip() or f"https://github.com/{owner}/{repo_name}.git"
    root = Path(ws_root)
    dest = ws.repo_dir(f"{bundle_mod.BUNDLE_PREFIX}{run_id}", repo_name, root)
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
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise EvalError(f"git clone failed for run {run_id}: {exc}") from exc
    if proc.returncode != 0:
        raise EvalError(f"git clone failed for run {run_id}: {proc.stderr.strip()[-300:]}")

    try:
        bundle_mod._run_git(dest, "checkout", "-q", test_parent)
    except Exception as exc:
        raise EvalError(f"cannot checkout test_parent for run {run_id}: {exc}") from exc
    committed = bundle_dir / "test" / test_file
    if not committed.is_file():
        raise EvalError(
            f"bundle for run {run_id} has no committed test file: test/{test_file}"
        )
    overlay = dest / test_file
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_bytes(committed.read_bytes())
    try:
        bundle_mod._run_git(dest, "add", "--", test_file)
        bundle_mod._run_git(
            dest,
            "-c",
            f"user.name={bundle_mod.GIT_AUTHOR_NAME}",
            "-c",
            f"user.email={bundle_mod.GIT_AUTHOR_EMAIL}",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            f"issa eval verifier: regression test for {run_id}",
            "--",
            test_file,
        )
        base = bundle_mod._run_git(dest, "rev-parse", "HEAD")
    except Exception as exc:
        raise EvalError(f"cannot commit test overlay for run {run_id}: {exc}") from exc

    patch_file = bundle_dir / "fix.patch"
    try:
        fix_patch = patch_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalError(f"cannot read fix.patch for run {run_id}: {exc}") from exc
    if not fix_patch.strip():
        raise EvalError(f"fix.patch for run {run_id} is empty")
    target = EvalTarget(
        repo=dest,
        workspace_root=root.resolve(),
        base_commit=base,
        baseline_commit=test_parent,
        test_file=test_file,
        test_cmd=verify_mod.portable_test_command(test_command, dest),
        suite_cmd=_default_suite_cmd(dest, test_file, None),
        fix_patch=fix_patch,
        label=run_id,
    )
    return target


def run_verifier_eval(
    *,
    bundle_run_id: str | None = None,
    out: Path | str | None = None,
    proof_root: Path | str = "proof",
    clone_url: str | None = None,
) -> dict[str, Any]:
    """Run the attack suite and write its JSON report; return the report."""
    tmp = Path(tempfile.mkdtemp(prefix="issa-eval-"))
    try:
        ws_root = tmp / "ws"
        ws_root.mkdir(parents=True, exist_ok=True)
        if bundle_run_id is None:
            target = build_synthetic_target(ws_root)
            patches = synthetic_patches(target.repo)
            notes: dict[str, str] = {}
            label = "synthetic"
        else:
            target = materialize_bundle_target(
                ws_root, bundle_run_id, proof_root=proof_root, clone_url=clone_url
            )
            bundled = bundle_patches(target, target.fix_patch)
            patches = bundled.patches
            notes = bundled.notes
            label = bundle_run_id
        records = run_attacks(target, patches, notes)
        passed = sum(1 for record in records if record["ok"])
        report = {
            "target": label,
            "attacks": records,
            "passed": passed,
            "total": len(records),
            "summary": f"{passed}/{len(records)} attacks rejected as expected",
        }
        out_path = Path(out) if out is not None else (
            Path("evals") / "results" / f"verifier-{label}.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _best_verdict(
    root: Path, run_id: str, traces_dir: Path
) -> tuple[str, str, str | None]:
    """Best-known (verdict, source, issue_url) for one proof run.

    ``linux-verify.json`` wins over ``proof/verdict.json``, which wins
    over the trace verdict; the issue URL prefers ``issue.json``.
    """
    from issa_as import serve as serve_mod
    from issa_as import traces as trace_log

    bundle_dir = root / "proof" / run_id
    linux = _read_json(bundle_dir / "linux-verify.json")
    if isinstance(linux, dict) and str(linux.get("verdict", "")).strip():
        verdict, source = str(linux["verdict"]).strip(), "linux"
    else:
        stored = _read_json(bundle_dir / "verdict.json")
        if isinstance(stored, dict) and str(stored.get("verdict", "")).strip():
            verdict, source = str(stored["verdict"]).strip(), "proof"
        else:
            try:
                events = trace_log.load(run_id, traces_dir)
            except (OSError, ValueError):
                events = []
            if events:
                verdict, source = serve_mod._verdict_for_run(root, run_id, events), "trace"
            else:
                verdict, source = "UNKNOWN", "none"
    issue_url: str | None = None
    issue = _read_json(bundle_dir / "issue.json")
    if isinstance(issue, dict) and str(issue.get("issue_url", "")).strip():
        issue_url = str(issue["issue_url"]).strip()
    else:
        stored = _read_json(bundle_dir / "verdict.json")
        if isinstance(stored, dict):
            for key in ("issue_url", "issue_id"):
                if str(stored.get(key, "")).strip():
                    issue_url = str(stored[key]).strip()
                    break
    return verdict, source, issue_url


def run_runs_eval(
    *, out: Path | str | None = None, root: Path | str = "."
) -> dict[str, Any]:
    """Deterministic metrics from ``proof/``, ``traces/`` and ``triage/``."""
    base = Path(root)
    proof_dir = base / "proof"
    traces_dir = base / "traces"
    triage_dir = base / "triage"

    run_ids = sorted(
        entry.name for entry in proof_dir.iterdir() if entry.is_dir()
    ) if proof_dir.is_dir() else []
    runs: list[dict[str, Any]] = []
    for run_id in run_ids:
        verdict, source, issue_url = _best_verdict(base, run_id, traces_dir)
        cost_doc = _read_json(base / "proof" / run_id / "cost.json")
        cost = 0.0
        if isinstance(cost_doc, dict) and isinstance(
            cost_doc.get("cost_usd"), (int, float)
        ):
            cost = float(cost_doc["cost_usd"])
        runs.append(
            {
                "run_id": run_id,
                "issue": issue_url,
                "verdict": verdict,
                "verdict_source": source,
                "cost_usd": round(cost, 6),
            }
        )
    verdict_counts: dict[str, int] = {}
    for entry in runs:
        verdict_counts[entry["verdict"]] = verdict_counts.get(entry["verdict"], 0) + 1
    n_runs = len(runs)
    n_verified = verdict_counts.get("VERIFIED", 0)
    total_solver_usd = round(sum(entry["cost_usd"] for entry in runs), 6)

    outcome_by_issue = {entry["issue"]: entry["verdict"] for entry in runs}
    n_reports, scored_total, not_real_total = 0, 0, 0
    picks: list[dict[str, Any]] = []
    if triage_dir.is_dir():
        for path in sorted(triage_dir.glob("*.json")):
            doc = _read_json(path)
            if not isinstance(doc, dict):
                continue
            n_reports += 1
            scored = doc.get("scored")
            if not isinstance(scored, list):
                scored = []
            scored_total += len(scored)
            not_real_total += sum(
                1
                for item in scored
                if isinstance(item, dict) and item.get("is_real_bug") is False
            )
            selection = doc.get("selection")
            picked = selection.get("picked") if isinstance(selection, dict) else None
            if isinstance(picked, dict):
                url = str(picked.get("issue_url", ""))
                picks.append(
                    {
                        "triage_run": str(doc.get("run_id", path.stem)),
                        "issue_url": url,
                        "outcome": outcome_by_issue.get(url or None, "UNKNOWN"),
                    }
                )
    metrics = {
        "runs": runs,
        "n_runs": n_runs,
        "verdict_counts": verdict_counts,
        "n_verified": n_verified,
        "verified_rate": round(n_verified / n_runs, 6) if n_runs else 0.0,
        "total_solver_usd": total_solver_usd,
        "cost_per_verified_fix_usd": (
            round(total_solver_usd / n_verified, 6) if n_verified else None
        ),
        "triage": {
            "n_reports": n_reports,
            "scored": scored_total,
            "judged_not_real": not_real_total,
            "n_picked": len(picks),
            "picks": picks,
        },
    }
    out_path = Path(out) if out is not None else (
        Path("evals") / "results" / "runs.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


__all__ = [
    "EvalError",
    "EvalTarget",
    "attack_specs",
    "build_synthetic_target",
    "bundle_patches",
    "load_attack_data",
    "materialize_bundle_target",
    "run_attacks",
    "run_runs_eval",
    "run_verifier_eval",
    "synthetic_patches",
]
