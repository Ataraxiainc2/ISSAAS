"""Dry-run publishing with SECURITY.md respect (B5, Prompt 18).

During the current testing phase upstream publishing is not automated at
all: :func:`publish_bundle` only writes local dry-run artifacts under
``proof/<run_id>/`` and never touches the network for writes. A read-only
client may be used to *read* ``SECURITY.md`` when no local copy exists.

- Repos whose policy requires private reporting never get a PR, issue or
  comment draft. They get ``proof/<run_id>/disclosure.md`` instead: a
  private report addressed to the policy contact.
- All other repos get ``proof/<run_id>/pr_draft.md`` (at most one draft
  per run, sent only with explicit human approval — which this module
  never performs).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from issa_as.scout import (
    SECURITY_PATHS,
    SecurityPolicy,
    fetch_security_policy,
    parse_security_policy,
)

DEFAULT_PROOF_ROOT = Path("proof")
DEFAULT_WORKSPACES_ROOT = Path("workspaces")

DRAFT_HEADER = (
    "DRAFT — for a human to review and send privately; ISSA AS never sends it."
)

AI_DISCLOSURE_LINE = (
    "AI-assistance disclosure: this report and the attached fix were produced "
    "with AI assistance (ISSA AS, an automated fixing agent); a human must "
    "review and verify everything before sending."
)

DISCLOSURE_STATUS_TEMPLATE = (
    "the issue is already public at {issue_url}; this report adds a fix "
    "verified by Fail-to-Pass"
)


def _read_bundle_json(bundle_dir: Path, name: str) -> dict[str, Any]:
    try:
        doc = json.loads((bundle_dir / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _find_run_repo(run_id: str, workspaces_root: Path | str) -> Path | None:
    """Locate the cloned repo under ``workspaces/<run_id>/`` (read-only)."""
    run_dir = Path(workspaces_root) / run_id
    if not run_dir.is_dir():
        return None
    candidates = sorted(
        p for p in run_dir.iterdir() if p.is_dir() and (p / ".git").is_dir()
    )
    if not candidates:
        # Fall back to any directory: the policy files are what matter.
        others = sorted(p for p in run_dir.iterdir() if p.is_dir())
        return others[0] if others else None
    return candidates[0]


def read_policy_from_dir(repo_dir: Path, repo_full: str = "") -> tuple[SecurityPolicy, str]:
    """Read the first found SECURITY.md under a local directory.

    Returns ``(policy, raw_text)``; a missing policy yields a non-private
    policy and empty text. Never touches the network.
    """
    for path in SECURITY_PATHS:
        candidate = repo_dir / path
        try:
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8", errors="replace")
                policy = parse_security_policy(text, repo_full)
                policy.source = path
                return policy, text
        except OSError:
            continue
    return SecurityPolicy(), ""


def fetch_policy_via_client(
    client: Any, repo_full: str, source: str = ""
) -> tuple[SecurityPolicy, str]:
    """Read the policy with a single GitHub contents request.

    When the bundle remembers which path held the policy (``source``), only
    that path is requested; otherwise only root ``SECURITY.md`` is read.
    Any transport error yields a non-private policy (never a crash).
    """
    paths = [source] if source else ["SECURITY.md"]
    for path in paths:
        try:
            text = client.get_text(repo_full, path)
        except Exception:  # noqa: BLE001 — offline-friendly: no policy then
            return SecurityPolicy(), ""
        if text:
            policy = parse_security_policy(text, repo_full)
            policy.source = path
            return policy, text
    return SecurityPolicy(), ""


def build_disclosure(
    *,
    title: str = "",
    repo: str = "",
    issue_url: str = "",
    contact: str = "",
    policy_source: str = "",
    issue_body: str = "",
    affected_versions: str = "",
    test_file: str = "",
    test_command: list[str] | None = None,
    patch_summary: str = "",
    patch_text: str = "",
    model: str = "",
) -> str:
    """Render the private disclosure draft (deterministic, plain text).

    Covers the fields a SECURITY.md policy typically asks for (title,
    impacted project, description, affected versions, reproduction steps,
    impact, suggested fix) plus the disclosure status and the AI-assistance
    disclosure line. Reproduction steps point at the regression test and
    the suggested fix is ``fix.patch``.
    """
    to = contact or "(no contact found in SECURITY.md — see the policy)"
    versions = affected_versions or "Unknown (not recorded in the bundle)"
    command = " ".join(test_command or []) or "(not recorded)"
    lines = [
        "# Private vulnerability disclosure — DRAFT",
        "",
        DRAFT_HEADER,
        "",
        f"To: {to}",
    ]
    if policy_source:
        lines.append(f"Policy: {policy_source}")
    lines += [
        "",
        f"Title: {title or '(untitled)'}",
        "",
        f"Impacted project: {repo or '(unknown)'}",
        "",
        "Description:",
        (issue_body.strip() or "(no description recorded)")[:2000],
        "",
        f"Affected versions: {versions}",
        "",
        "Reproduction steps (regression test):",
        f"- Test: {test_file or '(not recorded)'}",
        f"- Command: {command}",
        "- The test fails on the base commit and passes with the fix (Fail-to-Pass).",
        "",
        "Impact:",
        "See the description above; the fix below removes the vulnerable behaviour",
        "without changing any public API.",
        "",
        "Suggested fix (fix.patch):",
        (patch_summary.strip() or "(no summary recorded)"),
        "",
        "```diff",
        (patch_text.strip() or "(patch not recorded)")[:20000],
        "```",
        "",
        "Disclosure status: "
        + DISCLOSURE_STATUS_TEMPLATE.format(issue_url=issue_url or "(unknown)"),
        "",
        AI_DISCLOSURE_LINE,
    ]
    if model:
        lines.append(f"Solver model: {model}")
    return "\n".join(lines).rstrip() + "\n"


def build_pr_draft(
    *,
    title: str = "",
    repo: str = "",
    issue_url: str = "",
    patch_summary: str = "",
    test_file: str = "",
    model: str = "",
) -> str:
    """Render the single public-PR dry-run draft (never sent by this module)."""
    return "\n".join(
        [
            "# Pull request draft — DRY-RUN",
            "",
            "DRAFT — dry-run only; requires explicit human approval before "
            "sending; ISSA AS never sends it.",
            "",
            f"Repo: {repo or '(unknown)'}",
            f"Fixes: {issue_url or '(unknown)'}",
            f"Title: {title or '(untitled)'}",
            "",
            "Summary:",
            (patch_summary.strip() or "(no summary recorded)"),
            "",
            f"Regression test: {test_file or '(not recorded)'}",
            "",
            AI_DISCLOSURE_LINE,
        ]
        + ([f"Solver model: {model}"] if model else [])
    ).rstrip() + "\n"


def _resolve_bundle(bundle: str | Path) -> Path:
    path = Path(bundle) if str(bundle) else DEFAULT_PROOF_ROOT
    if not path.is_dir():
        raise FileNotFoundError(f"proof bundle not found: {path}")
    return path


def publish_bundle(
    bundle: str | Path,
    *,
    approval_id: str = "",
    client: Any | None = None,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    policy_text: str | None = None,
    policy_source: str = "",
) -> dict[str, Any]:
    """Dry-run publish of one proof bundle (local artifacts only).

    For ``private_reporting`` repos this never writes a PR/issue/comment
    draft — it writes ``disclosure.md`` instead. For other repos it writes
    ``pr_draft.md`` (at most one draft per run). Returns a dict with
    ``run_id``, ``private_reporting``, ``written`` and ``refused_public``.
    """
    bundle_dir = _resolve_bundle(bundle)
    run_id = bundle_dir.name
    verdict = _read_bundle_json(bundle_dir, "verdict.json")
    issue = _read_bundle_json(bundle_dir, "issue.json")
    try:
        patch_text = (bundle_dir / "fix.patch").read_text(encoding="utf-8")
    except OSError:
        patch_text = ""

    repo_full = str(verdict.get("issue_id") or "").split("#")[0] or str(
        issue.get("repo_full") or ""
    )
    issue_url = str(verdict.get("issue_url") or issue.get("issue_url") or "")
    title = str(issue.get("title") or verdict.get("patch_summary") or "")

    policy = SecurityPolicy()
    if policy_text is not None:
        policy = parse_security_policy(policy_text, repo_full)
        policy.source = policy_source or "provided"
    else:
        workspace_repo = _find_run_repo(run_id, workspaces_root)
        if workspace_repo is not None:
            policy, _ = read_policy_from_dir(workspace_repo, repo_full)
        if not policy.source and client is not None and repo_full and "/" in repo_full:
            policy, _ = fetch_policy_via_client(client, repo_full, policy_source)

    if policy.private_reporting:
        text = build_disclosure(
            title=title,
            repo=repo_full,
            issue_url=issue_url,
            contact=policy.contact,
            policy_source=policy.source,
            issue_body=str(issue.get("body") or ""),
            test_file=str(verdict.get("test_file") or ""),
            test_command=[str(p) for p in (verdict.get("test_command") or [])],
            patch_summary=str(verdict.get("patch_summary") or ""),
            patch_text=patch_text,
            model=str(verdict.get("model") or ""),
        )
        out = bundle_dir / "disclosure.md"
        out.write_text(text, encoding="utf-8")
        return {
            "run_id": run_id,
            "private_reporting": True,
            "written": str(out),
            "refused_public": True,
            "approval_id": approval_id,
        }

    text = build_pr_draft(
        title=title,
        repo=repo_full,
        issue_url=issue_url,
        patch_summary=str(verdict.get("patch_summary") or ""),
        test_file=str(verdict.get("test_file") or ""),
        model=str(verdict.get("model") or ""),
    )
    out = bundle_dir / "pr_draft.md"
    out.write_text(text, encoding="utf-8")
    return {
        "run_id": run_id,
        "private_reporting": False,
        "written": str(out),
        "refused_public": False,
        "approval_id": approval_id,
    }


def build_disclosure_for_run(
    run_id: str,
    *,
    proof_root: Path | str = DEFAULT_PROOF_ROOT,
    workspaces_root: Path | str = DEFAULT_WORKSPACES_ROOT,
    client: Any | None = None,
) -> Path:
    """Build ``disclosure.md`` for an existing run (``issa disclosure``).

    Reads ``SECURITY.md`` from the run's workspace repo when present;
    otherwise performs one GitHub contents request via ``client`` (a live
    :class:`GitHubClient` when ``client`` is None and the repo is known).
    """
    bundle_dir = Path(proof_root) / run_id
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"proof bundle not found for run: {run_id}")
    verdict = _read_bundle_json(bundle_dir, "verdict.json")
    issue = _read_bundle_json(bundle_dir, "issue.json")
    try:
        patch_text = (bundle_dir / "fix.patch").read_text(encoding="utf-8")
    except OSError:
        patch_text = ""
    repo_full = str(verdict.get("issue_id") or "").split("#")[0] or str(
        issue.get("repo_full") or ""
    )
    issue_url = str(verdict.get("issue_url") or issue.get("issue_url") or "")
    title = str(issue.get("title") or verdict.get("patch_summary") or "")

    policy = SecurityPolicy()
    workspace_repo = _find_run_repo(run_id, workspaces_root)
    if workspace_repo is not None:
        policy, _ = read_policy_from_dir(workspace_repo, repo_full)
    live_client = client
    if not policy.source and live_client is None and repo_full and "/" in repo_full:
        from issa_as.scout import GitHubClient

        live_client = GitHubClient()
    if not policy.source and live_client is not None and repo_full and "/" in repo_full:
        policy, _ = fetch_policy_via_client(live_client, repo_full)

    # ``issa disclosure`` explicitly asks for a private draft, so it is
    # written even when the policy does not demand privacy (noted below).
    contact = policy.contact
    extra_note = ""
    if not policy.private_reporting:
        extra_note = (
            "\nNote: no private-reporting SECURITY.md was found for this repo; "
            "this draft was still built because `issa disclosure` was requested."
        )
    text = build_disclosure(
        title=title,
        repo=repo_full,
        issue_url=issue_url,
        contact=contact,
        policy_source=policy.source,
        issue_body=str(issue.get("body") or ""),
        test_file=str(verdict.get("test_file") or ""),
        test_command=[str(p) for p in (verdict.get("test_command") or [])],
        patch_summary=str(verdict.get("patch_summary") or ""),
        patch_text=patch_text,
        model=str(verdict.get("model") or ""),
    )
    out = bundle_dir / "disclosure.md"
    out.write_text((text.rstrip() + "\n" + extra_note).rstrip() + "\n", encoding="utf-8")
    return out


__all__ = [
    "AI_DISCLOSURE_LINE",
    "DISCLOSURE_STATUS_TEMPLATE",
    "DRAFT_HEADER",
    "build_disclosure",
    "build_disclosure_for_run",
    "build_pr_draft",
    "fetch_policy_via_client",
    "publish_bundle",
    "read_policy_from_dir",
]


def fetch_security_policy_for_repo(
    repo_full: str, client: Any
) -> SecurityPolicy:
    """Fetch and parse the policy for one repo (cached, rate-limited seam)."""
    return fetch_security_policy(client, repo_full)
