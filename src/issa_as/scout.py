"""Security-first GitHub issue discovery (B5, B7, B8).

Read-only use of the GitHub REST/search API (``GITHUB_TOKEN`` optional,
higher rate limit when set). Results are cached under ``.cache/scout/``.

Pipeline: query open issues for vulnerability classes first (path
traversal / zip slip, command or SQL injection, XSS, open redirect, XXE,
ReDoS, unsafe deserialization / pickle / yaml.load, SSRF, auth bypass,
secret leakage), then ordinary ``bug``-labelled issues to fill the pool.
Every query carries the requested ``language:`` qualifiers so
``--languages`` restricts the GitHub search itself. Each candidate is
tagged with ``security_class`` (or ``null``), passes the reputation
filter, the bot/alert and feature-request filters, the maintainer-respect
filter, and ecosystem detection (``python`` / ``node`` / ``none``). The
final list is sorted by score, security first only inside a score band.
The fixed queries above run with no model calls. With ``guided=True``,
one model call (same provider path as triage, logged with tokens and
cost) first proposes keyword searches from past ``proof/*/`` outcomes;
the model proposes keywords only and code builds and runs every GitHub
query, so the model can never target a repo, user or org. The GitHub
token (when set) is used only by the deterministic client and is never
passed to any model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from issa_as import providers as providers_mod
from issa_as import ratelimit as limits
from issa_as import signals as signal_mod
from issa_as import traces as trace_log
from issa_as.budget import SPARK_CONTRIBUTOR_MODEL

DEFAULT_CACHE_DIR = Path(".cache/scout")
GITHUB_API = "https://api.github.com"

STAR_FLOOR = 50
PUSH_WINDOW_DAYS = 180
MAX_SIZE_KB = 50 * 1024

# Vulnerability class -> search keywords (matched against issue text).
SECURITY_KEYWORDS: dict[str, list[str]] = {
    "path-traversal": ["path traversal", "zip slip", "directory traversal", "../"],
    "command-injection": ["command injection", "os.system", "shell=True", "subprocess"],
    "sql-injection": ["sql injection", "sql注入", "sqli"],
    "xss": ["xss", "cross-site scripting", "cross site scripting"],
    "open-redirect": ["open redirect", "unvalidated redirect"],
    "xxe": ["xxe", "xml external entity", "billion laughs"],
    "redos": ["redos", "regular expression denial of service", "catastrophic backtracking"],
    "unsafe-deserialization": ["deserialization", "pickle.loads", "yaml.load", "unsafe load"],
    "ssrf": ["ssrf", "server-side request forgery"],
    "auth-bypass": ["auth bypass", "authentication bypass", "broken access control"],
    "secret-leakage": ["secret leakage", "leaked secret", "exposed api key", "hardcoded secret"],
}

SECURITY_SEARCH_QUERIES: list[tuple[str, str]] = [
    (cls, f"type:issue state:open {' '.join(words[:2])}")
    for cls, words in SECURITY_KEYWORDS.items()
]

BUG_FILL_QUERY = "type:issue state:open label:bug"

# GitHub `language:` qualifiers per requested ecosystem. `node` covers both
# JavaScript and TypeScript repos, hence two qualifiers (one query each).
LANGUAGE_QUALIFIERS: dict[str, list[str]] = {
    "python": ["language:python"],
    "node": ["language:javascript", "language:typescript"],
    "javascript": ["language:javascript"],
    "typescript": ["language:typescript"],
}

# Score band for the final sort: candidates whose scores round to the same
# 2 decimals tie, and security breaks the tie inside the band.
SCORE_BAND_DIGITS = 2


def sort_key(candidate: Candidate) -> tuple[float, bool, float]:
    """Final ordering: score band first, security only breaks ties in-band."""
    return (
        -round(candidate.score, SCORE_BAND_DIGITS),
        candidate.security_class is None,
        -candidate.score,
    )

BOT_LOGINS = {"dependabot", "dependabot-bot", "mend", "renovate", "renovate-bot", "snyk-bot"}
BOT_TITLE_RES = [
    re.compile(r"\b\d+\s+vulnerabilit", re.IGNORECASE),
    re.compile(r"^bump\s+\S+\s+from\s+", re.IGNORECASE),
    re.compile(r"^\[security\]", re.IGNORECASE),
    re.compile(r"\[vuln-sentinel\]", re.IGNORECASE),
    re.compile(r"\b\d+\s+known\s+vulnerabilit", re.IGNORECASE),
    re.compile(r"\bNEMO-\d+\b", re.IGNORECASE),
    re.compile(r"\[SQ:[^\]]*\]", re.IGNORECASE),
]

FEATURE_TITLE_RES = [
    re.compile(r"^\[feature\]", re.IGNORECASE),
    re.compile(r"^feat(\(|:)", re.IGNORECASE),
    re.compile(r"^document\b", re.IGNORECASE),
    re.compile(r"^add\b.*\bsupport\b", re.IGNORECASE),
]
FEATURE_LABELS = {"enhancement", "feature", "documentation"}

AI_REFUSAL_RES = [
    re.compile(r"no\s+ai\b.{0,60}(contribution|generated|pull request)", re.IGNORECASE),
    re.compile(
        r"ai[- ]generated\s+(code|contributions|pull requests).{0,60}not\s+accept",
        re.IGNORECASE,
    ),
    re.compile(r"do\s+not\s+submit\s+ai[- ]generated", re.IGNORECASE),
    re.compile(r"ban.{0,20}ai[- ]generated", re.IGNORECASE),
    re.compile(r"will\s+not\s+accept\s+ai\b", re.IGNORECASE),
]


# Max chars of the issue body kept for the model stages (triage/critic).
BODY_EXCERPT_CHARS = 1000

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def body_excerpt(body: str | None, limit: int = BODY_EXCERPT_CHARS) -> str:
    """Sanitized issue-body excerpt for model prompts.

    Strips HTML comments (hidden instructions/metadata) and collapses all
    whitespace runs to single spaces, then truncates to ``limit`` chars.
    The excerpt is untrusted issue text, never instructions.
    """
    if not body:
        return ""
    scrubbed = _HTML_COMMENT_RE.sub(" ", body)
    collapsed = " ".join(scrubbed.split())
    return collapsed[:limit]


class SecurityPolicy(BaseModel):
    """Deterministic parse of a repo's SECURITY.md (B5, Prompt 18).

    ``private_reporting`` is True when the policy text asks reporters not
    to use public issues/PRs, or to report privately, by email, or through
    GitHub security advisories. ``contact`` is the first security-style
    email found, else the repo's ``/security/advisories/new`` URL when
    advisories are named, else "". ``source`` is the policy path that was
    found ("" when no policy exists).
    """

    private_reporting: bool = False
    contact: str = ""
    source: str = ""


# Candidate locations for the security policy, first found wins.
SECURITY_PATHS: tuple[str, ...] = (
    "SECURITY.md",
    ".github/SECURITY.md",
    "docs/SECURITY.md",
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_SECURITY_EMAIL_RE = re.compile(r"[\w.+-]*secur[\w.+-]*@[\w.-]+\.[A-Za-z]{2,}")

# The policy asks reporters to stay off public channels.
# (``[\s\S]`` gaps span words and line breaks.)
_PUBLIC_REFUSAL_RES = [
    re.compile(r"must\s+not\s+be[\s\S]{0,60}public", re.IGNORECASE),
    re.compile(r"do\s+not[\s\S]{0,60}public\s+(channel|issue|tracker)", re.IGNORECASE),
    re.compile(r"(never|don'?t)[\s\S]{0,60}public", re.IGNORECASE),
    re.compile(r"not[\s\S]{0,60}through\s+public\s+channels?", re.IGNORECASE),
    re.compile(r"not[\s\S]{0,60}(report|file|submit|open|create)[\w\s]{0,40}public", re.IGNORECASE),
    re.compile(
        r"do\s+not\s+(report|file|submit|open|create)\b[\s\S]{0,80}github\s+issues?",
        re.IGNORECASE,
    ),
    re.compile(r"please\s+do\s+not\s+(report|file|submit|open|create)\b", re.IGNORECASE),
    re.compile(r"do\s+not\s+open\s+(a\s+)?(public\s+)?issues?", re.IGNORECASE),
    re.compile(r"no\s+public\s+(issue|disclosure)", re.IGNORECASE),
]

# The policy directs reporters to a private channel.
_PRIVATE_CHANNEL_RES = [
    re.compile(r"report\s+privately", re.IGNORECASE),
    re.compile(r"privately\s+(report|disclose|to)", re.IGNORECASE),
    re.compile(r"\bprivate\s+vulnerability\s+reporting\b", re.IGNORECASE),
    re.compile(r"\bconfidential", re.IGNORECASE),
    re.compile(r"\bsecurity\s+advisor", re.IGNORECASE),
    re.compile(r"\bsecurit\w*\s+advisories\b", re.IGNORECASE),
    re.compile(r"report\s+a\s+vulnerability", re.IGNORECASE),
    re.compile(r"\be-?mail\b[\s\S]{0,60}(secur|vulnerab|report)", re.IGNORECASE),
    re.compile(r"(secur|vulnerab|report)[\s\S]{0,60}\be-?mail\b", re.IGNORECASE),
    re.compile(r"mail\s+to\s+\S+@", re.IGNORECASE),
]


def parse_security_policy(text: str | None, repo: str = "") -> SecurityPolicy:
    """Parse SECURITY.md text into a :class:`SecurityPolicy` (deterministic).

    No-policy (None/empty/whitespace) yields a non-private policy with an
    empty contact and source. A permissive policy that invites public
    GitHub issues without any private channel stays non-private.
    """
    if not text or not text.strip():
        return SecurityPolicy()
    body = text.strip()
    private = any(rx.search(body) for rx in _PUBLIC_REFUSAL_RES) or any(
        rx.search(body) for rx in _PRIVATE_CHANNEL_RES
    )
    contact = ""
    security_emails = _SECURITY_EMAIL_RE.findall(body)
    if security_emails:
        contact = security_emails[0]
    else:
        emails = _EMAIL_RE.findall(body)
        if emails:
            contact = emails[0]
    if not contact and re.search(r"advisor", body, re.IGNORECASE) and repo and "/" in repo:
        contact = f"https://github.com/{repo}/security/advisories/new"
    return SecurityPolicy(
        private_reporting=bool(private),
        contact=contact,
        source="",
    )


def fetch_security_policy(client: ScoutClient, full_name: str) -> SecurityPolicy:
    """Fetch the first found SECURITY.md path and parse it.

    Uses the same cached, rate-limited ``get_text`` seam as the
    README/CONTRIBUTING fetches, so budget and caching behaviour match.
    Returns a non-private policy when no file exists.
    """
    for path in SECURITY_PATHS:
        text = client.get_text(full_name, path)
        if text:
            policy = parse_security_policy(text, full_name)
            policy.source = path
            return policy
    return SecurityPolicy()


class Candidate(BaseModel):
    """One scouted issue that survived every filter."""

    issue_url: str = ""
    repo: str = ""
    number: int = 0
    title: str = ""
    labels: list[str] = Field(default_factory=list)
    security_class: str | None = None
    # Search-query class that surfaced this issue. Set whenever the issue
    # was found via a security query but its text carries no security
    # keywords (honest ``security_class`` stays None then). Absent in old
    # reports, which still load (default None).
    matched_query_class: str | None = None
    # Full GitHub query string that found this issue (guided or fixed).
    # Absent in old reports, which still load (default None).
    matched_query: str | None = None
    runner: str = "none"
    stars: int = 0
    has_snippet: bool = False
    signals: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0
    # Sanitized issue-body excerpt (see ``body_excerpt``): untrusted text
    # for the triage/critic prompts, never instructions.
    body_excerpt: str = ""
    # Parsed SECURITY.md policy (B5/Prompt 18). The issue is still fixed
    # locally when it is already public; only the publish step changes.
    security_policy: SecurityPolicy = Field(default_factory=SecurityPolicy)


class ScoutClient(Protocol):
    """Minimal seam over GitHub; fakes implement this in offline tests."""

    def search_issues(self, query: str) -> list[dict[str, Any]]: ...
    def get_repo(self, full_name: str) -> dict[str, Any]: ...
    def get_text(self, full_name: str, path: str) -> str | None: ...
    def list_dir(self, full_name: str, path: str) -> list[str]: ...
    def linked_open_pr(self, full_name: str, number: int) -> bool: ...


def classify_security(title: str, body: str | None) -> str | None:
    """Return the first vulnerability class whose keywords match, else None."""
    text = f"{title or ''}\n{body or ''}".lower()
    for cls, words in SECURITY_KEYWORDS.items():
        if any(word.lower() in text for word in words):
            return cls
    return None


def is_bot_alert(issue: Mapping[str, Any]) -> bool:
    """True for Dependabot/Mend/Renovate-style dependency-alert issues."""
    login = str(((issue.get("user") or {}).get("login") or "")).lower()
    if login in BOT_LOGINS:
        return True
    title = str(issue.get("title") or "")
    return any(rx.search(title) for rx in BOT_TITLE_RES)


def is_feature_or_docs(title: str, labels: list[str], body: str | None) -> bool:
    """True for feature requests / docs tasks without evidence of a real bug.

    Matches titles like ``[Feature] …``, ``feat: …``, ``Document …`` or
    ``Add … support``, or the ``enhancement``/``feature``/``documentation``
    labels — unless the body carries a stack trace or reproduction steps,
    which mark a real, actionable bug report.
    """
    wanted = {str(name).lower() for name in labels}
    if not any(rx.search(title or "") for rx in FEATURE_TITLE_RES) and not (
        wanted & FEATURE_LABELS
    ):
        return False
    return not (
        signal_mod.has_stack_trace(body) > 0.0 or signal_mod.has_repro_steps(body) > 0.0
    )


def language_qualifiers(languages: tuple[str, ...]) -> list[str]:
    """GitHub `language:` qualifiers for the requested ecosystems.

    Unknown ecosystem names fall back to ``language:<name>``. An empty
    input means "no restriction" (bare queries).
    """
    qualifiers: list[str] = []
    for lang in languages:
        key = lang.strip().lower()
        if not key:
            continue
        for qualifier in LANGUAGE_QUALIFIERS.get(key, [f"language:{key}"]):
            if qualifier not in qualifiers:
                qualifiers.append(qualifier)
    return qualifiers


def check_repo(repo: Mapping[str, Any], now: datetime | None = None) -> str | None:
    """Reputation filter. Returns None when the repo passes, else a reason."""
    if repo.get("archived"):
        return "archived"
    if repo.get("fork"):
        return "fork"
    stars = repo.get("stargazers_count", 0) or 0
    if stars < STAR_FLOOR:
        return f"stars {stars} < {STAR_FLOOR}"
    size = repo.get("size", 0) or 0
    if size > MAX_SIZE_KB:
        return f"size {size}KB > {MAX_SIZE_KB}KB"
    pushed = repo.get("pushed_at")
    if pushed:
        try:
            pushed_dt = datetime.fromisoformat(str(pushed).replace("Z", "+00:00"))
        except ValueError:
            return "unparseable pushed_at"
        if pushed_dt.tzinfo is None:
            pushed_dt = pushed_dt.replace(tzinfo=timezone.utc)
        ref = now or datetime.now(timezone.utc)
        age_days = (ref - pushed_dt).total_seconds() / 86400.0
        if age_days > PUSH_WINDOW_DAYS:
            return f"stale (pushed {age_days:.0f}d ago)"
    return None


def refuses_ai(*texts: str | None) -> bool:
    """True when README/CONTRIBUTING text refuses AI-generated contributions."""
    for text in texts:
        if text and any(rx.search(text) for rx in AI_REFUSAL_RES):
            return True
    return False


def detect_runner(
    package_json: str | None = None,
    *,
    has_python_manifest: bool = False,
    has_tests_dir: bool = False,
) -> str:
    """Detect the test ecosystem: ``python``, ``node``, or ``none``.

    Python needs a manifest (pyproject.toml / setup.py / setup.cfg) *plus* a
    tests directory; Node needs a package.json with a test script. Anything
    else is honestly reported as ``none`` and never attempted.
    """
    if package_json:
        try:
            scripts = json.loads(package_json).get("scripts", {})
        except (ValueError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict) and scripts.get("test"):
            return "node"
    if has_python_manifest and has_tests_dir:
        return "python"
    return "none"


def _labels(issue: Mapping[str, Any]) -> list[str]:
    labels = issue.get("labels") or []
    names = []
    for label in labels:
        names.append(label.get("name") if isinstance(label, dict) else str(label))
    return names


def _repo_full_name(issue: Mapping[str, Any]) -> str:
    if issue.get("repository_full_name"):
        return str(issue["repository_full_name"])
    repo_url = str(issue.get("repository_url") or "")
    marker = "repos/"
    if marker in repo_url:
        return repo_url.split(marker, 1)[1]
    return ""


def evaluate_issue(
    client: ScoutClient,
    issue: Mapping[str, Any],
    query_class: str | None,
    languages: tuple[str, ...] = ("python", "node"),
    matched_query: str | None = None,
) -> Candidate | None:
    """Apply every filter to one raw issue; return a Candidate or None."""
    if "pull_request" in issue:
        return None
    full_name = _repo_full_name(issue)
    if not full_name or "/" not in full_name:
        return None
    if is_bot_alert(issue):
        return None
    raw_body = issue.get("body")
    if is_feature_or_docs(
        str(issue.get("title") or ""),
        _labels(issue),
        raw_body if isinstance(raw_body, str) else None,
    ):
        return None
    try:
        repo = client.get_repo(full_name)
    except (httpx.HTTPError, OSError, ValueError):
        return None
    if check_repo(repo) is not None:
        return None
    try:
        readme = client.get_text(full_name, "README.md")
        contributing = client.get_text(full_name, "CONTRIBUTING.md")
        github_contributing = client.get_text(full_name, ".github/CONTRIBUTING.md")
    except (httpx.HTTPError, OSError, ValueError):
        return None
    if refuses_ai(readme, contributing, github_contributing):
        return None
    try:
        security_policy = fetch_security_policy(client, full_name)
    except (httpx.HTTPError, OSError, ValueError):
        return None
    except limits.RateLimited:
        raise
    number = int(issue.get("number") or 0)
    try:
        if number and client.linked_open_pr(full_name, number):
            return None
    except (httpx.HTTPError, OSError, ValueError):
        return None

    package_json = client.get_text(full_name, "package.json")
    has_manifest = any(
        client.get_text(full_name, path) is not None
        for path in ("pyproject.toml", "setup.py", "setup.cfg")
    )
    has_tests = bool(client.list_dir(full_name, "tests") or client.list_dir(full_name, "test"))
    runner = detect_runner(package_json, has_python_manifest=has_manifest, has_tests_dir=has_tests)
    if runner != "none" and runner not in languages:
        return None
    has_ci = bool(client.list_dir(full_name, ".github/workflows"))

    title = str(issue.get("title") or "")
    body = issue.get("body")
    security_class = classify_security(title, body if isinstance(body, str) else None)
    # Honest class: a query match without keywords in the text (e.g. a
    # plain crash found via a security query) is not a security bug, so
    # security_class stays None and the query class is recorded separately.
    matched_query_class = query_class if security_class is None else None
    enriched = dict(issue)
    enriched["security_class"] = security_class
    scored = signal_mod.compute_signals(enriched, repo, runner=runner, has_ci=has_ci)
    return Candidate(
        issue_url=str(issue.get("html_url") or ""),
        repo=full_name,
        number=number,
        title=title,
        labels=_labels(issue),
        security_class=security_class,
        matched_query_class=matched_query_class,
        matched_query=matched_query,
        runner=runner,
        stars=int(repo.get("stargazers_count") or 0),
        has_snippet=signal_mod.has_code_snippet(body if isinstance(body, str) else None) > 0,
        signals=scored.values,
        score=scored.total,
        body_excerpt=body_excerpt(body if isinstance(body, str) else None),
        security_policy=security_policy,
    )


# Guided scout: the agent chooses where to look, code stays in control.
#
# One model call proposes up to MAX_GUIDED_QUERIES keyword searches based
# on past outcomes from proof/*/. The model proposes keywords only; every
# proposal is validated deterministically (plain words, no GitHub
# qualifiers, so the model cannot target a repo, user or org) and code
# builds the ``type:issue state:open <keywords> <language qualifier>``
# query and runs it through the existing cached, rate-limited client.
# Guided queries run first; the fixed queries still run after them as a
# fill, within the same --limit and the same rate-limit early stop.
# Rejected proposals are recorded with the reason.

MAX_GUIDED_QUERIES = 5
MAX_KEYWORDS_CHARS = 60
GUIDED_KEYWORDS_RE = re.compile(r"[A-Za-z0-9 \-_.\"]+")
GUIDED_MAX_TOKENS = 2048
HISTORY_LIMIT = 20
DEFAULT_PROOF_ROOT = Path("proof")

GUIDED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["queries", "rationale"],
    "properties": {
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["keywords", "class", "why"],
                "properties": {
                    "keywords": {"type": "string"},
                    "class": {"type": ["string", "null"]},
                    "why": {"type": "string"},
                },
            },
        },
        "rationale": {"type": "string"},
    },
}


def validate_guided_keywords(keywords: Any) -> str | None:
    """Deterministic gate for one proposed keyword string.

    Returns None when the proposal may become a query, else the rejection
    reason. Keywords are 1-60 chars of letters, digits, spaces, ``-``,
    ``_``, ``.`` and double quotes only; anything containing ``:`` is
    rejected so the model cannot smuggle in GitHub qualifiers
    (``repo:``, ``user:``, ``org:``, ``language:``, ...).
    """
    if not isinstance(keywords, str) or not keywords.strip():
        return "empty keywords"
    text = keywords.strip()
    if ":" in text:
        return "must not contain GitHub qualifiers (`:`)"
    if len(text) > MAX_KEYWORDS_CHARS:
        return f"too long ({len(text)} chars, max {MAX_KEYWORDS_CHARS})"
    if GUIDED_KEYWORDS_RE.fullmatch(text) is None:
        return "bad characters (letters, digits, spaces, -, _, . and double quotes only)"
    return None


def read_proof_history(
    proof_root: Path | str = DEFAULT_PROOF_ROOT,
    limit: int = HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    """Past outcomes for the guided prompt: title, class, verdict, cost.

    Reads up to ``limit`` bundles from ``proof/*/`` (sorted order): the
    title/body from ``issue.json`` (security class re-derived
    deterministically) and the verdict/cost from ``verdict.json``
    (``cost.json`` as fallback). Missing or corrupt bundles are skipped.
    """
    root = Path(proof_root)
    if not root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if len(entries) >= limit:
            break
        try:
            issue = json.loads((child / "issue.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(issue, dict):
            continue
        verdict: dict[str, Any] = {}
        try:
            loaded = json.loads((child / "verdict.json").read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                verdict = loaded
        except (OSError, ValueError):
            verdict = {}
        cost = verdict.get("cost_usd")
        if cost is None:
            try:
                cost_doc = json.loads((child / "cost.json").read_text(encoding="utf-8"))
                if isinstance(cost_doc, dict):
                    cost = cost_doc.get("cost_usd")
            except (OSError, ValueError):
                cost = None
        title = str(issue.get("title", "") or "")
        body = issue.get("body")
        entries.append(
            {
                "title": title,
                "security_class": classify_security(
                    title, body if isinstance(body, str) else None
                ),
                "verdict": str(verdict.get("verdict", "") or ""),
                "cost_usd": cost,
            }
        )
    return entries


def build_guided_prompt(history: list[Mapping[str, Any]]) -> str:
    """Prompt asking the model where the scout should look next."""
    lines = [
        "You choose where a GitHub issue scout should look next.",
        "The scout fixes security bugs it can reproduce; it has already "
        "shown it can solve the past outcomes below. Propose keyword "
        "searches likely to find similar solvable issues.",
        "Allowed security classes: " + ", ".join(sorted(SECURITY_KEYWORDS)),
        "Reply with JSON only, matching this schema:",
        json.dumps(GUIDED_SCHEMA, separators=(",", ":")),
        "Rules: at most 5 queries; keywords is 1-60 chars of plain words "
        "(letters, digits, spaces, -, _, . and double quotes) — never a "
        "repo, user, org or other GitHub qualifier (no `:`); class is one "
        "of the allowed security classes or null; why is one short sentence.",
    ]
    if history:
        lines.append("Past outcomes (title | security class | verdict | cost USD):")
        for entry in history:
            lines.append(
                f"- {entry.get('title', '')} | {entry.get('security_class')} | "
                f"{entry.get('verdict', '')} | {entry.get('cost_usd')}"
            )
    else:
        lines.append("No past outcomes yet: propose general high-value searches.")
    return "\n".join(lines)


def parse_guided_response(text: str) -> tuple[list[dict[str, Any]], str]:
    """Parse the guided answer into (raw proposals, rationale)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        parts = stripped.splitlines()[1:]
        if parts and parts[-1].strip().startswith("```"):
            parts = parts[:-1]
        stripped = "\n".join(parts)
    try:
        data = json.loads(stripped)
    except ValueError as exc:
        raise ValueError(f"guided answer is not JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise ValueError("guided answer must be an object with a 'queries' array")
    proposals: list[dict[str, Any]] = []
    for item in data["queries"]:
        if not isinstance(item, dict):
            raise ValueError("guided query must be an object")
        proposals.append(
            {
                "keywords": item.get("keywords", ""),
                "class": item.get("class"),
                "why": str(item.get("why", "") or ""),
            }
        )
    return proposals, str(data.get("rationale", "") or "")


def propose_guided_queries(
    provider: Any | None,
    history: list[Mapping[str, Any]],
    *,
    run_id: str | None = None,
    traces_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, Any]:
    """One model call proposing scout queries.

    Returns ``(accepted, rejected, rationale, completion)``. ``provider``
    is the triage-route provider (a fake in offline tests); None uses Muse
    Spark Open on Baseten with fallback to
    ``muse-spark-1.3-contributor``, logged with tokens and cost like every
    call. Validation is deterministic: at most MAX_GUIDED_QUERIES
    proposals are kept, the rest are rejected with reasons. Transport
    errors and unparsable answers propagate so the caller can fall back to
    the fixed queries.
    """
    prompt = build_guided_prompt(history)
    if provider is None:
        completion = providers_mod.complete_with_fallback(
            providers_mod.triage_provider(),
            providers_mod.meta_provider(model=SPARK_CONTRIBUTOR_MODEL),
            stage="scout",
            prompt=prompt,
            effort="low",
            run_id=run_id,
            traces_dir=traces_dir,
            max_tokens=GUIDED_MAX_TOKENS,
        )
    else:
        completion = provider.complete(
            stage="scout",
            prompt=prompt,
            effort="low",
            run_id=run_id,
            traces_dir=traces_dir,
            max_tokens=GUIDED_MAX_TOKENS,
        )
    proposals, rationale = parse_guided_response(completion.text)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in proposals[:MAX_GUIDED_QUERIES]:
        keywords = item.get("keywords", "")
        reason = validate_guided_keywords(keywords)
        cls = item.get("class")
        if reason is None and cls is not None and cls not in SECURITY_KEYWORDS:
            reason = f"unknown security class {cls!r}"
        record = {
            "keywords": keywords.strip() if isinstance(keywords, str) else "",
            "class": cls,
            "why": item.get("why", ""),
        }
        if reason is None:
            accepted.append(record)
        else:
            rejected.append({**record, "reason": reason})
    for item in proposals[MAX_GUIDED_QUERIES:]:
        keywords = item.get("keywords", "")
        rejected.append(
            {
                "keywords": keywords.strip() if isinstance(keywords, str) else "",
                "class": item.get("class"),
                "why": item.get("why", ""),
                "reason": f"cut: more than {MAX_GUIDED_QUERIES} proposals",
            }
        )
    return accepted, rejected, rationale, completion


@dataclass
class ScoutOutcome:
    """Full result of discovery, including the guided proposal record."""

    candidates: list[Candidate]
    rate_limited: bool
    guided: bool = False
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""
    github_authenticated: bool = False


def discover_full(
    client: ScoutClient,
    limit: int = 30,
    languages: tuple[str, ...] = ("python", "node"),
    *,
    guided: bool = False,
    provider: Any | None = None,
    proof_root: Path | str | None = None,
    run_id: str | None = None,
    traces_dir: Path | None = None,
) -> ScoutOutcome:
    """Discover up to ``limit`` candidates, with the guided proposal first.

    Every base query is issued once per requested ``language:`` qualifier,
    so ``--languages`` restricts the GitHub queries themselves instead of
    only filtering afterwards. With ``guided=True``, the model-proposed
    queries run before the fixed security queries and the generic ``bug``
    fill query, within the same ``limit`` and the same rate-limit early
    stop. A failed proposal falls back to the fixed queries. A
    ``scout_queries_proposed`` event records the model, accepted/rejected
    queries with reasons, the rationale, tokens and cost (never the token
    value). Each candidate keeps the query that found it (``matched_query``).

    Returns a :class:`ScoutOutcome`; also reports ``github_authenticated``
    (True when ``GITHUB_TOKEN`` is set) without ever exposing the value.
    """
    github_authenticated = bool(os.environ.get("GITHUB_TOKEN", "").strip())
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    rationale = ""
    if guided:
        history = read_proof_history(
            proof_root if proof_root is not None else DEFAULT_PROOF_ROOT
        )
        model_name = ""
        provider_name = ""
        in_tokens = out_tokens = cached_tokens = 0
        cost: float | None = None
        price_known = True
        try:
            accepted, rejected, rationale, completion = propose_guided_queries(
                provider, history, run_id=run_id, traces_dir=traces_dir
            )
            model_name = completion.model
            provider_name = completion.provider
            in_tokens = completion.in_tokens
            out_tokens = completion.out_tokens
            cached_tokens = completion.cached_tokens
            cost = completion.cost_usd
            price_known = completion.price_known
        except Exception as exc:  # noqa: BLE001 — guided is best-effort; fixed queries fill
            rationale = f"guided proposal failed ({exc}); using fixed queries"
        if run_id is not None:
            trace_log.append(
                run_id,
                trace_log.ScoutQueriesProposed(
                    run_id=run_id,
                    model=model_name,
                    provider=provider_name,
                    accepted=accepted,
                    rejected=rejected,
                    rationale=rationale,
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    cached_tokens=cached_tokens,
                    cost_usd=cost,
                    price_known=price_known,
                ),
                traces_dir,
            )
    ordered: list[tuple[dict[str, Any], str | None, str | None]] = []
    seen: set[str] = set()
    qualifiers = language_qualifiers(languages) or [""]
    query_plan: list[tuple[str | None, str]] = []
    for proposal in accepted:
        for qualifier in qualifiers:
            scoped = f"type:issue state:open {proposal['keywords']} {qualifier}".strip()
            query_plan.append((proposal["class"], scoped))
    for query_class, query in SECURITY_SEARCH_QUERIES + [(None, BUG_FILL_QUERY)]:
        for qualifier in qualifiers:
            scoped = f"{query} {qualifier}".strip()
            query_plan.append((query_class, scoped))
    rate_limited = False
    for query_class, query in query_plan:
        try:
            issues = client.search_issues(query)
        except limits.RateLimited:
            rate_limited = True
            break
        except (httpx.HTTPError, OSError, ValueError):
            continue
        for issue in issues:
            url = str(issue.get("html_url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            ordered.append((issue, query_class, query))
    candidates: list[Candidate] = []
    for issue, query_class, matched_query in ordered:
        if len(candidates) >= limit:
            break
        try:
            candidate = evaluate_issue(
                client, issue, query_class, languages, matched_query=matched_query
            )
        except limits.RateLimited:
            rate_limited = True
            break
        if candidate is not None:
            candidates.append(candidate)
    # Score first; security is only a tie-breaker inside the same band.
    candidates.sort(key=sort_key)
    return ScoutOutcome(
        candidates=candidates[:limit],
        rate_limited=rate_limited,
        guided=guided,
        accepted=accepted,
        rejected=rejected,
        rationale=rationale,
        github_authenticated=github_authenticated,
    )


def discover_limited(
    client: ScoutClient,
    limit: int = 30,
    languages: tuple[str, ...] = ("python", "node"),
) -> tuple[list[Candidate], bool]:
    """Discover up to ``limit`` candidates; also report rate limiting.

    Every base query is issued once per requested ``language:`` qualifier,
    so ``--languages`` restricts the GitHub queries themselves instead of
    only filtering afterwards. Security queries run before the generic
    ``bug`` fill query. The final list is sorted by score (descending);
    security breaks ties inside the same score band only.

    Returns ``(candidates, rate_limited)``: when the GitHub budget runs
    low, discovery stops early and ``rate_limited`` is True so the report
    can say so. Guided discovery (model-proposed queries) lives in
    :func:`discover_full`.
    """
    outcome = discover_full(client, limit=limit, languages=languages)
    return outcome.candidates, outcome.rate_limited


def discover(
    client: ScoutClient,
    limit: int = 30,
    languages: tuple[str, ...] = ("python", "node"),
) -> list[Candidate]:
    """Discover up to ``limit`` candidates (drops the rate-limit flag)."""
    candidates, _ = discover_limited(client, limit=limit, languages=languages)
    return candidates


class GitHubClient:
    """Live read-only GitHub API client with a file cache.

    One instance (one :class:`GitHubThrottle`) is shared by scout and
    workspace so the minimum delays and the stop-early budget apply
    across both. Cache hits return without waiting.
    """

    def __init__(
        self,
        token: str | None = None,
        cache_dir: Path | None = None,
        timeout: float = 30.0,
        throttle: limits.GitHubThrottle | None = None,
    ) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._http = httpx.Client(base_url=GITHUB_API, headers=headers, timeout=timeout)
        self.throttle = throttle if throttle is not None else limits.GitHubThrottle()

    def _cached(self, key: str, fetch: Any) -> Any:
        path = self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        data = fetch()
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
        return data

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        kind: str = "rest",
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        # Cache hits (in _cached) never reach here, so they never wait.
        self.throttle.wait(kind)
        response = self._http.get(path, params=params, headers=extra_headers)
        self.throttle.note_response(response.status_code, response.headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def search_issues(self, query: str) -> list[dict[str, Any]]:
        data = self._cached(
            f"search:{query}",
            lambda: self._get(
                "/search/issues", {"q": query, "per_page": 30}, kind="search"
            ),
        )
        return list((data or {}).get("items", []))

    def get_repo(self, full_name: str) -> dict[str, Any]:
        return self._cached(f"repo:{full_name}", lambda: self._get(f"/repos/{full_name}"))

    def get_text(self, full_name: str, path: str) -> str | None:
        import base64

        def fetch() -> str | None:
            data = self._get(f"/repos/{full_name}/contents/{path}")
            if not data or isinstance(data, list) or data.get("encoding") != "base64":
                return None
            try:
                return base64.b64decode(data["content"]).decode("utf-8", "replace")
            except (ValueError, KeyError):
                return None

        return self._cached(f"text:{full_name}:{path}", fetch)

    def list_dir(self, full_name: str, path: str) -> list[str]:
        def fetch() -> list[str]:
            data = self._get(f"/repos/{full_name}/contents/{path}")
            if isinstance(data, list):
                return [str(entry.get("name") or "") for entry in data]
            return []

        return self._cached(f"dir:{full_name}:{path}", fetch)

    def linked_open_pr(self, full_name: str, number: int) -> bool:
        """True when the issue timeline references an open pull request."""

        def fetch() -> bool:
            try:
                events = self._get(
                    f"/repos/{full_name}/issues/{number}/timeline",
                    {"per_page": 100},
                    extra_headers={
                        "Accept": "application/vnd.github.mockingbird-preview+json"
                    },
                )
            except (httpx.HTTPError, ValueError):
                return False
            if not isinstance(events, list):
                return False
            for event in events:
                if event.get("event") not in ("cross-referenced", "connected"):
                    continue
                source = event.get("source", {}) or {}
                pr = source.get("pull_request") or {}
                is_pr = bool(pr) or "pull" in str(source.get("html_url"))
                if source.get("state") == "open" and is_pr:
                    return True
            return False

        return bool(self._cached(f"linked:{full_name}:{number}", fetch))
