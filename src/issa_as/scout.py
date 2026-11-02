"""Security-first GitHub issue discovery (B5, B7, B8).

Read-only use of the GitHub REST/search API (``GITHUB_TOKEN`` optional,
higher rate limit when set). Results are cached under ``.cache/scout/``.

Pipeline: query open issues for vulnerability classes first (path
traversal / zip slip, command or SQL injection, XSS, open redirect, XXE,
ReDoS, unsafe deserialization / pickle / yaml.load, SSRF, auth bypass,
secret leakage), then ordinary ``bug``-labelled issues to fill the pool.
Each candidate is tagged with ``security_class`` (or ``null``), passes the
reputation filter, the maintainer-respect filter, and ecosystem detection
(``python`` / ``node`` / ``none``). No model calls here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from issa_as import signals as signal_mod

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

BOT_LOGINS = {"dependabot", "dependabot-bot", "mend", "renovate", "renovate-bot", "snyk-bot"}
BOT_TITLE_RES = [
    re.compile(r"\b\d+\s+vulnerabilit", re.IGNORECASE),
    re.compile(r"^bump\s+\S+\s+from\s+", re.IGNORECASE),
    re.compile(r"^\[security\]", re.IGNORECASE),
]

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


class Candidate(BaseModel):
    """One scouted issue that survived every filter."""

    issue_url: str = ""
    repo: str = ""
    number: int = 0
    title: str = ""
    labels: list[str] = Field(default_factory=list)
    security_class: str | None = None
    runner: str = "none"
    stars: int = 0
    has_snippet: bool = False
    signals: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0


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
) -> Candidate | None:
    """Apply every filter to one raw issue; return a Candidate or None."""
    if "pull_request" in issue:
        return None
    full_name = _repo_full_name(issue)
    if not full_name or "/" not in full_name:
        return None
    if is_bot_alert(issue):
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
    if security_class is None:
        security_class = query_class
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
        runner=runner,
        stars=int(repo.get("stargazers_count") or 0),
        has_snippet=signal_mod.has_code_snippet(body if isinstance(body, str) else None) > 0,
        signals=scored.values,
        score=scored.total,
    )


def discover(
    client: ScoutClient,
    limit: int = 30,
    languages: tuple[str, ...] = ("python", "node"),
) -> list[Candidate]:
    """Discover up to ``limit`` candidates, security issues first.

    Security queries run before the generic ``bug`` fill query; the final
    list is stable-sorted so tagged candidates always come first.
    """
    ordered: list[tuple[dict[str, Any], str | None]] = []
    seen: set[str] = set()
    queries: list[tuple[str | None, str]] = [
        (cls, query) for cls, query in SECURITY_SEARCH_QUERIES
    ] + [(None, BUG_FILL_QUERY)]
    for query_class, query in queries:
        try:
            issues = client.search_issues(query)
        except (httpx.HTTPError, OSError, ValueError):
            continue
        for issue in issues:
            url = str(issue.get("html_url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            ordered.append((issue, query_class))
    candidates: list[Candidate] = []
    for issue, query_class in ordered:
        if len(candidates) >= limit:
            break
        candidate = evaluate_issue(client, issue, query_class, languages)
        if candidate is not None:
            candidates.append(candidate)
    # Stable: security-tagged candidates keep discovery order, first.
    candidates.sort(key=lambda c: c.security_class is None)
    return candidates[:limit]


class GitHubClient:
    """Live read-only GitHub API client with a file cache."""

    def __init__(
        self,
        token: str | None = None,
        cache_dir: Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._http = httpx.Client(base_url=GITHUB_API, headers=headers, timeout=timeout)

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

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._http.get(path, params=params)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def search_issues(self, query: str) -> list[dict[str, Any]]:
        data = self._cached(
            f"search:{query}",
            lambda: self._get("/search/issues", {"q": query, "per_page": 30}),
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
                response = self._http.get(
                    f"/repos/{full_name}/issues/{number}/timeline",
                    params={"per_page": 100},
                    headers={"Accept": "application/vnd.github.mockingbird-preview+json"},
                )
                if response.status_code != 200:
                    return False
                for event in response.json():
                    if event.get("event") not in ("cross-referenced", "connected"):
                        continue
                    source = event.get("source", {}) or {}
                    pr = source.get("pull_request") or {}
                    is_pr = bool(pr) or "pull" in str(source.get("html_url"))
                    if source.get("state") == "open" and is_pr:
                        return True
                return False
            except (httpx.HTTPError, ValueError):
                return False

        return bool(self._cached(f"linked:{full_name}:{number}", fetch))
