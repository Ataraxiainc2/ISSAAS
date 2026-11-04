"""Regression tests for issue #310.

``AgentState.get_summary()`` reports ``verified_claims`` as an int count,
while the ``--stream`` finalizers in ``vulnclaw/cli/main.py`` (``run`` and
``solve``) consumed it as a list of claim dicts and sliced it with ``[:50]``.
With zero verified claims ``(0 or [])`` silently degraded to ``[]``; with at
least one claim the truthy int hit ``int[:50]`` and raised
``TypeError: 'int' object is not subscriptable`` after the task had already
succeeded, so the ``complete`` event never fired and the CLI exited 1.

These tests drive both ``--stream`` entry points with a genuine
``AgentState.get_summary()`` payload carrying a nonzero count, i.e. the exact
shape production code produces on a successful run.

The sandbox used here forbids the socketpair every asyncio event loop needs,
so ``asyncio.run`` is replaced with a tiny trampoline that drives the
already-pure fake coroutine chain (real CLI code, fake agent/transport) to
completion. The crash site itself -- the ``--stream`` finalizer -- is real
code, not a copy of it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from typer.testing import CliRunner

from vulnclaw.agent.agent_state import AgentState


def _run_without_loop(coro):
    """Drive coroutines that only await other plain coroutines to completion."""
    try:
        while True:
            try:
                coro.send(None)
            except StopIteration as done:
                return done.value
    finally:
        coro.close()


def _successful_summary(claim_count: int = 1) -> dict:
    """Build a real ``get_summary()`` dict with a nonzero claim count."""
    state = AgentState()
    for index in range(claim_count):
        evidence = state.remember_tool_result(
            tool="fetch",
            arguments={"url": f"http://example.com/{index}"},
            output=f"response body flag{{test-{index}}}",
        )
        state.record_verified_claim(f"finding {index} confirmed", [evidence.id])
    state.completed = True
    summary = state.get_summary()
    # Pin the production contract: a count, not a list. If this changes, the
    # --stream finalizers below must be revisited together with this test.
    assert summary["verified_claims"] == claim_count
    assert isinstance(summary["verified_claims"], int)
    return summary


def _json_events(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def _patch_event_loop(monkeypatch):
    monkeypatch.setattr(
        asyncio, "run", lambda coro, *_, **__: _run_without_loop(coro)
    )


def _fake_config(monkeypatch, cli_main):
    from vulnclaw.config.schema import VulnClawConfig

    config = VulnClawConfig()
    config.llm.api_key = "test-key"
    config.session.engine = "solve"
    config.session.show_thinking = False
    monkeypatch.setattr(cli_main, "load_config", lambda: config)
    return config


def test_run_stream_with_verified_claim_count_exits_zero(monkeypatch):
    import vulnclaw.cli.main as cli_main

    config = _fake_config(monkeypatch, cli_main)
    _patch_event_loop(monkeypatch)
    summary = _successful_summary(claim_count=2)

    class FakeAgent:
        def __init__(self):
            self.config = config
            self.session_state = SimpleNamespace(findings=[])
            self.context = SimpleNamespace(
                state=SimpleNamespace(
                    agent_state=SimpleNamespace(get_summary=lambda: summary)
                )
            )

    async def fake_run_task_action(agent, task, **kwargs):
        kwargs["on_event"]("completed", {})
        return []

    async def fake_orchestrated(*, runner, **kwargs):
        await runner(FakeAgent(), config)
        return SimpleNamespace(summary={"findings_count": 0})

    monkeypatch.setattr(cli_main, "run_task_action", fake_run_task_action)
    monkeypatch.setattr(cli_main, "_run_cli_orchestrated_task", fake_orchestrated)

    result = CliRunner().invoke(
        cli_main.app, ["run", "example.com", "--stream", "--engine", "solve"]
    )

    assert result.exit_code == 0, result.exception
    events = _json_events(result.output)
    complete = next(event for event in events if event["type"] == "complete")
    assert isinstance(complete["result"]["findings"], list)


def test_solve_stream_with_verified_claim_count_exits_zero(monkeypatch):
    import vulnclaw.cli.main as cli_main

    config = _fake_config(monkeypatch, cli_main)
    _patch_event_loop(monkeypatch)
    summary = _successful_summary(claim_count=1)

    class FakeAgent:
        def __init__(self):
            self.config = config
            self.context = SimpleNamespace(
                state=SimpleNamespace(
                    agent_state=SimpleNamespace(get_summary=lambda: summary)
                )
            )

        async def solve(self, task_prompt, **kwargs):
            kwargs["on_event"]("completed", {})
            return []

    async def fake_orchestrated(*, runner, **kwargs):
        await runner(FakeAgent(), config)
        return SimpleNamespace(summary={"findings_count": 0})

    monkeypatch.setattr(cli_main, "_run_cli_orchestrated_task", fake_orchestrated)

    result = CliRunner().invoke(
        cli_main.app, ["solve", "example.com", "--goal", "probe", "--stream"]
    )

    assert result.exit_code == 0, result.exception
    events = _json_events(result.output)
    complete = next(event for event in events if event["type"] == "complete")
    assert isinstance(complete["result"]["findings"], list)
