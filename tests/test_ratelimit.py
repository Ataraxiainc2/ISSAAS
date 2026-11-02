"""Offline tests for the GitHub throttle (fake clock: no real waiting)."""

import httpx
import pytest

from issa_as import scout as scout_mod
from issa_as.ratelimit import (
    GitHubThrottle,
    RateLimited,
    parse_retry_after,
    remaining_budget,
    reset_wait_s,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_search_delay_enforced_between_requests() -> None:
    clock = FakeClock()
    throttle = GitHubThrottle(time_fn=clock.time, sleep_fn=clock.sleep)
    throttle.wait("search")
    assert clock.sleeps == []  # first request never waits
    throttle.wait("search")
    assert clock.sleeps == [pytest.approx(2.5)]


def test_rest_delay_is_shorter_and_independent() -> None:
    clock = FakeClock()
    throttle = GitHubThrottle(time_fn=clock.time, sleep_fn=clock.sleep)
    throttle.wait("rest")
    throttle.wait("rest")
    assert clock.sleeps == [pytest.approx(0.5)]
    throttle.wait("search")  # search budget is untouched by REST calls
    assert clock.sleeps == [pytest.approx(0.5)]


def test_no_wait_once_enough_time_passed() -> None:
    clock = FakeClock()
    throttle = GitHubThrottle(time_fn=clock.time, sleep_fn=clock.sleep)
    throttle.wait("search")
    clock.now += 10.0
    throttle.wait("search")
    assert clock.sleeps == []


def test_stops_when_remaining_budget_is_low() -> None:
    throttle = GitHubThrottle()
    with pytest.raises(RateLimited, match="budget low"):
        throttle.note_response(200, {"X-RateLimit-Remaining": "3"})
    with pytest.raises(RateLimited, match="budget low"):
        throttle.note_response(200, {"x-ratelimit-remaining": "0"})


def test_healthy_budget_does_not_stop() -> None:
    throttle = GitHubThrottle()
    throttle.note_response(200, {"X-RateLimit-Remaining": "59"})


def test_rate_limited_response_honors_retry_after() -> None:
    clock = FakeClock()
    throttle = GitHubThrottle(time_fn=clock.time, sleep_fn=clock.sleep)
    with pytest.raises(RateLimited):
        throttle.note_response(429, {"Retry-After": "5", "X-RateLimit-Remaining": "60"})
    assert clock.sleeps == [pytest.approx(5.0)]


def test_rate_limited_response_honors_reset_header() -> None:
    clock = FakeClock()
    throttle = GitHubThrottle(time_fn=clock.time, sleep_fn=clock.sleep)
    import time as _time

    with pytest.raises(RateLimited):
        throttle.note_response(
            403,
            {
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(_time.time() + 4),
            },
        )
    assert len(clock.sleeps) == 1
    assert 0 < clock.sleeps[0] <= 60.0


def test_header_helpers() -> None:
    assert parse_retry_after({"Retry-After": "12"}) == 12.0
    assert parse_retry_after({}) is None
    assert parse_retry_after({"Retry-After": "soon"}) is None
    assert remaining_budget({"X-RateLimit-Remaining": "7"}) == 7
    assert remaining_budget({}) is None
    assert reset_wait_s({"X-RateLimit-Reset": str(2000.0)}, 1990.0) == pytest.approx(10.0)
    assert reset_wait_s({}, 1990.0) is None


def _search_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"items": []},
        headers={"X-RateLimit-Remaining": "59"},
    )


def test_cache_hit_never_waits(tmp_path) -> None:
    clock = FakeClock()
    throttle = GitHubThrottle(time_fn=clock.time, sleep_fn=clock.sleep)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _search_ok(request)

    transport = httpx.MockTransport(handler)
    client = scout_mod.GitHubClient(
        cache_dir=tmp_path / "scout",
        throttle=throttle,
    )
    client._http = httpx.Client(transport=transport, base_url=scout_mod.GITHUB_API)
    client.search_issues("type:issue state:open xss language:python")
    assert len(calls) == 1
    client.search_issues("type:issue state:open xss language:python")
    assert len(calls) == 1  # served from cache: no request, no wait
    assert clock.sleeps == []


def test_second_search_waits_and_low_budget_stops(tmp_path) -> None:
    clock = FakeClock()
    throttle = GitHubThrottle(time_fn=clock.time, sleep_fn=clock.sleep)
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(
                200, json={"items": []}, headers={"X-RateLimit-Remaining": "59"}
            )
        return httpx.Response(
            200, json={"items": []}, headers={"X-RateLimit-Remaining": "2"}
        )

    transport = httpx.MockTransport(handler)
    client = scout_mod.GitHubClient(
        cache_dir=tmp_path / "scout",
        throttle=throttle,
    )
    client._http = httpx.Client(transport=transport, base_url=scout_mod.GITHUB_API)
    client.search_issues("q1 language:python")
    client.search_issues("q2 language:python")
    assert clock.sleeps == [pytest.approx(2.5)]
    with pytest.raises(RateLimited):
        client.search_issues("q3 language:python")
