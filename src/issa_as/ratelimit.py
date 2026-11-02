"""Shared rate-limit handling for outbound API calls (AGENTS.md rule 8).

GitHub: every REST/search request goes through :class:`GitHubThrottle`,
which enforces a minimum delay between requests (search >= 2.5 s,
REST >= 0.5 s), honors ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset``
and ``Retry-After``, and stops early (raising :class:`RateLimited`) when
the remaining budget is low instead of retrying in a loop. Cache hits
never touch the throttle, so they never wait.

Meta Model API: :mod:`issa_as.providers` retries 429/5xx with exponential
backoff (at most 3 retries) and never runs requests in parallel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

# Minimum delay between two GitHub requests of the same kind (seconds).
MIN_SEARCH_DELAY_S = 2.5
MIN_REST_DELAY_S = 0.5

# When the reported remaining budget is at or below this, stop early and
# return what we have instead of spending the last calls.
STOP_WHEN_REMAINING_AT_OR_BELOW = 5

# Upper bound for a single courtesy sleep while honoring Retry-After or
# X-RateLimit-Reset before stopping. We sleep (fake clock in tests) and
# then stop; we never retry in a loop.
MAX_HONOR_SLEEP_S = 60.0


class RateLimited(Exception):
    """The remote budget is exhausted; stop and return what we have."""

    def __init__(self, reason: str, retry_after_s: float = 0.0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_s = retry_after_s


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Seconds from a ``Retry-After`` header, or None when absent/unparsable."""
    raw: str | None = None
    for key, value in headers.items():
        if key.lower() == "retry-after":
            raw = str(value)
            break
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def remaining_budget(headers: Mapping[str, str]) -> int | None:
    """Value of ``X-RateLimit-Remaining``, or None when absent/unparsable."""
    for key, value in headers.items():
        if key.lower() == "x-ratelimit-remaining":
            try:
                return int(str(value))
            except ValueError:
                return None
    return None


def reset_wait_s(headers: Mapping[str, str], now: float) -> float | None:
    """Seconds until ``X-RateLimit-Reset`` (unix epoch), or None."""
    for key, value in headers.items():
        if key.lower() == "x-ratelimit-reset":
            try:
                return max(0.0, float(str(value)) - now)
            except ValueError:
                return None
    return None


@dataclass
class GitHubThrottle:
    """Minimum-delay + stop-early policy for one shared GitHub client.

    One instance is shared by scout and workspace so delays apply across
    both. ``time_fn``/``sleep_fn`` are injectable for offline tests
    (fake clock: no real waiting).
    """

    time_fn: Callable[[], float] = field(default_factory=lambda: time.monotonic)
    sleep_fn: Callable[[float], None] = field(default_factory=lambda: time.sleep)
    min_search_s: float = MIN_SEARCH_DELAY_S
    min_rest_s: float = MIN_REST_DELAY_S
    stop_remaining: int = STOP_WHEN_REMAINING_AT_OR_BELOW
    last_search: float = 0.0
    last_rest: float = 0.0
    started: bool = False

    def wait(self, kind: str) -> None:
        """Sleep when the previous request of ``kind`` was too recent.

        ``kind`` is ``"search"`` or ``"rest"``. Call only on cache misses;
        cache hits return without waiting.
        """
        now = self.time_fn()
        if kind == "search":
            last, gap = self.last_search, self.min_search_s
        else:
            last, gap = self.last_rest, self.min_rest_s
        if self.started:
            delay = gap - (now - last)
            if delay > 0:
                self.sleep_fn(delay)
                now = self.time_fn()
        if kind == "search":
            self.last_search = now
        else:
            self.last_rest = now
        self.started = True

    def note_response(self, status: int, headers: Mapping[str, str]) -> None:
        """Inspect response headers; raise :class:`RateLimited` to stop early.

        Stops when the remaining budget is low, or on 429 / rate-limited
        403. A ``Retry-After``/reset wait is first honored with one bounded
        sleep (never a retry loop); the caller then returns what it has.
        """
        remaining = remaining_budget(headers)
        limited = status == 429 or (status == 403 and remaining == 0)
        if limited:
            wait_s = parse_retry_after(headers)
            if wait_s is None:
                wait_s = reset_wait_s(headers, time.time())
            if wait_s is None:
                wait_s = 0.0
            honored = min(wait_s, MAX_HONOR_SLEEP_S)
            if honored > 0:
                self.sleep_fn(honored)
            raise RateLimited(f"github rate limited (http {status})", honored)
        if remaining is not None and remaining <= self.stop_remaining:
            raise RateLimited(f"github budget low: {remaining} remaining")


__all__ = [
    "MAX_HONOR_SLEEP_S",
    "MIN_REST_DELAY_S",
    "MIN_SEARCH_DELAY_S",
    "STOP_WHEN_REMAINING_AT_OR_BELOW",
    "GitHubThrottle",
    "RateLimited",
    "parse_retry_after",
    "remaining_budget",
    "reset_wait_s",
]
